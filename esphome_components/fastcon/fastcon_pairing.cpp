// Pairing reset brMesh/FastCon lamps from the ESP32.
//
// Reverse engineered from the brMesh app (BLEFastconHelper.sendStartScan / sendDiscRes,
// ScanDeviceActivity.AddDevThread) and its libbroadlink_ble.so (package_disc_res,
// parse_ble_broadcast):
//
//  1. Discovery request: command type 0, 12 zero bytes, body encrypted with the default key and
//     0xFF as the header's key byte.
//  2. A lamp in pairing mode answers with an advertisement (manufacturer data, company 0xfff0)
//     carrying a 16 byte frame: 4 byte header (XOR default key, type 1), its 6 byte device ID,
//     2 byte device type and its own 4 byte key.
//  3. Reply: command type 2 to the new light ID, payload [device ID][light ID][1][mesh key],
//     encrypted with the lamp's key.
//  4. The lamp joins the mesh and sends heartbeats (type 3, body encrypted with the mesh key,
//     payload type 4) from its new light ID.
//
// Lamps that answer in the "new protocol" frame (light IDs above 255, gateways) need the app's
// 18 byte reply, which is not implemented; they are reported as unsupported.
#include <algorithm>
#include <cstdio>
#include <cstring>
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "fastcon_controller.h"
#include "protocol.h"

namespace esphome {
namespace fastcon {

static const char *const TAG = "fastcon.pairing";

static const uint32_t PAIRING_SEND_INTERVAL_MS = 1000;
static const size_t PAIRING_MAX_LAMPS = 64;
// The text sensor's state goes to Home Assistant, which rejects states over 255 characters.
static const size_t PAIRING_STATUS_MAX_LEN = 250;
static const uint16_t TYPE_GATEWAY_NEW = 43756;
static const uint8_t DEV_CTRL_UPLOAD_PREFIX[] = {0x6d, 0xb6, 0x43, 0x68, 0x93, 0x1d};

void FastconController::start_pairing(const std::vector<int32_t> &light_ids, int32_t seconds) {
  this->pairing_lamps_.clear();
  this->pairing_free_ids_.clear();
  for (int32_t id : light_ids) {
    if (id >= 1 && id <= 255 &&
        std::find(this->pairing_free_ids_.begin(), this->pairing_free_ids_.end(), id) == this->pairing_free_ids_.end())
      this->pairing_free_ids_.push_back(static_cast<uint16_t>(id));
  }
  seconds = std::max<int32_t>(10, std::min<int32_t>(seconds <= 0 ? 120 : seconds, 600));
  this->pairing_ = true;
  this->pairing_until_ = millis() + static_cast<uint32_t>(seconds) * 1000;
  this->pairing_last_send_ = millis() - PAIRING_SEND_INTERVAL_MS;
  this->pairing_set_scanning_(true);
  ESP_LOGI(TAG, "Pairing for %d s, %u light IDs available", (int) seconds, (unsigned) this->pairing_free_ids_.size());
  this->pairing_dirty_ = true;
  this->pairing_publish_();
}

void FastconController::stop_pairing() {
  if (!this->pairing_)
    return;
  this->pairing_ = false;
  this->pairing_set_scanning_(false);
  size_t paired = std::count_if(this->pairing_lamps_.begin(), this->pairing_lamps_.end(),
                                [](const PairingLamp &l) { return l.state == PairingLamp::State::OK; });
  ESP_LOGI(TAG, "Pairing stopped, %u lamp(s) paired", (unsigned) paired);
  this->pairing_dirty_ = true;
  this->pairing_publish_();
}

void FastconController::pairing_set_scanning_(bool on) {
  if (on == this->scanning_)
    return;
  this->scanning_ = on;
  if (!on) {
    esp_ble_gap_stop_scanning();
    return;
  }
  // Passive scan, leaving the radio free half of the time for the advertisements that carry commands.
  esp_ble_scan_params_t params = {
      .scan_type = BLE_SCAN_TYPE_PASSIVE,
      .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
      .scan_filter_policy = BLE_SCAN_FILTER_ALLOW_ALL,
      .scan_interval = 0x50,  // 50 ms
      .scan_window = 0x28,    // 25 ms
      .scan_duplicate = BLE_SCAN_DUPLICATE_DISABLE,
  };
  esp_err_t err = esp_ble_gap_set_scan_params(&params);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Setting scan parameters failed: %s", esp_err_to_name(err));
    this->scanning_ = false;
  }
  // Scanning starts once the stack confirms the parameters (gap_event_handler).
}

void FastconController::gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
  if (event == ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT && this->scanning_) {
    esp_err_t err = esp_ble_gap_start_scanning(0);
    if (err != ESP_OK)
      ESP_LOGW(TAG, "Starting the scan failed: %s", esp_err_to_name(err));
  } else if (event == ESP_GAP_BLE_SCAN_START_COMPLETE_EVT && this->scanning_ &&
             param->scan_start_cmpl.status != ESP_BT_STATUS_SUCCESS) {
    ESP_LOGW(TAG, "Scan did not start, status %d", param->scan_start_cmpl.status);
  }
}

