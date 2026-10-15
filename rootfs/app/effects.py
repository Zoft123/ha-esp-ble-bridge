#!/usr/bin/env python3
"""
Effects and scenes for BRMesh lights.

Every light update is a BLE advertisement sent by the ESP32, roughly 20 per second in total, shared by
all lights. Effects therefore pace themselves by the number of lights they drive instead of firing
updates every 50-100 ms (which only filled the controller's queue), and use one service call for all
lights whenever they share a color.
"""
import colorsys
import logging
import math
import random
import threading
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


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


def _rgb(h: float, s: float = 1.0, v: float = 1.0) -> Tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


class BRMeshEffects:
    def __init__(self, bridge):
        self.bridge = bridge
        self.running_effects: Dict[str, threading.Event] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start_effect(self, light_ids: List[int], effect_name: str, **params) -> str:
        if effect_name not in EFFECTS:
            raise ValueError(f"Unknown effect: {effect_name}")
        light_ids = [int(i) for i in light_ids if int(i) in self.bridge.lights] or sorted(self.bridge.lights)
        if not light_ids:
            raise ValueError("No lights configured")

        effect_id = f"{effect_name}_{'-'.join(map(str, light_ids))}"
        # A light can only run one effect at a time.
        with self._lock:
            for other_id in list(self.running_effects):
                other_lights = {int(x) for x in other_id.split('_')[-1].split('-')}
                if other_lights & set(light_ids):
                    self._stop_locked(other_id)
            stop = threading.Event()
            self.running_effects[effect_id] = stop
            thread = threading.Thread(target=self._run, args=(effect_name, light_ids, stop, params),
                                      name=f"effect-{effect_id}", daemon=True)
            self._threads[effect_id] = thread
            thread.start()
        logger.info(f"Started effect '{effect_name}' on lights {light_ids}")
        return effect_id

    def stop_effect(self, effect_id: str):
        with self._lock:
            self._stop_locked(effect_id)

    def stop_all(self):
        with self._lock:
            for effect_id in list(self.running_effects):
                self._stop_locked(effect_id)

    def _stop_locked(self, effect_id: str):
        stop = self.running_effects.pop(effect_id, None)
        if stop:
            stop.set()
            self._threads.pop(effect_id, None)
            logger.info(f"Stopped effect {effect_id}")

    def _run(self, name: str, light_ids: List[int], stop: threading.Event, params: Dict):
        try:
            getattr(self, f"_effect_{name}")(light_ids, stop, **params)
        except TypeError as e:
            logger.error(f"Bad parameters for effect {name}: {e}")
        except Exception as e:
            logger.error(f"Effect {name} failed: {e}", exc_info=True)

    def _step(self, n: int, minimum: float) -> float:
        """Shortest step the BLE link can deliver for `n` individually colored lights.

        The controller sends one advertisement per light update (adv_duration + a 5 ms gap) and
        repeats each update; leave room for the first pass plus one repeat.
        """
        per_update = (int(self.bridge.settings.get('adv_duration', 40)) + 5) / 1000.0
        return max(minimum, n * per_update * 2)

    def _each(self, light_ids, colors, brightness=255):
        for light_id, color in zip(light_ids, colors):
            self.bridge.set_lights([light_id], color, brightness, True)

    # --- effects -------------------------------------------------------------

    def _effect_rainbow(self, light_ids, stop, speed: float = 50, brightness: int = 255):
        step = self._step(len(light_ids), 0.5)
        hue = 0.0
        while not stop.is_set():
            self._each(light_ids, [_rgb(hue + i / len(light_ids)) for i in range(len(light_ids))], brightness)
            hue += float(speed) / 1000.0 * step * 10
            stop.wait(step)

    def _effect_color_loop(self, light_ids, stop, interval: float = 3.0, colors=None):
        colors = colors or [(255, 0, 0), (255, 127, 0), (255, 255, 0), (0, 255, 0), (0, 0, 255), (75, 0, 130), (148, 0, 211)]
        i = 0
        while not stop.is_set():
            self.bridge.set_lights(light_ids, tuple(colors[i % len(colors)]), 255, True)
            i += 1
            stop.wait(max(1.0, float(interval)))

    def _effect_twinkle(self, light_ids, stop, color=(255, 255, 255), speed: float = 1.0):
        step = self._step(len(light_ids), 1.0) / max(0.1, float(speed))
        while not stop.is_set():
            bright = set(random.sample(light_ids, max(1, len(light_ids) // 3)))
            dim = [l for l in light_ids if l not in bright]
            self.bridge.set_lights(sorted(bright), tuple(color), random.randint(180, 255), True)
            if dim:
                self.bridge.set_lights(dim, tuple(color), 40, True)
            stop.wait(step)

    def _effect_fire(self, light_ids, stop, intensity: float = 1.0):
        step = self._step(len(light_ids), 0.4)
        while not stop.is_set():
            colors = [(random.randint(200, 255), random.randint(40, max(41, int(140 * float(intensity)))), 0) for _ in light_ids]
            for light_id, color in zip(light_ids, colors):
                self.bridge.set_lights([light_id], color, random.randint(140, 255), True)
            stop.wait(step)

    def _effect_christmas(self, light_ids, stop, interval: float = 2.0):
        palette = [(255, 0, 0), (0, 255, 0), (255, 255, 255)]
        offset = 0
        while not stop.is_set():
            for k, color in enumerate(palette):
                group = [l for i, l in enumerate(light_ids) if (i + offset) % len(palette) == k]
                if group:
                    self.bridge.set_lights(group, color, 255, True)
            offset += 1
            stop.wait(max(1.0, float(interval)))

    def _effect_halloween(self, light_ids, stop, interval: float = 1.5):
        toggle = True
        while not stop.is_set():
            self.bridge.set_lights(light_ids, (255, 100, 0) if toggle else (128, 0, 128), 255, True)
            toggle = not toggle
            stop.wait(max(0.75, float(interval)))

    def _effect_strobe(self, light_ids, stop, color=(255, 255, 255), frequency: float = 2):
        # BLE cannot do a real strobe; cap at 2 flashes per second.
        half = max(0.25, 1.0 / (2 * float(frequency)))
        on = True
        while not stop.is_set():
            self.bridge.set_lights(light_ids, tuple(color), 255, on)
            on = not on
            stop.wait(half)

    def _effect_breathe(self, light_ids, stop, color=(255, 255, 255), speed: float = 1.0):
        phase = 0.0
        while not stop.is_set():
            brightness = int(20 + (math.sin(phase) + 1) * 117)
            self.bridge.set_lights(light_ids, tuple(color), brightness, True)
            phase += 0.5 * float(speed)
            stop.wait(0.5)

    # --- scenes ----------------------------------------------------------------

    def apply_scene(self, scene: Dict):
        logger.info(f"Applying scene: {scene.get('name')}")
        # Group lights with identical settings into one call.
        buckets: Dict[tuple, List[int]] = {}
        for item in scene.get('lights', []):
            light_id = int(item.get('light_id', item.get('id', 0)))
            key = (bool(item.get('state', True)), int(item.get('brightness', 255)), tuple(item.get('rgb', [255, 255, 255])))
            buckets.setdefault(key, []).append(light_id)
        for (state, brightness, rgb), ids in buckets.items():
            self.bridge.set_lights(ids, rgb, brightness, state)
