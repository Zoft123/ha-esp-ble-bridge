#pragma once
#include <cstdint>
namespace esphome { extern uint32_t g_now; inline uint32_t millis() { return g_now; } }