void FastconController::gap_scan_event_handler(const esp32_ble::BLEScanResult &scan_result) {
  if (!this->pairing_ || scan_result.search_evt != ESP_GAP_SEARCH_INQ_RES_EVT)
    return;
  const uint8_t *adv = scan_result.ble_adv;
  const size_t total = scan_result.adv_data_len + scan_result.scan_rsp_len;
  for (size_t i = 0; i + 1 < total;) {
    const uint8_t len = adv[i];
    if (len == 0 || i + 1 + len > total)
      break;
    // Manufacturer data of company 0xfff0, as the lamps send it.
    if (adv[i + 1] == ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE && len >= 3 + 16 && adv[i + 2] == 0xf0 &&
        adv[i + 3] == 0xff) {
      const uint8_t *data = adv + i + 4;
      const size_t data_len = len - 3;
      if (data[0] == 0xa5 && data[1] == 0x5a) {
        this->pairing_handle_frame_(data + 2, data_len - 2, true);
      } else if (!std::equal(std::begin(DEV_CTRL_UPLOAD_PREFIX), std::end(DEV_CTRL_UPLOAD_PREFIX), data)) {
        this->pairing_handle_frame_(data, data_len, false);
      }
    }
    i += len + 1;
  }
}

void FastconController::pairing_handle_frame_(const uint8_t *frame, size_t len, bool new_protocol) {
  if (len < 16)
    return;
  uint8_t header[4];
  for (size_t i = 0; i < 4; i++)
    header[i] = frame[i] ^ DEFAULT_ENCRYPT_KEY[i];
  const uint8_t type = (header[0] >> 4) & 0x07;
  const uint8_t high = header[0] & 0x0f;

  if (type == 1 && len == (new_protocol ? 22u : 16u)) {
    // Discovery answer of a lamp in pairing mode. The body is not encrypted.
    PairingLamp lamp;
    std::copy(frame + 4, frame + 10, lamp.did.begin());
    lamp.type = frame[10] | (frame[11] << 8);
    std::copy(frame + 12, frame + 16, lamp.key.begin());
    for (auto &known : this->pairing_lamps_) {
      if (known.did == lamp.did) {
        known.key = lamp.key;  // replies are encrypted with the latest key it announced
        return;
      }
    }
    if (this->pairing_lamps_.size() >= PAIRING_MAX_LAMPS)
      return;
    if (new_protocol || high > 2 || lamp.type == TYPE_GATEWAY_NEW) {
      lamp.state = PairingLamp::State::UNSUPPORTED;
    } else if (this->pairing_free_ids_.empty()) {
      lamp.state = PairingLamp::State::NO_FREE_ID;
    } else {
      lamp.light_id = this->pairing_free_ids_.front();
      this->pairing_free_ids_.pop_front();
      lamp.state = PairingLamp::State::OFFERED;
      // Answer right away instead of waiting for the next round.
      this->pairing_last_send_ = millis() - PAIRING_SEND_INTERVAL_MS;
    }
    lamp.last_change = millis();
    ESP_LOGI(TAG, "Found lamp %02x%02x%02x%02x%02x%02x, type %u -> %s", lamp.did[0], lamp.did[1], lamp.did[2],
             lamp.did[3], lamp.did[4], lamp.did[5], lamp.type,
             lamp.state == PairingLamp::State::OFFERED       ? "pairing"
             : lamp.state == PairingLamp::State::NO_FREE_ID ? "no free light ID"
                                                             : "not supported (extended protocol)");
    if (lamp.state == PairingLamp::State::OFFERED)
      ESP_LOGI(TAG, "  assigned light ID %u", lamp.light_id);
    this->pairing_lamps_.push_back(lamp);
    this->pairing_dirty_ = true;
    return;
  }

  if (type == 3 && !new_protocol) {
    // Heartbeat: body encrypted with the mesh key, checksum over header and the 12 byte payload.
    uint8_t payload[12];
    uint8_t checksum = header[0] + header[1] + header[2];
    for (size_t i = 0; i < sizeof(payload); i++) {
      payload[i] = frame[4 + i] ^ this->mesh_key_[i & 3];
      checksum += payload[i];
    }
    if (checksum != header[3] || (payload[0] & 0x0f) != 4)
      return;
    const uint16_t addr = payload[1] | (high << 8);
    for (auto &lamp : this->pairing_lamps_) {
      if (lamp.state == PairingLamp::State::OFFERED && lamp.light_id == addr) {
        lamp.state = PairingLamp::State::OK;
        lamp.last_change = millis();
        this->pairing_dirty_ = true;
        ESP_LOGI(TAG, "Lamp %02x%02x%02x%02x%02x%02x paired as light %u", lamp.did[0], lamp.did[1], lamp.did[2],
                 lamp.did[3], lamp.did[4], lamp.did[5], addr);
      }
    }
  }
}

