
#include <algorithm>
#include "esphome/core/log.h"
#include "esphome/components/light/light_state.h"
#include "fastcon_controller.h"
#include "fastcon_light.h"

#ifndef FASTCON_VERSION
#define FASTCON_VERSION "0.3.3-dev"
#endif

namespace esphome {
namespace fastcon {

static const char *const TAG = "fastcon.light";

static const uint8_t KIND_CCT = 1;
static const uint8_t KIND_DIMMABLE = 2;

light::LightTraits FastconLight::get_traits() {
  light::LightTraits t;
  if (this->kind_ == KIND_DIMMABLE) {
    t.set_supported_color_modes({light::ColorMode::BRIGHTNESS});
    return t;
  }
  if (this->kind_ == KIND_CCT) {
    t.set_supported_color_modes({light::ColorMode::COLD_WARM_WHITE});
    t.set_min_mireds(153.0f);
    t.set_max_mireds(500.0f);
    return t;
  }
  if (this->color_interlock_) {
    if (this->supports_cwww_) {
      t.set_supported_color_modes({light::ColorMode::RGB, light::ColorMode::COLD_WARM_WHITE});
    } else {
      t.set_supported_color_modes({light::ColorMode::RGB, light::ColorMode::WHITE});
    }
  } else {
    if (this->supports_cwww_) {
      t.set_supported_color_modes({light::ColorMode::RGB_COLD_WARM_WHITE});
    } else {
      t.set_supported_color_modes({light::ColorMode::RGB_WHITE});
    }
  }

  if (this->supports_cwww_) {
    t.set_min_mireds(153.0f);
    t.set_max_mireds(500.0f);
  }
  
  return t;
}

void FastconLight::write_state(light::LightState *state) {
  if (this->controller_ == nullptr) {
    ESP_LOGW(TAG, "No controller bound; dropping command");
    return;
  }

  // The first write is the state restored at boot. Sending it would switch every lamp whenever the
  // ESP32 restarts, so skip it (the lamps keep whatever state they are in).
  if (this->first_write_) {
    this->first_write_ = false;
    return;
  }

  std::vector<uint8_t> light_bytes;
  auto &values = state->current_values;

  // Determine if it's a "white-only" command
  bool is_white_only = values.get_color_mode() == light::ColorMode::WHITE;

  if (this->kind_ == KIND_DIMMABLE) {
    // Brightness-only lamps take a single byte: 0x80 | brightness (7 bit), or 0 for off.
    if (!values.is_on()) {
      light_bytes = {0x00};
    } else {
      uint8_t bri = static_cast<uint8_t>(std::min(values.get_brightness() * 127.0f + 0.5f, 127.0f));
      light_bytes = {static_cast<uint8_t>(0x80 | (bri == 0 ? 1 : bri))};
    }
  } else if (is_white_only) {
    ESP_LOGV(TAG, "Sending white-only command for light %u", (unsigned)this->light_id_);
    light_bytes = this->controller_->get_white_light_data(state);
  } else {
    ESP_LOGV(TAG, "Sending RGB/color command for light %u", (unsigned)this->light_id_);
    light_bytes = this->controller_->get_light_data(state);
  }

  // Wrap into inner payload for this light_id
  std::vector<uint8_t> payload = this->controller_->single_control(this->light_id_, light_bytes);

  // Queue it for advertisement
  this->controller_->queueCommand(this->light_id_, payload);

  ESP_LOGV(TAG, "Queued state v%s: light_id=%u, payload_len=%d",
           FASTCON_VERSION, (unsigned)this->light_id_, (int)payload.size());
}

}  // namespace fastcon
}  // namespace esphome
