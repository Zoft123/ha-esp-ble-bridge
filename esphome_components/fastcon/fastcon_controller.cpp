#include <algorithm>
#include <cstdint>
#include <cstring>
#include "esphome/core/component_iterator.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "esphome/components/light/color_mode.h"
#include "esphome/components/light/light_state.h"
#include "fastcon_controller.h"
#include "protocol.h"

#ifndef FASTCON_VERSION
#define FASTCON_VERSION "0.3.2-dev"
#endif

namespace esphome {
namespace fastcon {

static const char *const TAG = "fastcon.controller";

void FastconController::queueCommand(uint32_t target, const std::vector<uint8_t> &data, bool required,
                                     uint32_t owner) {
  std::lock_guard<std::mutex> lock(queue_mutex_);
  if (owner == 0)
    owner = target;

  auto it = std::find_if(slots_.begin(), slots_.end(), [target](const Slot &s) { return s.target == target; });
  if (it == slots_.end()) {
    slots_.push_back(Slot{target, 0, {}});
    it = slots_.end() - 1;
  }
  Slot &slot = *it;

  if (slot.tick != tick_ || slot.submissions.empty() || slot.submissions.back().owner != owner) {
    // A new state from this owner. Its older submissions are superseded: drop them, unless one still
    // holds a required packet that has not been sent yet. Those, and other owners' submissions
    // that have not been sent completely, are kept but sent only once.
    std::deque<Submission> kept;
    for (auto &sub : slot.submissions) {
      if (sub.owner != owner) {
        if (!sub.first_pass_done || sub.cursor != 0) {
          sub.rounds_left = 1;
          kept.push_back(std::move(sub));
        } else {
          dropped_count_++;
        }
        continue;
      }
      size_t last_required = SIZE_MAX;
      if (!sub.first_pass_done) {
        for (size_t i = sub.cursor; i < sub.packets.size(); i++) {
          if (sub.packets[i].required && !sub.packets[i].sent)
            last_required = i;
        }
      }
      if (last_required != SIZE_MAX) {
        // Keep it up to its last required packet; the state after that is superseded.
        sub.packets.resize(last_required + 1);
        sub.rounds_left = 1;
        kept.push_back(std::move(sub));
      } else {
        dropped_count_++;
      }
    }
    slot.submissions = std::move(kept);
    slot.submissions.push_back(Submission{owner, {}, 0, repeats_, false});
    slot.tick = tick_;
  }

  slot.submissions.back().packets.push_back(Packet{data, required, false});

  // Bound memory: if far too much is pending, drop the oldest submissions first.
  size_t total = 0;
  for (auto &s : slots_)
    for (auto &sub : s.submissions)
      total += sub.packets.size();
  while (total > max_queue_size_) {
    Slot *oldest = nullptr;
    for (auto &s : slots_)
      if (s.submissions.size() > 1 && (oldest == nullptr || s.submissions.size() > oldest->submissions.size()))
        oldest = &s;
    if (oldest == nullptr)
      break;
    total -= oldest->submissions.front().packets.size();
    oldest->submissions.pop_front();
    dropped_count_++;
    ESP_LOGW(TAG, "Too many pending commands, dropped an old one for target %u", (unsigned) oldest->target);
  }
}

void FastconController::clear_queue() {
  std::lock_guard<std::mutex> lock(queue_mutex_);
  slots_.clear();
  next_slot_ = 0;
}

size_t FastconController::get_queue_size() const {
  std::lock_guard<std::mutex> lock(queue_mutex_);
  size_t total = 0;
  for (auto &s : slots_)
    for (auto &sub : s.submissions)
      total += sub.packets.size() - sub.cursor;
  return total;
}

void FastconController::setup() {}

void FastconController::dump_config() {
  ESP_LOGCONFIG(TAG, "FastCon BLE Controller:");
  ESP_LOGCONFIG(TAG, "  Advertisement interval: %u-%u (x0.625ms)", adv_interval_min_, adv_interval_max_);
  ESP_LOGCONFIG(TAG, "  Advertisement duration: %ums, gap: %ums", adv_duration_, adv_gap_);
  ESP_LOGCONFIG(TAG, "  Repeats per command: %u", repeats_);
}

bool FastconController::next_packet_(std::vector<uint8_t> &out) {
  std::lock_guard<std::mutex> lock(queue_mutex_);

  // Drop finished targets.
  for (auto &slot : slots_) {
    while (!slot.submissions.empty() && slot.submissions.front().packets.empty())
      slot.submissions.pop_front();
  }
  // Remove empty slots, keeping the round-robin position on the same target.
  size_t kept = 0;
  const size_t old_next = next_slot_;
  for (size_t i = 0; i < slots_.size(); i++) {
    if (slots_[i].submissions.empty()) {
      if (i < old_next && next_slot_ > 0)
        next_slot_--;
      continue;
    }
    if (kept != i)
      slots_[kept] = std::move(slots_[i]);
    kept++;
  }
  slots_.resize(kept);
  if (slots_.empty())
    return false;

  // Round-robin over targets, but every target's first transmission goes before any repeat, so a
  // large change (many lights at once) reaches every lamp once as fast as possible.
  const size_t count = slots_.size();
  size_t pick = next_slot_ % count;
  for (size_t i = 0; i < count; i++) {
    const size_t idx = (next_slot_ + i) % count;
    if (!slots_[idx].submissions.front().first_pass_done) {
      pick = idx;
      break;
    }
  }

  Slot &slot = slots_[pick];
  Submission &sub = slot.submissions.front();
  Packet &packet = sub.packets[sub.cursor];
  out = packet.data;
  packet.sent = true;

  sub.cursor++;
  if (sub.cursor >= sub.packets.size()) {
    sub.cursor = 0;
    sub.first_pass_done = true;
    if (sub.rounds_left <= 1) {
      slot.submissions.pop_front();
    } else {
      sub.rounds_left--;
    }
  }
  next_slot_ = pick + 1;
  return true;
}

bool FastconController::start_advertising_(const std::vector<uint8_t> &data) {
  esp_ble_adv_params_t adv_params = {
      .adv_int_min = adv_interval_min_,
      .adv_int_max = adv_interval_max_,
      .adv_type = ADV_TYPE_NONCONN_IND,
      .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
      .peer_addr = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00},
      .peer_addr_type = BLE_ADDR_TYPE_PUBLIC,
      .channel_map = ADV_CHNL_ALL,
      .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
  };

