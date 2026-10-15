#pragma once
#include <cstdint>
#include <cstddef>
typedef int esp_err_t;
#define ESP_OK 0
enum { ADV_TYPE_NONCONN_IND, BLE_ADDR_TYPE_PUBLIC, ADV_CHNL_ALL, ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY };
#define ESP_BLE_AD_TYPE_FLAG 1
#define ESP_BLE_ADV_FLAG_BREDR_NOT_SPT 4
#define ESP_BLE_ADV_FLAG_GEN_DISC 2
#define ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE 0xff
struct esp_ble_adv_params_t { uint16_t adv_int_min, adv_int_max; int adv_type, own_addr_type; uint8_t peer_addr[6]; int peer_addr_type, channel_map, adv_filter_policy; };
esp_err_t esp_ble_gap_config_adv_data_raw(uint8_t *d, uint32_t n);
esp_err_t esp_ble_gap_start_advertising(esp_ble_adv_params_t *p);
esp_err_t esp_ble_gap_stop_advertising();
inline const char *esp_err_to_name(esp_err_t) { return "err"; }