void FastconController::pairing_loop_(uint32_t now) {
  if (static_cast<int32_t>(now - this->pairing_until_) >= 0) {
    this->stop_pairing();
    return;
  }
  if (now - this->pairing_last_send_ >= PAIRING_SEND_INTERVAL_MS) {
    this->pairing_last_send_ = now;
    bool offered = false;
    for (auto &lamp : this->pairing_lamps_) {
      if (lamp.state != PairingLamp::State::OFFERED)
        continue;
      offered = true;
      std::vector<uint8_t> reply(lamp.did.begin(), lamp.did.end());
      reply.push_back(static_cast<uint8_t>(lamp.light_id & 0xff));
      reply.push_back(1);
      reply.insert(reply.end(), this->mesh_key_.begin(), this->mesh_key_.end());
      // One scheduler slot per lamp, so replies to different lamps do not replace each other.
      this->queueCommand(TARGET_PAIRING + 0x100 + lamp.light_id,
                         this->generate_command_with_key(2, lamp.light_id, reply, false, lamp.key, lamp.key[3]));
    }
    // The app keeps asking for new lamps while it answers found ones, less often.
    static uint8_t round = 0;
    if (!offered || ++round % 3 == 0) {
      std::vector<uint8_t> request(12, 0);
      std::array<uint8_t, 4> default_key = {DEFAULT_ENCRYPT_KEY[0], DEFAULT_ENCRYPT_KEY[1], DEFAULT_ENCRYPT_KEY[2],
                                            DEFAULT_ENCRYPT_KEY[3]};
      this->queueCommand(TARGET_PAIRING, this->generate_command_with_key(0, 0, request, false, default_key, 0xff));
    }
  }
  if (this->pairing_dirty_)
    this->pairing_publish_();
}

void FastconController::pairing_publish_() {
  if (!this->pairing_dirty_)
    return;
  this->pairing_dirty_ = false;
  if (this->pairing_sensor_ == nullptr)
    return;
  // "on|<light id>,<device id>,<type>,<state>;..." with the most recently changed lamps first.
  std::vector<const PairingLamp *> lamps;
  for (auto &lamp : this->pairing_lamps_)
    lamps.push_back(&lamp);
  std::stable_sort(lamps.begin(), lamps.end(),
                   [](const PairingLamp *a, const PairingLamp *b) { return a->last_change > b->last_change; });
  std::string out = this->pairing_ ? "on|" : "off|";
  for (const PairingLamp *lamp : lamps) {
    const char *state = lamp->state == PairingLamp::State::OK           ? "ok"
                        : lamp->state == PairingLamp::State::OFFERED    ? "wait"
                        : lamp->state == PairingLamp::State::NO_FREE_ID ? "nofree"
                                                                        : "unsup";
    char entry[48];
    snprintf(entry, sizeof(entry), "%u,%02x%02x%02x%02x%02x%02x,%u,%s;", lamp->light_id, lamp->did[0], lamp->did[1],
             lamp->did[2], lamp->did[3], lamp->did[4], lamp->did[5], lamp->type, state);
    if (out.size() + strlen(entry) > PAIRING_STATUS_MAX_LEN)
      break;
    out += entry;
  }
  this->pairing_sensor_->publish_state(out);
}

}  // namespace fastcon
}  // namespace esphome
