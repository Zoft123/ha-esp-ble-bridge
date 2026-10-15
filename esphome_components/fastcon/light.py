"""Light platform for Fastcon BLE lights."""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import light
from esphome.const import CONF_COLOR_INTERLOCK, CONF_LIGHT_ID, CONF_OUTPUT_ID
from .fastcon_controller import FastconController

# New config key to toggle RGBCW capability per-entity
CONF_SUPPORTS_CWWW = "supports_cwww"
# Lamp type from the brMesh app: rgbcw, rgbw, rgb (color lamps), cct (cold/warm white) or dimmable.
CONF_LIGHT_TYPE = "light_type"
LIGHT_TYPES = {"rgbcw": 0, "rgbw": 0, "rgb": 0, "cct": 1, "dimmable": 2}

DEPENDENCIES = ["fastcon"]
AUTO_LOAD = ["light"]

CONF_CONTROLLER_ID = "controller_id"

fastcon_ns = cg.esphome_ns.namespace("fastcon")
FastconLight = fastcon_ns.class_("FastconLight", light.LightOutput, cg.Component)

CONFIG_SCHEMA = cv.All(
    light.BRIGHTNESS_ONLY_LIGHT_SCHEMA
    .extend(
        {
            cv.GenerateID(CONF_OUTPUT_ID): cv.declare_id(FastconLight),
            cv.Required(CONF_LIGHT_ID): cv.int_range(min=1, max=255),
            cv.Optional(CONF_CONTROLLER_ID, default="fastcon_controller"): cv.use_id(FastconController),
            cv.Optional(CONF_SUPPORTS_CWWW): cv.boolean,
            cv.Optional(CONF_LIGHT_TYPE, default="rgbw"): cv.one_of(*LIGHT_TYPES, lower=True),
            cv.Optional(CONF_COLOR_INTERLOCK, default=False): cv.boolean,
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)

async def to_code(config):
    var = cg.new_Pvariable(config[CONF_OUTPUT_ID], config[CONF_LIGHT_ID])

    await cg.register_component(var, config)
    await light.register_light(var, config)

    if config.get(CONF_COLOR_INTERLOCK):
        cg.add(var.set_color_interlock(True))

    controller = await cg.get_variable(config[CONF_CONTROLLER_ID])
    cg.add(var.set_controller(controller))

    light_type = config[CONF_LIGHT_TYPE]
    cg.add(var.set_kind(LIGHT_TYPES[light_type]))
    # supports_cwww (older configs) wins over the type default; rgbcw lamps have CW/WW LEDs.
    if config.get(CONF_SUPPORTS_CWWW, light_type == "rgbcw"):
        cg.add(var.set_supports_cwww(True))
