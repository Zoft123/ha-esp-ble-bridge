// Host test of the light state encoding and of pairing, against the packet formats of the brMesh
// app (BLEFastconHelper / libbroadlink_ble.so).
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>
#include "fastcon_color_helpers.h"
#include "fastcon_controller.h"
#include "protocol.h"

namespace esphome { uint32_t g_now = 0; }
using esphome::g_now;
using namespace esphome;
using namespace esphome::fastcon;

static std::vector<std::vector<uint8_t>> sent;
static std::vector<uint8_t> cur;
esp_err_t esp_ble_gap_config_adv_data_raw(uint8_t *d, uint32_t n) { cur.assign(d + 7, d + n); return 0; }
esp_err_t esp_ble_gap_start_advertising(esp_ble_adv_params_t *) { sent.push_back(cur); return 0; }
esp_err_t esp_ble_gap_stop_advertising() { return 0; }
static int scanning = 0;
esp_err_t esp_ble_gap_set_scan_params(esp_ble_scan_params_t *) { return 0; }
esp_err_t esp_ble_gap_start_scanning(uint32_t) { scanning = 1; return 0; }
esp_err_t esp_ble_gap_stop_scanning() { scanning = 0; return 0; }

struct Ctl : FastconController {};
static int failures = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", msg); failures++; } } while (0)

static std::string hex(const std::vector<uint8_t> &v) {
  std::string s; char b[3];
  for (auto x : v) { snprintf(b, sizeof b, "%02x", x); s += b; }
  return s;
}

// Undo prepare_payload(): whitening is a keystream XOR, the 3 header bytes and the address are
// bit-reversed. Returns the fastcon body (header + payload), still encrypted.
static std::vector<uint8_t> rf_to_body(const std::vector<uint8_t> &rf) {
  std::vector<uint8_t> tmp(15, 0);
  tmp.insert(tmp.end(), rf.begin(), rf.end());
  WhiteningContext ctx;
  whitening_init(0x25, ctx);
  whitening_encode(tmp, ctx);
  std::vector<uint8_t> plain(tmp.begin() + 15, tmp.end());
  return std::vector<uint8_t>(plain.begin() + 6, plain.end() - 2);  // skip 71 0f 55 + address, drop CRC
}

struct Decoded { uint8_t type, high, seq, safe, checksum_ok; std::vector<uint8_t> payload; };
static Decoded decode(const std::vector<uint8_t> &body, const std::array<uint8_t, 4> &key) {
  Decoded d{};
  uint8_t h[4];
  for (int i = 0; i < 4; i++) h[i] = body[i] ^ DEFAULT_ENCRYPT_KEY[i];
  d.type = (h[0] >> 4) & 7; d.high = h[0] & 0x0f; d.seq = h[1]; d.safe = h[2];
  uint8_t sum = h[0] + h[1] + h[2];
  for (size_t i = 4; i < body.size(); i++) { d.payload.push_back(body[i] ^ key[(i - 4) & 3]); sum += d.payload.back(); }
  d.checksum_ok = sum == h[3];
  return d;
}

// An advertisement as a lamp sends it: flags + manufacturer data (company 0xfff0) + frame.
static esp32_ble::BLEScanResult advert(const std::vector<uint8_t> &frame) {
  esp32_ble::BLEScanResult r{};
  std::vector<uint8_t> adv = {0x02, 0x01, 0x06, static_cast<uint8_t>(frame.size() + 3), 0xff, 0xf0, 0xff};
  adv.insert(adv.end(), frame.begin(), frame.end());
  std::copy(adv.begin(), adv.end(), r.ble_adv);
  r.adv_data_len = adv.size();
  r.search_evt = ESP_GAP_SEARCH_INQ_RES_EVT;
  return r;
}

static void run(Ctl &c, int ms) { for (int i = 0; i < ms; i++) { g_now++; c.loop(); } }

