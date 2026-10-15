#pragma once
namespace esphome { namespace light {
enum class ColorMode { UNKNOWN, WHITE, COLD_WARM_WHITE, RGB };
struct Values { bool is_on() const { return true; } ColorMode get_color_mode() const { return ColorMode::RGB; } float get_brightness() const { return 1; } };
struct Traits { float get_min_mireds() const { return 0; } };
class LightState { public: Values current_values; void current_values_as_rgbww(float*r,float*g,float*b,float*c,float*w,bool){*r=*g=*b=*c=*w=0;} Traits get_traits() { return {}; } };
}}
