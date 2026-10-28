"""Effects and scenes for BRMesh lights, driven through the lights' Home Assistant entities.

Every light update is a BLE advertisement sent by the ESP32, roughly 20 per second in total, shared by
all lights. Effects therefore pace themselves by the number of lights they drive instead of firing
updates every 50-100 ms (which only filled the controller's queue), and use one service call for all
lights whenever they share a color.

An effect keeps a light only until someone else takes it over: setting the light in the web UI, or
switching it (or "All Lights") off anywhere in Home Assistant, takes it out of the effect. Otherwise
the next step of the effect would switch it straight back on.
"""
import itertools
import threading
import time
from colorsys import hsv_to_rgb
from datetime import datetime
from logging import getLogger
from math import log as ln, sin
from random import randint, sample
from typing import Dict, List, Optional, Tuple

log = getLogger(__name__)


EFFECTS = {
    'rainbow': {'params': ['speed', 'brightness']},
    'color_loop': {'params': ['interval']},
    'twinkle': {'params': ['color', 'speed']},
    'fire': {'params': ['intensity']},
    'christmas': {'params': ['interval']},
    'halloween': {'params': ['interval']},
    'strobe': {'params': ['color', 'frequency']},
    'breathe': {'params': ['color', 'speed']},
}

# How often a running effect checks Home Assistant for lights switched off by someone else.
OVERRIDE_CHECK_INTERVAL = 2.0
# A light that went off within this many seconds of the add-on's own turn_off was switched off by
# the add-on (strobe, a scene), not by the user.
OWN_CHANGE_WINDOW = 5.0


# One-tap colors in the web UI. The white presets use the lamps' white LEDs (color temperature on
# CW/WW lamps, the white channel on RGBW lamps) and only fall back to mixing RGB on RGB-only lamps.
PRESETS = {
    'cool_white': {'label': 'Cool White', 'kelvin': 6500},
    'daylight': {'label': 'Daylight', 'kelvin': 5000},
    'neutral_white': {'label': 'Neutral White', 'kelvin': 4000},
    'warm_white': {'label': 'Warm White', 'kelvin': 2700},
    'candle': {'label': 'Candle', 'kelvin': 2000},
    'red': {'label': 'Red', 'rgb': (255, 0, 0)},
    'orange': {'label': 'Orange', 'rgb': (255, 100, 0)},
    'yellow': {'label': 'Yellow', 'rgb': (255, 200, 0)},
    'green': {'label': 'Green', 'rgb': (0, 255, 0)},
    'cyan': {'label': 'Cyan', 'rgb': (0, 255, 255)},
    'blue': {'label': 'Blue', 'rgb': (0, 0, 255)},
    'purple': {'label': 'Purple', 'rgb': (140, 0, 255)},
    'pink': {'label': 'Pink', 'rgb': (255, 0, 140)},
}


