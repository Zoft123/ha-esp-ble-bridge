#!/usr/bin/env python3
"""
Compile and flash the ESP32 controllers from the add-on.

Builds take minutes, so they run as background jobs; the web UI polls their status and output
instead of holding an HTTP request (and one of the web server's threads) open the whole time.
"""
import collections
import glob
import logging
import os
import subprocess
import threading
import time
from typing import Dict, List, Optional

from esphome_generator import controller_slug
from state_store import ESPHOME_DIR

logger = logging.getLogger(__name__)

COMPILE_TIMEOUT = 1800
UPLOAD_TIMEOUT = 600
MAX_OUTPUT_LINES = 400


class BuildJob:
    def __init__(self, controller: str, action: str):
        self.controller = controller
        self.action = action
        self.started = time.time()
        self.finished: Optional[float] = None
        self.success: Optional[bool] = None
        self.error: Optional[str] = None
        self.output = collections.deque(maxlen=MAX_OUTPUT_LINES)

    @property
    def running(self) -> bool:
        return self.finished is None

    def to_dict(self) -> Dict:
        return {
            'controller': self.controller,
            'action': self.action,
            'running': self.running,
            'success': self.success,
            'error': self.error,
            'started': self.started,
            'finished': self.finished,
            'elapsed': round((self.finished or time.time()) - self.started),
            'output': '\n'.join(self.output),
        }


class ESPHomeBuilder:
    def __init__(self, bridge):
        self.bridge = bridge
        self.build_dir = ESPHOME_DIR
        self.jobs: Dict[str, BuildJob] = {}
        self._lock = threading.Lock()

    def yaml_path(self, controller_name: str) -> str:
        return os.path.join(self.build_dir, f"{controller_slug(controller_name)}.yaml")

    def job(self, controller_name: str) -> Optional[BuildJob]:
        return self.jobs.get(controller_slug(controller_name))

    def start(self, controller_name: str, action: str, port: str = 'OTA') -> BuildJob:
        """Start 'compile', 'upload' or 'compile_upload' in the background."""
        key = controller_slug(controller_name)
        with self._lock:
            current = self.jobs.get(key)
            if current and current.running:
                raise RuntimeError(f"A {current.action} is already running for {controller_name}")
            yaml_file = self.yaml_path(controller_name)
            if not os.path.exists(yaml_file):
                raise FileNotFoundError(f"Config file not found: {yaml_file}. Generate the ESPHome config first.")
            job = BuildJob(controller_name, action)
            self.jobs[key] = job
        threading.Thread(target=self._run_job, args=(job, yaml_file, port), daemon=True,
                         name=f"esphome-{action}-{key}").start()
        return job

    def _run_job(self, job: BuildJob, yaml_file: str, port: str):
        try:
            ok = True
            if job.action in ('compile', 'compile_upload'):
                ok = self._run(job, ['esphome', 'compile', yaml_file], COMPILE_TIMEOUT)
            if ok and job.action in ('upload', 'compile_upload'):
                device = port if port and port != 'auto' else 'OTA'
                ok = self._run(job, ['esphome', 'upload', '--device', device, yaml_file], UPLOAD_TIMEOUT)
            job.success = ok
            if not ok and not job.error:
                job.error = self._summarize_error(job)
        except Exception as e:
            job.success = False
            job.error = str(e)
            logger.error(f"{job.action} for {job.controller} failed: {e}", exc_info=True)
        finally:
            job.finished = time.time()
            logger.info(f"{job.action} for {job.controller} {'succeeded' if job.success else 'failed'} "
                        f"after {round(job.finished - job.started)}s")

    def _run(self, job: BuildJob, cmd: List[str], timeout: int) -> bool:
        env = {
            **os.environ,
            # /config/.platformio in the add-on, shared with earlier builds
            'PLATFORMIO_CORE_DIR': os.path.join(os.path.dirname(self.build_dir), '.platformio'),
            'PIO_INSTALL_DEVPLATFORM_SILENT': 'yes',
        }
        job.output.append(f"$ {' '.join(cmd)}")
        logger.info(f"Running: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, bufsize=1, env=env, cwd=self.build_dir)
        deadline = time.time() + timeout
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        try:
            for line in proc.stdout:
                job.output.append(line.rstrip())
            proc.wait()
        finally:
            timer.cancel()
        if time.time() >= deadline:
            job.error = f"Timed out after {timeout // 60} minutes"
            return False
        return proc.returncode == 0

    @staticmethod
    def _summarize_error(job: BuildJob) -> str:
        lines = [l for l in job.output if 'error' in l.lower() or 'failed' in l.lower()]
        for line in job.output:
            if "Secret '" in line or 'secrets.yaml' in line:
                return line.strip()
        return (lines[-1] if lines else (job.output[-1] if job.output else 'Unknown error')).strip()

    def list_serial_ports(self) -> List[Dict]:
        """USB serial devices on the Home Assistant machine, with readable names where available."""
        ports = {}
        for dev in sorted(glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')):
            ports[os.path.realpath(dev)] = {'path': dev, 'label': dev}
        # /dev/serial/by-id names the adapter, e.g. usb-Silicon_Labs_CP2102_..._0001-if00-port0
        for link in sorted(glob.glob('/dev/serial/by-id/*')):
            real = os.path.realpath(link)
            name = os.path.basename(link).replace('usb-', '').replace('_', ' ')
            ports[real] = {'path': link, 'label': f"{name} ({os.path.basename(real)})"}
        return list(ports.values())

    def find_firmware_binary(self, controller_name: str, factory: bool = True) -> Optional[str]:
        name = controller_slug(controller_name)
        base = f"{self.build_dir}/.esphome/build/{name}/.pioenvs/{name}"
        candidates = ([f"{base}/firmware.factory.bin"] if factory else []) + [f"{base}/firmware.bin"]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def firmware_up_to_date(self, controller_name: str) -> bool:
        """True if a firmware exists that was built after the last change to the YAML or components."""
        binary = self.find_firmware_binary(controller_name)
        if not binary:
            return False
        built = os.path.getmtime(binary)
        sources = [self.yaml_path(controller_name), os.path.join(self.build_dir, 'secrets.yaml')]
        sources += glob.glob(os.path.join(self.build_dir, 'esp_ble_bridge_components', '*', '*'))
        return all(not os.path.exists(src) or os.path.getmtime(src) <= built for src in sources)
