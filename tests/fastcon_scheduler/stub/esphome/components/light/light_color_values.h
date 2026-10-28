#pragma once
#include "color_mode.h"
namespace esphome { namespace light {
// Host stub of ESPHome's LightColorValues: raw (unscaled) channel values.
struct LightColorValues {
  ColorMode mode{ColorMode::RGB};
  bool on{true};
  float brightness{1}, color_brightness{1}, red{1}, green{1}, blue{1}, cold{0}, warm{0};
  bool is_on() const { return on; }
  ColorMode get_color_mode() const { return mode; }
  float get_brightness() const { return brightness; }
  float get_color_brightness() const { return color_brightness; }
  float get_red() const { return red; }
  float get_green() const { return green; }
  float get_blue() const { return blue; }
  float get_cold_white() const { return cold; }
  float get_warm_white() const { return warm; }
};
}}
