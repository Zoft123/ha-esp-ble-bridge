#!/bin/bash
# Host-side tests of the FastCon controller (stubbed ESPHome/BLE headers): the command scheduler,
# the light state encoding and pairing.
set -e
cd "$(dirname "$0")"
SRC=../../esphome_components/fastcon
FLAGS="-std=gnu++17 -Wall -Wextra -Wno-unused-parameter -Istub -I$SRC"
LIB="$SRC/fastcon_controller.cpp $SRC/fastcon_pairing.cpp $SRC/protocol.cpp $SRC/utils.cpp"
g++ $FLAGS scheduler_test.cpp $LIB -o /tmp/fastcon_scheduler_test
/tmp/fastcon_scheduler_test
g++ $FLAGS pairing_test.cpp $LIB -o /tmp/fastcon_pairing_test
/tmp/fastcon_pairing_test
