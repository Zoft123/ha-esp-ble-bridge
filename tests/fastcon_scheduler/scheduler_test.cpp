#include <cassert>
#include <cstring>
#include <vector>
#include <string>
#include "fastcon_controller.h"
namespace esphome { uint32_t g_now = 0; }
static std::vector<std::vector<uint8_t>> sent;
static std::vector<uint8_t> cur;
esp_err_t esp_ble_gap_config_adv_data_raw(uint8_t *d, uint32_t n) { cur.assign(d + 7, d + n); return 0; }
esp_err_t esp_ble_gap_start_advertising(esp_ble_adv_params_t *) { sent.push_back(cur); return 0; }
esp_err_t esp_ble_gap_stop_advertising() { return 0; }
using esphome::g_now;
struct Ctl : esphome::fastcon::FastconController {};
static std::vector<uint8_t> P(uint8_t t, uint8_t v) { return {t, v}; }
static void run(Ctl &c, int ms) { for (int i = 0; i < ms; i++) { g_now++; c.loop(); } }
static std::string fmt() { std::string s; for (auto &p : sent) { s += std::to_string(p[0]) + ":" + std::to_string(p[1]) + " "; } return s; }
int main() { setvbuf(stdout, nullptr, _IONBF, 0);
  { // round robin + repeats
    Ctl c; c.set_repeats(2); sent.clear();
    c.queueCommand(1, P(1, 10)); c.queueCommand(2, P(2, 20));
    run(c, 1000);
    printf("rr:      %s\n", fmt().c_str());
    assert(fmt() == "1:10 2:20 1:10 2:20 ");
  }
  { // newer state for a target cancels older repeats
    Ctl c; c.set_repeats(3); sent.clear();
    c.queueCommand(1, P(1, 10)); run(c, 1); // first packet goes out
    c.queueCommand(1, P(1, 11));             // new loop tick -> new submission
    run(c, 1000);
    printf("latest:  %s\n", fmt().c_str());
    assert(fmt() == "1:10 1:11 1:11 1:11 ");
  }
  { // superseded unsent state is dropped entirely
    Ctl c; c.set_repeats(1); sent.clear();
    c.queueCommand(5, P(5, 1)); c.loop(); /* tick but adv started for 5:1 */
    c.queueCommand(6, P(6, 1)); c.loop();
    c.queueCommand(6, P(6, 2)); // 6:1 unsent? loop advanced tick; 6:1 was not yet sent (5:1 still advertising)
    run(c, 1000);
    printf("drop:    %s\n", fmt().c_str());
    assert(fmt() == "5:1 6:2 ");
  }
  { // required power-on is kept when a newer color arrives before it was sent
    Ctl c; c.set_repeats(2); sent.clear();
    c.queueCommand(9, P(9, 99)); c.loop();              // something else is advertising
    c.queueCommand(0, P(0, 0x80), true); c.queueCommand(0, P(0, 1)); c.loop();  // power + color, same submission
    c.queueCommand(0, P(0, 2));                          // new color before the power went out
    run(c, 1000);
    printf("power:   %s\n", fmt().c_str());
    assert(fmt() == "9:99 0:128 0:2 9:99 0:2 ");  // new first transmissions go before 9's repeat
  }
  { // same-tick packets keep their order
    Ctl c; c.set_repeats(1); sent.clear();
    c.queueCommand(0xFD, P(0xFD, 1), true); c.queueCommand(0xFD, P(0xFD, 2));
    run(c, 1000);
    printf("order:   %s\n", fmt().c_str());
    assert(fmt() == "253:1 253:2 ");
  }
  { // first transmissions of all targets go before any repeats
    Ctl c; c.set_repeats(2); sent.clear();
    for (uint8_t t = 1; t <= 4; t++) c.queueCommand(t, P(t, 1));
    run(c, 1000);
    printf("first:   %s\n", fmt().c_str());
    assert(fmt() == "1:1 2:1 3:1 4:1 1:1 2:1 3:1 4:1 ");
  }
  { // a target queued while others repeat jumps ahead of the repeats
    Ctl c; c.set_repeats(3); sent.clear();
    c.queueCommand(1, P(1, 1)); c.queueCommand(2, P(2, 1));
    run(c, 100);                  // 1 and 2 sent once, repeats running
    c.queueCommand(3, P(3, 1));
    run(c, 1000);
    printf("jump:    %s\n", fmt().c_str());
    assert(fmt().find("3:1") < fmt().find("2:1 1:1 2:1"));
  }
  { // many targets: the last one gets its first packet after exactly N transmissions
    Ctl c; c.set_repeats(3); c.set_max_queue_size(1000); sent.clear();
    for (int t = 1; t <= 200; t++) c.queueCommand(t, P(t & 0xff, 1));
    run(c, 100000);
    size_t first_of_last = 0;
    for (size_t i = 0; i < sent.size(); i++) if (sent[i][0] == 200) { first_of_last = i; break; }
    printf("scale:   %zu packets, last target first sent at #%zu\n", sent.size(), first_of_last + 1);
    assert(sent.size() == 600 && first_of_last == 199);
  }
  { // group lights share a slot: different groups are serialized, the same group supersedes itself
    using C = esphome::fastcon::FastconController;
    Ctl c; c.set_repeats(2); sent.clear();
    c.queueCommand(5, P(5, 1)); c.loop();                                    // keep the radio busy
    c.queueCommand(C::TARGET_GROUPS, P(0xA1, 0), true, 0xA); c.queueCommand(C::TARGET_GROUPS, P(0xA2, 0), false, 0xA);
    c.queueCommand(C::TARGET_GROUPS, P(0xB1, 0), true, 0xB); c.queueCommand(C::TARGET_GROUPS, P(0xB2, 0), false, 0xB);
    c.loop();
    c.queueCommand(C::TARGET_GROUPS, P(0xB1, 1), true, 0xB); c.queueCommand(C::TARGET_GROUPS, P(0xB2, 1), false, 0xB);
    run(c, 2000);
    printf("groups:  %s\n", fmt().c_str());
    // A goes out completely and once; B's first submission is cut after its selector, then the new B repeats.
    assert(fmt() == "5:1 161:0 162:0 177:0 177:1 178:1 5:1 177:1 178:1 ");
  }
  { // broadcast and group keys never collide with light IDs
    using C = esphome::fastcon::FastconController;
    assert(C::TARGET_BROADCAST > 255 && C::TARGET_GROUPS > 255);
  }
  printf("ALL OK\n");
}
