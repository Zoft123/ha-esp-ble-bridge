// Prints what the firmware's protocol code produces, for native_compare.py.
// Input lines:  rf <addr hex> <data hex>   |   crc <addr hex> <data hex>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include "protocol.h"
#include "utils.h"

using namespace esphome::fastcon;

static std::vector<uint8_t> unhex(const std::string &s) {
  std::vector<uint8_t> out;
  for (size_t i = 0; i + 1 < s.size(); i += 2)
    out.push_back(static_cast<uint8_t>(std::stoi(s.substr(i, 2), nullptr, 16)));
  return out;
}

int main() {
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream in(line);
    std::string cmd, a, d;
    in >> cmd >> a >> d;
    if (d == "-")
      d.clear();
    if (cmd == "rf") {
      for (uint8_t b : prepare_payload(unhex(a), unhex(d)))
        printf("%02x", b);
    } else if (cmd == "crc") {
      printf("%04x", crc16(unhex(a), unhex(d)));
    }
    printf("\n");
    fflush(stdout);
  }
}
