#pragma once
namespace esphome { namespace setup_priority { const float AFTER_BLUETOOTH = 0; }
class Component { public: virtual void setup() {} virtual void loop() {} virtual void dump_config() {} virtual float get_setup_priority() const { return 0; } virtual ~Component() = default; }; }