  uint8_t adv_data_raw[31] = {0};
  uint8_t adv_data_len = 0;

  // Flags
  adv_data_raw[adv_data_len++] = 2;
  adv_data_raw[adv_data_len++] = ESP_BLE_AD_TYPE_FLAG;
  adv_data_raw[adv_data_len++] = ESP_BLE_ADV_FLAG_BREDR_NOT_SPT | ESP_BLE_ADV_FLAG_GEN_DISC;

  // Manufacturer data
  const size_t len = std::min<size_t>(data.size(), sizeof(adv_data_raw) - 7);
  adv_data_raw[adv_data_len++] = len + 3;
  adv_data_raw[adv_data_len++] = ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE;
  adv_data_raw[adv_data_len++] = MANUFACTURER_DATA_ID & 0xFF;
  adv_data_raw[adv_data_len++] = (MANUFACTURER_DATA_ID >> 8) & 0xFF;
  memcpy(&adv_data_raw[adv_data_len], data.data(), len);
  adv_data_len += len;

  esp_err_t err = esp_ble_gap_config_adv_data_raw(adv_data_raw, adv_data_len);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Error setting raw advertisement data: %s", esp_err_to_name(err));
    return false;
  }
  err = esp_ble_gap_start_advertising(&adv_params);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Error starting advertisement: %s", esp_err_to_name(err));
    return false;
  }
  return true;
}

void FastconController::loop() {
  const uint32_t now = millis();
  {
    // Everything queued during one pass of the main loop (one write_state() call) is one
    // submission; packets queued after this point start a new one.
    std::lock_guard<std::mutex> lock(queue_mutex_);
    if (++tick_ == 0)
      tick_ = 1;
  }
  switch (adv_state_) {
    case AdvertiseState::IDLE: {
      std::vector<uint8_t> data;
      if (!next_packet_(data))
        return;
      if (!start_advertising_(data))
        return;
      sent_count_++;
      adv_state_ = AdvertiseState::ADVERTISING;
      state_start_time_ = now;
      break;
    }
    case AdvertiseState::ADVERTISING: {
      if (now - state_start_time_ >= adv_duration_) {
        esp_ble_gap_stop_advertising();
        adv_state_ = AdvertiseState::GAP;
        state_start_time_ = now;
      }
      break;
    }
    case AdvertiseState::GAP: {
      if (now - state_start_time_ >= adv_gap_) {
        adv_state_ = AdvertiseState::IDLE;
      }
      break;
    }
  }
}

// --- helpers for channel resolution ---
static inline uint8_t to8(float v) {
  if (v < 0.0f) v = 0.0f;
  if (v > 1.0f) v = 1.0f;
  return static_cast<uint8_t>(v * 255.0f + 0.5f);
}

static inline bool all_zero(float r, float g, float b, float cw, float ww) {
  return r == 0.0f && g == 0.0f && b == 0.0f && cw == 0.0f && ww == 0.0f;
}

