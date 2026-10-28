#pragma once
#include <cstdint>
#include <vector>
#include "esphome/components/light/color_mode.h"
#include "esphome/components/light/light_color_values.h"

namespace esphome {
namespace fastcon {

// Values the brMesh app uses (GlobalBluetooth): white on RGBW lamps is warm = cold = 127, and an
// RGB-only lamp's white is red = green = blue = 85.
static const uint8_t APP_WHITE_PARAM = 127;
static const uint8_t APP_WHITE_RGB_PARAM = 85;

inline uint8_t to8(float v) {
  if (v < 0.0f) v = 0.0f;
  if (v > 1.0f) v = 1.0f;
  return static_cast<uint8_t>(v * 255.0f + 0.5f);
}

// The 6 byte light state the brMesh app sends (BLEFastconHelper.genSingleLightCommand), or {0x00}
// for off:
//   [0x80 | brightness 0..127] [blue] [red] [green] [warm] [cold]
// The brightness travels only in the first byte: the color bytes are the color itself (the
// app's rgbCommand sends the picked RGB unscaled), and for white warm + cold add up to 255 (the
// app's color temperature slider: warm = t * 255, cold = 255 - warm). ESPHome's
// current_values_as_rgbww() multiplies the brightness into every channel, which dimmed lamps twice.
inline std::vector<uint8_t> fastcon_light_data(const light::LightColorValues &v, bool supports_cwww) {
  using light::ColorMode;
  if (!v.is_on())
    return {0x00};

  float bri = v.get_brightness();
  uint8_t bri7 = static_cast<uint8_t>((bri < 0.0f ? 0.0f : bri > 1.0f ? 1.0f : bri) * 127.0f + 0.5f);
  if (bri7 == 0)
    bri7 = 1;
  const uint8_t head = 0x80 | bri7;

  const ColorMode mode = v.get_color_mode();
  const bool has_rgb = mode == ColorMode::RGB || mode == ColorMode::RGB_WHITE ||
                       mode == ColorMode::RGB_COLD_WARM_WHITE || mode == ColorMode::RGB_COLOR_TEMPERATURE;
  const bool has_cwww = mode == ColorMode::COLD_WARM_WHITE || mode == ColorMode::RGB_COLD_WARM_WHITE;

  if (mode == ColorMode::WHITE)
    return {head, 0, 0, 0, APP_WHITE_PARAM, APP_WHITE_PARAM};

  uint8_t r = 0, g = 0, b = 0, warm = 0, cold = 0;
  if (has_rgb) {
    const float cb = v.get_color_brightness();
    r = to8(v.get_red() * cb);
    g = to8(v.get_green() * cb);
    b = to8(v.get_blue() * cb);
  }
  if (has_cwww) {
    const float cw = v.get_cold_white(), ww = v.get_warm_white();
    if (cw + ww > 0.0f) {
      if (supports_cwww) {
        warm = static_cast<uint8_t>(255.0f * ww / (cw + ww) + 0.5f);
        cold = 255 - warm;
      } else if (!has_rgb) {
        r = g = b = APP_WHITE_RGB_PARAM;
      }
    }
  }
  if (r == 0 && g == 0 && b == 0 && warm == 0 && cold == 0) {
    // No color known yet (first turn on): warm white on lamps with white LEDs, RGB white otherwise.
    if (supports_cwww)
      warm = 255;
    else
      r = g = b = APP_WHITE_RGB_PARAM;
  }
  return {head, b, r, g, warm, cold};
}

}  // namespace fastcon
}  // namespace esphome
