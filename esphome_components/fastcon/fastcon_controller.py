import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import esp32_ble, text_sensor
from esphome.const import CONF_ID, ENTITY_CATEGORY_DIAGNOSTIC
from esphome.core import HexInt

# The controller only needs the BLE stack, not a GATT server or the always-on scanner of
# esp32_ble_tracker: both compete with the advertisements that carry the light commands. It
# scans by itself, only while pairing.
AUTO_LOAD = ["esp32_ble", "text_sensor"]

CONF_MESH_KEY = "mesh_key"
CONF_ADV_INTERVAL_MIN = "adv_interval_min"
CONF_ADV_INTERVAL_MAX = "adv_interval_max"
CONF_ADV_DURATION = "adv_duration"
CONF_ADV_GAP = "adv_gap"
CONF_MAX_QUEUE_SIZE = "max_queue_size"
CONF_REPEATS = "repeats"
# Text sensor with the lamps found and paired by start_pairing().
CONF_PAIRING_STATUS = "pairing_status"

DEFAULT_ADV_INTERVAL_MIN = 0x20
DEFAULT_ADV_INTERVAL_MAX = 0x28
DEFAULT_ADV_DURATION = 40
DEFAULT_ADV_GAP = 5
DEFAULT_MAX_QUEUE_SIZE = 100
# Each command is advertised this many times (round-robin with other pending lights).
DEFAULT_REPEATS = 3


def validate_hex_bytes(value):
    if isinstance(value, str):
        value = value.replace(" ", "")
        if len(value) != 8:
            raise cv.Invalid("Mesh key must be exactly 4 bytes (8 hex characters)")

        try:
            return HexInt(int(value, 16))
        except ValueError as err:
            raise cv.Invalid(f"Invalid hex value: {err}")
    raise cv.Invalid("Mesh key must be a string")


fastcon_ns = cg.esphome_ns.namespace("fastcon")
FastconController = fastcon_ns.class_("FastconController", cg.Component)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.Optional(CONF_ID, default="fastcon_controller"): cv.declare_id(
            FastconController
        ),
        cv.Required(CONF_MESH_KEY): validate_hex_bytes,
        cv.Optional(
            CONF_ADV_INTERVAL_MIN, default=DEFAULT_ADV_INTERVAL_MIN
        ): cv.uint16_t,
        cv.Optional(
            CONF_ADV_INTERVAL_MAX, default=DEFAULT_ADV_INTERVAL_MAX
        ): cv.uint16_t,
        cv.Optional(CONF_ADV_DURATION, default=DEFAULT_ADV_DURATION): cv.uint16_t,
        cv.Optional(CONF_ADV_GAP, default=DEFAULT_ADV_GAP): cv.uint16_t,
        cv.Optional(
            CONF_MAX_QUEUE_SIZE, default=DEFAULT_MAX_QUEUE_SIZE
        ): cv.positive_int,
        cv.Optional(CONF_REPEATS, default=DEFAULT_REPEATS): cv.int_range(min=1, max=20),
        cv.GenerateID(esp32_ble.CONF_BLE_ID): cv.use_id(esp32_ble.ESP32BLE),
        cv.Optional(CONF_PAIRING_STATUS): text_sensor.text_sensor_schema(
            icon="mdi:link-variant", entity_category=ENTITY_CATEGORY_DIAGNOSTIC
        ),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    if CONF_MESH_KEY in config:
        mesh_key = config[CONF_MESH_KEY]
        key_bytes = [(mesh_key >> (i * 8)) & 0xFF for i in range(3, -1, -1)]
        cg.add(var.set_mesh_key(key_bytes))

    if config[CONF_ADV_INTERVAL_MAX] < config[CONF_ADV_INTERVAL_MIN]:
        raise cv.Invalid(
            f"adv_interval_max ({config[CONF_ADV_INTERVAL_MAX]}) must be >= "
            f"adv_interval_min ({config[CONF_ADV_INTERVAL_MIN]})"
        )

    cg.add(var.set_adv_interval_min(config[CONF_ADV_INTERVAL_MIN]))
    cg.add(var.set_adv_interval_max(config[CONF_ADV_INTERVAL_MAX]))
    cg.add(var.set_adv_duration(config[CONF_ADV_DURATION]))
    cg.add(var.set_adv_gap(config[CONF_ADV_GAP]))
    cg.add(var.set_max_queue_size(config[CONF_MAX_QUEUE_SIZE]))
    cg.add(var.set_repeats(config[CONF_REPEATS]))

    if not hasattr(esp32_ble, "register_gap_scan_event_handler"):
        raise cv.Invalid("These components need ESPHome 2026.10 or newer (esp32_ble callback API)")
    ble = await cg.get_variable(config[esp32_ble.CONF_BLE_ID])
    esp32_ble.register_gap_event_handler(ble, var)
    esp32_ble.register_gap_scan_event_handler(ble, var)

    if CONF_PAIRING_STATUS in config:
        sensor = await text_sensor.new_text_sensor(config[CONF_PAIRING_STATUS])
        cg.add(var.set_pairing_sensor(sensor))