def kelvin_to_rgb(kelvin: float) -> Tuple[int, int, int]:
    """Approximate RGB of a white of `kelvin` (Tanner Helland's fit), for lamps without white LEDs."""
    t = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * ln(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * ln(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255.0
    return tuple(int(max(0.0, min(255.0, c)) + 0.5) for c in (r, g, b))


def _rgb(h: float, s: float = 1.0, v: float = 1.0) -> Tuple[int, int, int]:
    r, g, b = hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _timestamp(value) -> Optional[float]:
    """Seconds since the epoch of a Home Assistant ISO timestamp (last_changed)."""
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError):
        return None


def _color(value, default=(255, 255, 255)) -> Tuple[int, int, int]:
    """An RGB tuple from a list/tuple or an {r, g, b} object (what the web UI's color inputs give)."""
    if isinstance(value, dict):
        value = (value.get('r', 0), value.get('g', 0), value.get('b', 0))
    try:
        r, g, b = (max(0, min(255, int(c))) for c in value)
        return r, g, b
    except (TypeError, ValueError):
        return default


class EffectRun:
    def __init__(self, effect_id: str, name: str, light_ids: List[int], params: Dict):
        self.id = effect_id
        self.name = name
        # Replaced (never mutated) when lights are released, so effect steps can read it without a lock.
        self.light_ids = list(light_ids)
        self.params = params
        self.stop = threading.Event()
        self.started = time.time()
        self.next_check = self.started + OVERRIDE_CHECK_INTERVAL

    def to_dict(self) -> Dict:
        return {'id': self.id, 'effect': self.name, 'light_ids': list(self.light_ids), 'started': self.started}


class BRMeshEffects:
    """Runs effects in background threads, one per effect, and applies scenes."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.runs: Dict[str, EffectRun] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # --- running effects -----------------------------------------------------

    def start_effect(self, light_ids: Optional[List[int]], effect_name: str, **params) -> str:
        if effect_name not in EFFECTS:
            raise ValueError(f"Unknown effect: {effect_name}")
        light_ids = [int(i) for i in (light_ids or []) if int(i) in self.bridge.lights] or sorted(self.bridge.lights)
        if not light_ids:
            raise ValueError("No lights configured")
        params = {k: v for k, v in params.items() if k in EFFECTS[effect_name]['params']}

        # A light can only run one effect at a time.
        self.release_lights(light_ids)
        run = EffectRun(f"{effect_name}-{next(self._ids)}", effect_name, light_ids, params)
        with self._lock:
            self.runs[run.id] = run
        threading.Thread(target=self._run, args=(run,), name=f"effect-{run.id}", daemon=True).start()
        log.info(f"Started effect '{effect_name}' ({run.id}) on lights {light_ids}")
        return run.id

    def running(self) -> List[Dict]:
        with self._lock:
            return [r.to_dict() for r in self.runs.values()]

    def stop_effect(self, effect_id: str) -> bool:
        with self._lock:
            run = self.runs.pop(effect_id, None)
        if run:
            run.stop.set()
            log.info(f"Effect {effect_id} stopped")
        return run is not None

    def stop_all(self) -> int:
        with self._lock:
            runs, self.runs = list(self.runs.values()), {}
        for run in runs:
            run.stop.set()
        if runs:
            log.info(f"Stopped {len(runs)} effect(s)")
        return len(runs)

    def release_lights(self, light_ids) -> List[str]:
        """Take lights out of the running effects. Effects left without lights stop.

        Returns the IDs of the effects that stopped.
        """
        released = {int(i) for i in light_ids}
        stopped = []
        with self._lock:
            for run in list(self.runs.values()):
                if not released & set(run.light_ids):
                    continue
                run.light_ids = [l for l in run.light_ids if l not in released]
                if not run.light_ids:
                    run.stop.set()
                    del self.runs[run.id]
                    stopped.append(run.id)
        for effect_id in stopped:
            log.info(f"Stopped effect {effect_id}: all of its lights were taken over")
        return stopped

    def _run(self, run: EffectRun):
        try:
            getattr(self, f"_effect_{run.name}")(run, **run.params)
        except TypeError as e:
            log.error(f"Bad parameters for effect {run.name}: {e}")
        except Exception as e:
            log.error(f"Effect {run.name} failed: {e}", exc_info=True)
        finally:
            with self._lock:
                if self.runs.get(run.id) is run:
                    del self.runs[run.id]

    # --- taking lights back from an effect -------------------------------------

    def _switched_off_elsewhere(self, entity: Optional[str], since: float) -> bool:
        """True if `entity` went off after `since` and the add-on did not switch it off itself."""
        if not entity:
            return False
        state = self.bridge.ha.state_of(entity)
        if not state or state.get('state') != 'off':
            return False
        changed = _timestamp(state.get('last_changed'))
        if changed is None or changed <= since + 1.0:
            return False
        own = self.bridge.own_off_time(entity)
        return own is None or not (own - 1.0 <= changed <= own + OWN_CHANGE_WINDOW)

    def _check_overrides(self, run: EffectRun):
        now = time.time()
        if now < run.next_check:
            return
        run.next_check = now + OVERRIDE_CHECK_INTERVAL
        if not self.bridge.ha.available:
            return
        self.bridge.ha.states(max_age=OVERRIDE_CHECK_INTERVAL / 2)
        if self._switched_off_elsewhere(self.bridge.all_lights_entity(), run.started):
            released = list(run.light_ids)
        else:
            released = [l for l in run.light_ids
                        if self._switched_off_elsewhere(self.bridge.light_entity(l), run.started)]
        if released:
            log.info(f"Lights {released} were switched off in Home Assistant; taking them out of {run.id}")
            self.release_lights(released)

    # --- effect helpers ----------------------------------------------------------

    def _set(self, run: EffectRun, light_ids, rgb, brightness=255, on=True):
        """Set lights that still belong to the effect."""
        current = set(run.light_ids)
        ids = [l for l in light_ids if l in current]
        if ids and not run.stop.is_set():
            self.bridge.set_lights(ids, tuple(rgb), brightness, on)

    def _wait(self, run: EffectRun, seconds: float) -> bool:
        """Pause between steps. False once the effect should end."""
        if run.stop.wait(seconds):
            return False
        self._check_overrides(run)
        return not run.stop.is_set() and bool(run.light_ids)

    def _step(self, n: int, minimum: float) -> float:
        """Shortest step the BLE link can deliver for `n` individually colored lights.

        The controller sends one advertisement per light update (adv_duration + a 5 ms gap) and
        repeats each update; leave room for the first pass plus one repeat.
        """
        per_update = (int(self.bridge.settings.get('adv_duration', 40)) + 5) / 1000.0
        return max(minimum, n * per_update * 2)

    # --- effects -------------------------------------------------------------

    def _effect_rainbow(self, run, speed: float = 50, brightness: int = 255):
        hue = 0.0
        while True:
            ids = run.light_ids
            step = self._step(len(ids), 0.5)
            for i, light_id in enumerate(ids):
                self._set(run, [light_id], _rgb(hue + i / len(ids)), brightness)
            hue += float(speed) / 1000.0 * step * 10
            if not self._wait(run, step):
                return

    def _effect_color_loop(self, run, interval: float = 3.0, colors=None):
        colors = colors or [(255, 0, 0), (255, 127, 0), (255, 255, 0), (0, 255, 0), (0, 0, 255), (75, 0, 130), (148, 0, 211)]
        i = 0
        while True:
            self._set(run, run.light_ids, _color(colors[i % len(colors)]), 255)
            i += 1
            if not self._wait(run, max(1.0, float(interval))):
                return

    def _effect_twinkle(self, run, color=(255, 255, 255), speed: float = 1.0):
        color = _color(color)
        while True:
            ids = run.light_ids
            bright = set(sample(ids, max(1, len(ids) // 3)))
            dim = [l for l in ids if l not in bright]
            self._set(run, sorted(bright), color, randint(180, 255))
            if dim:
                self._set(run, dim, color, 40)
            if not self._wait(run, self._step(len(ids), 1.0) / max(0.1, float(speed))):
                return

    def _effect_fire(self, run, intensity: float = 1.0):
        while True:
            ids = run.light_ids
            for light_id in ids:
                color = (randint(200, 255), randint(40, max(41, int(140 * float(intensity)))), 0)
                self._set(run, [light_id], color, randint(140, 255))
            if not self._wait(run, self._step(len(ids), 0.4)):
                return

    def _palette(self, run, palette, interval: float, minimum: float):
        """Give neighbouring lights consecutive palette colors and shift them by one every step."""
        shift = 0
        while True:
            ids = run.light_ids
            by_color: Dict[tuple, List[int]] = {}
            for position, light_id in enumerate(ids):
                by_color.setdefault(palette[(position + shift) % len(palette)], []).append(light_id)
            for color, group in by_color.items():
                self._set(run, group, color, 255)  # one call per color
            shift += 1
            if not self._wait(run, max(minimum, float(interval))):
                return

    def _effect_christmas(self, run, interval: float = 2.0):
        self._palette(run, [(255, 0, 0), (0, 200, 40), (255, 190, 120)], interval, 1.0)

    def _effect_halloween(self, run, interval: float = 1.5):
        self._palette(run, [(255, 110, 0), (120, 0, 200)], interval, 0.75)

    def _effect_strobe(self, run, color=(255, 255, 255), frequency: float = 2):
        # BLE cannot do a real strobe; cap at 2 flashes per second.
        color = _color(color)
        half = max(0.25, 1.0 / (2 * float(frequency)))
        on = True
        while True:
            self._set(run, run.light_ids, color, 255, on)
            on = not on
            if not self._wait(run, half):
                return

    def _effect_breathe(self, run, color=(255, 255, 255), speed: float = 1.0):
        color = _color(color)
        phase = 0.0
        while True:
            brightness = int(20 + (sin(phase) + 1) * 117)
            self._set(run, run.light_ids, color, brightness)
            phase += 0.5 * float(speed)
            if not self._wait(run, 0.5):
                return

    # --- scenes ----------------------------------------------------------------

    def apply_scene(self, scene: Dict):
        log.info(f"Applying scene: {scene.get('name')}")
        if scene.get('effect'):
            self.start_effect(scene.get('light_ids'), scene['effect'], **(scene.get('params') or {}))
            return
        items = [i for i in scene.get('lights', []) if isinstance(i, dict)]
        self.release_lights([int(i.get('light_id', i.get('id', 0))) for i in items])
        # Group lights with identical settings into one call.
        buckets: Dict[tuple, List[int]] = {}
        for item in items:
            light_id = int(item.get('light_id', item.get('id', 0)))
            kelvin = int(item['kelvin']) if item.get('kelvin') else None
            key = (bool(item.get('state', True)), int(item.get('brightness', 255)),
                   None if kelvin else _color(item.get('rgb')), kelvin)
            buckets.setdefault(key, []).append(light_id)
        for (state, brightness, rgb, kelvin), ids in buckets.items():
            self.bridge.set_lights(ids, rgb, brightness, state, kelvin=kelvin)
