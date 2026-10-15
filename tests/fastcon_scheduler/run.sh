#!/bin/bash
# Host-side test of the FastCon controller's command scheduler (stubbed ESPHome/BLE headers).
set -e
cd "$(dirname "$0")"
SRC=../../esphome_components/fastcon
g++ -std=gnu++17 -Wall -Wextra -Wno-unused-parameter -Istub -I"$SRC" scheduler_test.cpp \
    "$SRC/fastcon_controller.cpp" "$SRC/protocol.cpp" "$SRC/utils.cpp" -o /tmp/fastcon_scheduler_test
/tmp/fastcon_scheduler_test
