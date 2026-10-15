#pragma once

#include <array>
#include <deque>
#include <mutex>
#include <vector>
#include <esp_gap_ble_api.h>
#include "esphome/core/component.h"

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
