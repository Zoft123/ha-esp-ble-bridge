#pragma once

#include <array>
#include <deque>
#include <mutex>
#include <vector>
#include <esp_gap_ble_api.h>
#include "esphome/core/component.h"
#include "esphome/components/esp32_ble/ble_scan_result.h"
#include "esphome/components/text_sensor/text_sensor.h"

namespace esphome
{
    namespace light
    {
        class LightState;
    }

    namespace fastcon
    {

        // Sends FastCon/brMesh packets as BLE advertisements.
        //
        // The brMesh app replaces whatever it is advertising as soon as a newer command is sent, so
        // lamps only ever see the latest state. This controller does the same per target (light ID,
        // group or broadcast): every write_state() call becomes one "submission", a newer submission
        // for the same target cancels the repeats of older ones, and targets are served round-robin
        // so one busy light cannot starve the others. Each submission is repeated `repeats` times to
        // make up for advertisements the lamps miss.
        class FastconController : public Component
        {
        public:
            FastconController() = default;

            void setup() override;
            void loop() override;
            void dump_config() override;
            float get_setup_priority() const override { return setup_priority::AFTER_BLUETOOTH; }

            std::vector<uint8_t> get_light_data(light::LightState *state);
            std::vector<uint8_t> get_white_light_data(light::LightState *state);
            std::vector<uint8_t> single_control(uint32_t addr, const std::vector<uint8_t> &light_data);

            // Scheduler keys for targets that are not a single light ID (1..255).
            static const uint32_t TARGET_BROADCAST = 0x100;
            // All group lights share one slot: the group selector is lamp-side state, so one group's
            // selector + command must never interleave with another group's.
            static const uint32_t TARGET_GROUPS = 0x101;

            // Queue an already encoded packet for `target`. Packets queued for the same target before
            // the controller's next loop() belong to the same submission and are sent in order.
            // A newer submission from the same `owner` (default: the target) replaces older ones;
            // submissions from other owners sharing the target are kept and sent once, in order.
            // `required` marks packets that must reach the lamps even if a newer submission arrives
            // before they were sent once (power-on transitions, group selectors).
            void queueCommand(uint32_t target, const std::vector<uint8_t> &data, bool required = false,
                              uint32_t owner = 0);

            void clear_queue();
            bool is_queue_empty() const
            {
                std::lock_guard<std::mutex> lock(queue_mutex_);
                return slots_.empty();
            }
            size_t get_queue_size() const;
            void set_max_queue_size(size_t size) { max_queue_size_ = size; }

            void set_mesh_key(std::array<uint8_t, 4> key) { mesh_key_ = key; }
            const std::array<uint8_t, 4> &get_mesh_key() const { return mesh_key_; }
            void set_adv_interval_min(uint16_t val) { adv_interval_min_ = val; }
            void set_adv_interval_max(uint16_t val)
            {
                adv_interval_max_ = val;
                if (adv_interval_max_ < adv_interval_min_)
                {
                    adv_interval_max_ = adv_interval_min_;
                }
            }
            void set_adv_duration(uint16_t val) { adv_duration_ = val; }
            void set_adv_gap(uint16_t val) { adv_gap_ = val; }
            void set_repeats(uint8_t val) { repeats_ = val < 1 ? 1 : val; }

            // --- pairing (fastcon_pairing.cpp) ---
            // Pairs reset lamps the way the brMesh app does: broadcast a discovery request, answer each
            // lamp that replies with its new light ID and this controller's mesh key (encrypted with
            // the key the lamp sent), and wait for the lamp's heartbeat from that ID.
            // `light_ids` are the IDs the lamps may get, in order; the caller knows which are free.
            void start_pairing(const std::vector<int32_t> &light_ids, int32_t seconds);
            void stop_pairing();
            bool is_pairing() const { return pairing_; }
            void set_pairing_sensor(text_sensor::TextSensor *sensor) { pairing_sensor_ = sensor; }
            // Called by esp32_ble for every advertisement heard while scanning.
            void gap_scan_event_handler(const esp32_ble::BLEScanResult &scan_result);
            void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param);

            // Scheduler key of the pairing packets (discovery request and replies).
            static const uint32_t TARGET_PAIRING = 0x102;

        protected:
            struct Packet
            {
                std::vector<uint8_t> data;
                bool required{false};
                bool sent{false};
            };

            struct Submission
            {
                uint32_t owner{0};
                std::vector<Packet> packets;
                size_t cursor{0};
                uint8_t rounds_left{1};
                bool first_pass_done{false};
            };

            struct Slot
            {
                uint32_t target{0};
                uint32_t tick{0};
                std::deque<Submission> submissions;
            };

            // Picks the next packet to advertise, or returns false if nothing is pending.
            bool next_packet_(std::vector<uint8_t> &out);
            bool start_advertising_(const std::vector<uint8_t> &data);

            std::vector<Slot> slots_;
            size_t next_slot_{0};
            uint32_t tick_{1};
            mutable std::mutex queue_mutex_;
            size_t max_queue_size_{100};

            enum class AdvertiseState
            {
                IDLE,
                ADVERTISING,
                GAP
            };

            AdvertiseState adv_state_{AdvertiseState::IDLE};
            uint32_t state_start_time_{0};
            uint32_t sent_count_{0};
            uint32_t dropped_count_{0};

            // Protocol implementation
            std::vector<uint8_t> generate_command(uint8_t n, uint32_t light_id_, const std::vector<uint8_t> &data, bool forward = true);
            // Same, with the body encrypted with `key` and `safe_key` in the header (pairing packets).
            std::vector<uint8_t> generate_command_with_key(uint8_t n, uint32_t light_id, const std::vector<uint8_t> &data,
                                                           bool forward, const std::array<uint8_t, 4> &key,
                                                           uint8_t safe_key);
            uint8_t sequence_{0};

            // --- pairing state ---
            struct PairingLamp
            {
                std::array<uint8_t, 6> did{};
                std::array<uint8_t, 4> key{};
                uint16_t type{0};
                uint16_t light_id{0};
                // offered: reply being sent; ok: heartbeat received; nofree: no light ID left;
                // unsupported: lamp needs the extended (new protocol) reply, not implemented.
                enum class State : uint8_t { OFFERED, OK, NO_FREE_ID, UNSUPPORTED } state{State::OFFERED};
                uint32_t last_change{0};
            };
            void pairing_loop_(uint32_t now);
            void pairing_handle_frame_(const uint8_t *frame, size_t len, bool new_protocol);
            void pairing_set_scanning_(bool on);
            void pairing_publish_();
            std::vector<PairingLamp> pairing_lamps_;
            std::deque<uint16_t> pairing_free_ids_;
            text_sensor::TextSensor *pairing_sensor_{nullptr};
            bool pairing_{false};
            bool scanning_{false};
            bool pairing_dirty_{false};
            uint32_t pairing_until_{0};
            uint32_t pairing_last_send_{0};

            std::array<uint8_t, 4> mesh_key_{};

            uint16_t adv_interval_min_{0x20};
            uint16_t adv_interval_max_{0x28};
            uint16_t adv_duration_{40};
            uint16_t adv_gap_{5};
            uint8_t repeats_{3};

            static const uint16_t MANUFACTURER_DATA_ID = 0xfff0;
        };

    } // namespace fastcon
} // namespace esphome