static void test_encoding() {
  light::LightColorValues v;
  v.mode = light::ColorMode::RGB; v.brightness = 0.5f; v.red = 1; v.green = 0.5f; v.blue = 0;
  // Brightness only in byte 0 (63/127), the color unscaled: [bri][B][R][G][warm][cold].
  CHECK(hex(fastcon_light_data(v, true)) == "c000ff800000", "RGB at 50% keeps the color unscaled");

  v = {}; v.mode = light::ColorMode::COLD_WARM_WHITE; v.cold = 1; v.warm = 1;
  CHECK(hex(fastcon_light_data(v, true)) == "ff0000008080" || hex(fastcon_light_data(v, true)) == "ff000000807f",
        "mid color temperature splits 255 between warm and cold");
  auto mid = fastcon_light_data(v, true);
  CHECK(mid[4] + mid[5] == 255, "warm + cold == 255");
  v.cold = 0; v.warm = 1;
  CHECK(hex(fastcon_light_data(v, true)) == "ff000000ff00", "warm white");
  v.cold = 1; v.warm = 0;
  CHECK(hex(fastcon_light_data(v, true)) == "ff00000000ff", "cool white");

  v = {}; v.mode = light::ColorMode::WHITE; v.brightness = 1;
  CHECK(hex(fastcon_light_data(v, false)) == "ff0000007f7f", "RGBW white is the app's whiteCommand");

  v = {}; v.on = false;
  CHECK(hex(fastcon_light_data(v, true)) == "00", "off is a single 00");

  v = {}; v.mode = light::ColorMode::UNKNOWN; v.brightness = 0.001f;
  CHECK(hex(fastcon_light_data(v, true)) == "81000000ff00", "first on: warm white, brightness at least 1");
  CHECK(hex(fastcon_light_data(v, false)) == "81555555" "0000", "first on without white LEDs: the app's RGB white");
}

