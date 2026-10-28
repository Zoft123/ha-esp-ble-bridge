#!/usr/bin/env python3
"""
Small Home Assistant Core REST client (through the Supervisor proxy).

The ESP32 controllers expose the lights to Home Assistant through the ESPHome integration, so the
add-on's dashboard, scenes and effects drive those light entities instead of keeping a second,
disconnected copy of every light.
"""
import json
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

CORE_URL = 'http://supervisor/core/api'


def slugify(text: str) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(text).lower())
    return text.strip('_')


class HomeAssistantAPI:
    def __init__(self):
        self.token = os.getenv('SUPERVISOR_TOKEN', '')
        self.session = requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
        self._states: List[Dict] = []
        self._by_id: Dict[str, Dict] = {}
        self._by_friendly: Dict[str, str] = {}
        self._states_time = 0.0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.token)

    def get(self, path: str, timeout: float = 5):
        r = self.session.get(f'{CORE_URL}{path}', timeout=timeout)
        r.raise_for_status()
        return r.json()

    def call_service(self, domain: str, service: str, data: Dict, timeout: float = 10) -> bool:
        if not self.available:
            logger.warning("No Supervisor token; cannot call Home Assistant services")
            return False
        try:
            r = self.session.post(f'{CORE_URL}/services/{domain}/{service}', json=data, timeout=timeout)
            if r.status_code >= 400:
                logger.warning(f"{domain}.{service} failed: HTTP {r.status_code} {r.text[:200]}")
                return False
            return True
        except requests.RequestException as e:
            logger.warning(f"{domain}.{service} failed: {e}")
            return False

    def states(self, max_age: float = 5.0) -> List[Dict]:
        """All entity states, cached for `max_age` seconds."""
        if not self.available:
            return []
        with self._lock:
            if time.monotonic() - self._states_time > max_age:
                try:
                    self._states = self.get('/states')
                    self._by_id = {st['entity_id']: st for st in self._states if 'entity_id' in st}
                    self._by_friendly = {str(st.get('attributes', {}).get('friendly_name', '')).lower(): st['entity_id']
                                         for st in self._states if st.get('entity_id', '').startswith('light.')}
                    self._states_time = time.monotonic()
                except Exception as e:
                    logger.debug(f"Could not fetch states: {e}")
            return self._states

    def config(self) -> Optional[Dict]:
        try:
            return self.get('/config')
        except Exception as e:
            logger.debug(f"Could not fetch Home Assistant config: {e}")
            return None

    # --- entity lookup -----------------------------------------------------

    def find_light_entity(self, light_name: str, controller_names: List[str]) -> Optional[str]:
        """Find the ESPHome light entity named `light_name` on one of the controllers.

        ESPHome entity IDs are light.<device name>_<light name> (slugified); fall back to the friendly
        name if the user renamed the entity ID in Home Assistant.
        """
        self.states()
        light_slug = slugify(light_name)
        for controller in controller_names:
            candidate = f"light.{slugify(controller)}_{light_slug}"
            if candidate in self._by_id:
                return candidate
        for controller in controller_names:
            entity = self._by_friendly.get(f"{controller} {light_name}".lower())
            if entity:
                return entity
        return None

    def state_of(self, entity_id: str) -> Optional[Dict]:
        self.states()
        return self._by_id.get(entity_id)

    def device_entity_ids(self, device_name: str) -> List[str]:
        """Entity IDs of the ESPHome device named `device_name`, whatever Home Assistant named the entities
        (it adds the area, e.g. "network_closet_", and users rename them)."""
        template = ("{{ integration_entities('esphome') | select('is_device_attr', 'name', %s) | list | tojson }}"
                    % json.dumps(device_name))
        try:
            r = self.session.post(f'{CORE_URL}/template', json={'template': template}, timeout=5)
            r.raise_for_status()
            return list(json.loads(r.text))
        except Exception as e:
            logger.debug(f"Device lookup for {device_name} failed: {e}")
            return []

    def controller_entities(self, controller_name: str) -> List[Dict]:
        """States of a controller's entities: by its ESPHome device, else by the default entity ID prefix."""
        states = self.states()
        ids = set(self.device_entity_ids(controller_name)) if self.available else set()
        if ids:
            return [s for s in states if s.get('entity_id') in ids]
        prefix = slugify(controller_name) + '_'
        return [s for s in states if s.get('entity_id', '').split('.', 1)[-1].startswith(prefix)]
