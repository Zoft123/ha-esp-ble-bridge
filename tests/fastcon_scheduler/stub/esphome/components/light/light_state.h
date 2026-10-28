#pragma once
#include "light_color_values.h"
namespace esphome { namespace light {
struct Traits { float get_min_mireds() const { return 0; } };
class LightState { public: LightColorValues current_values; Traits get_traits() { return {}; } };
}}