static void test_pairing() {
  Ctl c;
  c.set_mesh_key({0x11, 0x22, 0x33, 0x44});
  c.set_repeats(1);
  esphome::text_sensor::TextSensor sensor;
  c.set_pairing_sensor(&sensor);

  c.start_pairing({0, 7, 9, 300}, 60);  // 0 and 300 are not valid light IDs
  esp_ble_gap_cb_param_t p{};
  c.gap_event_handler(ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT, &p);
  CHECK(scanning == 1, "scanning while pairing");
  CHECK(sensor.state == "on|", "status published");

  // 1. The discovery request: type 0, 12 zero bytes, default key, key byte 0xff, no forwarding.
  sent.clear();
  run(c, 100);
  CHECK(!sent.empty(), "discovery request sent");
  auto d = decode(rf_to_body(sent[0]), {DEFAULT_ENCRYPT_KEY[0], DEFAULT_ENCRYPT_KEY[1], DEFAULT_ENCRYPT_KEY[2], DEFAULT_ENCRYPT_KEY[3]});
  CHECK(d.type == 0 && d.safe == 0xff && d.checksum_ok && d.payload == std::vector<uint8_t>(12, 0), "discovery request format");
  CHECK((rf_to_body(sent[0])[0] ^ DEFAULT_ENCRYPT_KEY[0]) < 0x80, "discovery request is not forwarded");

  // 2. A lamp answers: header type 1 (XOR default key), device ID, type 43050, its key.
  const std::array<uint8_t, 4> lamp_key = {0xa1, 0xb2, 0xc3, 0xd4};
  std::vector<uint8_t> frame = {0x10, 0x05, 0x00, 0x00, 0xde, 0xad, 0xbe, 0xef, 0x00, 0x01, 0x2a, 0xa8};
  frame.insert(frame.end(), lamp_key.begin(), lamp_key.end());
  for (int i = 0; i < 4; i++) frame[i] ^= DEFAULT_ENCRYPT_KEY[i];
  c.gap_scan_event_handler(advert(frame));
  c.gap_scan_event_handler(advert(frame));  // repeated answers change nothing
  run(c, 1);
  CHECK(sensor.state.rfind("on|7,deadbeef0001,43050,wait;", 0) == 0, ("lamp offered light 7: " + sensor.state).c_str());

  // 3. The reply: type 2 to light 7, [device ID][7][1][mesh key], encrypted with the lamp's key.
  sent.clear();
  run(c, 1100);
  bool found = false;
  for (auto &rf : sent) {
    auto r = decode(rf_to_body(rf), lamp_key);
    if (r.type != 2)
      continue;
    found = true;
    CHECK(r.checksum_ok, "reply checksum");
    CHECK(r.safe == lamp_key[3], "reply key byte is the lamp key's last byte");
    CHECK(hex(r.payload) == "deadbeef0001070111223344", ("reply payload " + hex(r.payload)).c_str());
  }
  CHECK(found, "pairing reply sent");

  // 4. Heartbeat from light 7 (type 3, payload type 4, encrypted with the mesh key) confirms it.
  std::vector<uint8_t> hb_payload = {0x04, 7, 0, 1, 2, 3, 4, 5, 0, 0, 0, 0};
  std::vector<uint8_t> hb = {0x30, 0x09, 0x44, 0};
  uint8_t sum = hb[0] + hb[1] + hb[2];
  for (auto x : hb_payload) sum += x;
  hb[3] = sum;
  for (int i = 0; i < 4; i++) hb[i] ^= DEFAULT_ENCRYPT_KEY[i];
  const std::array<uint8_t, 4> mesh = {0x11, 0x22, 0x33, 0x44};
  for (size_t i = 0; i < hb_payload.size(); i++) hb.push_back(hb_payload[i] ^ mesh[i & 3]);
  c.gap_scan_event_handler(advert(hb));
  run(c, 10);
  CHECK(sensor.state.rfind("on|7,deadbeef0001,43050,ok;", 0) == 0, ("lamp confirmed: " + sensor.state).c_str());

  // A second lamp gets the next free ID; a third finds none left.
  frame[4] ^= 0xff;  // other device ID
  c.gap_scan_event_handler(advert(frame));
  frame[5] ^= 0xff;
  c.gap_scan_event_handler(advert(frame));
  run(c, 1);
  CHECK(sensor.state.find(",9,") == std::string::npos, "status lists IDs first");
  CHECK(sensor.state.find("9,21adbeef0001,43050,wait") != std::string::npos, ("second lamp gets 9: " + sensor.state).c_str());
  CHECK(sensor.state.find("0,2152beef0001,43050,nofree") != std::string::npos, ("third lamp: no ID: " + sensor.state).c_str());

  // A heartbeat from light 9 with a wrong checksum must not confirm the second lamp.
  std::vector<uint8_t> bad = {0x30, 0x0a, 0x44, 0x00};
  for (int i = 0; i < 4; i++) bad[i] ^= DEFAULT_ENCRYPT_KEY[i];
  std::vector<uint8_t> bad_payload = {0x04, 9, 0, 1, 2, 3, 4, 5, 0, 0, 0, 0};
  for (size_t i = 0; i < bad_payload.size(); i++) bad.push_back(bad_payload[i] ^ mesh[i & 3]);
  c.gap_scan_event_handler(advert(bad));
  run(c, 1);
  CHECK(sensor.state.find("9,21adbeef0001,43050,wait") != std::string::npos, "bad heartbeat checksum ignored");

  // Pairing stops by itself after the given time.
  run(c, 61000);
  CHECK(!c.is_pairing() && scanning == 0, "pairing times out and stops scanning");
  CHECK(sensor.state.rfind("off|", 0) == 0, "status says off");
}

int main() {
  test_encoding();
  test_pairing();
  printf(failures ? "%d FAILURE(S)\n" : "ALL OK\n", failures);
  return failures ? 1 : 0;
}
