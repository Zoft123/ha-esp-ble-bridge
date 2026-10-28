#pragma once
#include <string>
namespace esphome { namespace text_sensor {
class TextSensor { public: std::string state; void publish_state(const std::string &s) { state = s; } };
}}