std::vector<uint8_t> FastconController::get_light_data(light::LightState *state) {
  // Protocol: 6 bytes when ON
  // [0] 0x80 | (brightness 0..127)
  // [1] Blue, [2] Red, [3] Green, [4] Warm, [5] Cold
  // When OFF, a single 0x00 byte is returned.

  auto &values = state->current_values;
  const bool is_on = values.is_on();
  if (!is_on) {
    return std::vector<uint8_t>({0x00});
  }

  // Compute final channel levels from current values so brightness/color_brightness are applied.
  float r=0, g=0, b=0, cw=0, ww=0;
  state->current_values_as_rgbww(&r, &g, &b, &cw, &ww, /*constant_brightness=*/false);  // per ESPHome light API

  // If color mode is WHITE on RGBW fixtures (no CW/WW), map to RGB white.
  const auto mode = values.get_color_mode();
  // Heuristic: if traits have a valid CT range, we treat device as supporting CW/WW.
  const bool supports_cwww = state->get_traits().get_min_mireds() > 0.0f;

  if ((mode == light::ColorMode::WHITE || mode == light::ColorMode::COLD_WARM_WHITE) && !supports_cwww) {
    float m = (ww > 0 ? ww : cw);
    r = g = b = m; cw = ww = 0.0f;
  }

  // Fallback for UNKNOWN color mode / zeroed channels: first ON should be warm white for RGBCW, RGB white otherwise.
  if (all_zero(r,g,b,cw,ww)) {
    if (supports_cwww) { ww = 1.0f; /* warm white */ }
    else { r = g = b = 1.0f; }
  }

  // Compose payload
  const float blevel = std::min(values.get_brightness() * 127.0f, 127.0f);
  std::vector<uint8_t> light_data = {
      static_cast<uint8_t>(0x80 | static_cast<uint8_t>(blevel)),
      to8(b),  // Blue
      to8(r),  // Red
      to8(g),  // Green
      to8(ww), // Warm
      to8(cw)  // Cold
  };

  return light_data;
}

// special payload for white LED with color_interlock 
std::vector<uint8_t> FastconController::get_white_light_data(light::LightState *state) {
  auto &values = state->current_values;
  const bool is_on = values.is_on();
  if (!is_on) {
    return std::vector<uint8_t>({0x00});
  }

  const float blevel = std::min(values.get_brightness() * 127.0f, 127.0f);
  std::vector<uint8_t> light_data = {
      static_cast<uint8_t>(0x80 | static_cast<uint8_t>(blevel)),
      0,
      0,
      0,
      127, // Warm
      127  // Cold
  };

  return light_data;
}

std::vector<uint8_t> FastconController::single_control(uint32_t light_id_, const std::vector<uint8_t> &light_data) {
  std::vector<uint8_t> result_data(12);
  result_data[0] = 2 | (((0x0FFFFFF & (light_data.size() + 1)) << 4));
  result_data[1] = light_id_;
  std::copy(light_data.begin(), light_data.end(), result_data.begin() + 2);

#if ESPHOME_LOG_LEVEL >= ESPHOME_LOG_LEVEL_VERBOSE
  const auto hex_vec = vector_to_hex_string(result_data);
  const std::string hex(hex_vec.begin(), hex_vec.end());
  ESP_LOGV(TAG, "Inner Payload v%s (%zu bytes): %s", FASTCON_VERSION, result_data.size(), hex.c_str());
#endif

  return this->generate_command(5, light_id_, result_data, true);
}

std::vector<uint8_t> FastconController::generate_command(uint8_t n, uint32_t light_id_, const std::vector<uint8_t> &data, bool forward) {
  static uint8_t sequence = 0;

  // Create command body with header
  std::vector<uint8_t> body(data.size() + 4);
  uint8_t i2 = (light_id_ / 256);

  // Header
  body[0] = (i2 & 0b1111) | ((n & 0b111) << 4) | (forward ? 0x80 : 0);
  body[1] = sequence++;
  if (sequence >= 255) sequence = 1;
  body[2] = this->mesh_key_[3];  // Safe key

  // Copy data
  std::copy(data.begin(), data.end(), body.begin() + 4);

  // Checksum
  uint8_t checksum = 0;
  for (size_t i = 0; i < body.size(); i++) {
    if (i != 3) checksum = checksum + body[i];
  }
  body[3] = checksum;

  // Encrypt header and data
  for (size_t i = 0; i < 4; i++) {
    body[i] = DEFAULT_ENCRYPT_KEY[i & 3] ^ body[i];
  }
  for (size_t i = 0; i < data.size(); i++) {
    body[4 + i] = this->mesh_key_[i & 3] ^ body[4 + i];
  }

  // RF protocol formatting
  std::vector<uint8_t> addr = {DEFAULT_BLE_FASTCON_ADDRESS.begin(), DEFAULT_BLE_FASTCON_ADDRESS.end()};
  return prepare_payload(addr, body);
}

} // namespace fastcon
} // namespace esphome
