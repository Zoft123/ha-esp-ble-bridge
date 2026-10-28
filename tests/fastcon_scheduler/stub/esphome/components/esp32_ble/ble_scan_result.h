#pragma once
#include <cstdint>
namespace esphome { namespace esp32_ble {
struct BLEScanResult { uint8_t bda[6]; uint8_t ble_addr_type; int8_t rssi; uint8_t ble_adv[62]; uint8_t adv_data_len; uint8_t scan_rsp_len; uint8_t search_evt; };
}}
