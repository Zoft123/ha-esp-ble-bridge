# ESP BLE Bridge Home Assistant Add-on Repository

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]
![Supports armhf Architecture][armhf-shield]
![Supports armv7 Architecture][armv7-shield]
![Supports i386 Architecture][i386-shield]

Bridge Fastcon/BRMesh protocol BLE lights to Home Assistant through ESP32 controllers, set up from your BRMesh app export.

## About

This repository contains the ESP BLE Bridge add-on for Home Assistant. An ESP32 running ESPHome sends the
BRMesh/FastCon BLE commands; the add-on sets it up and gives you a dashboard:

- **Import from the BRMesh app** - upload the app's export file from your phone or computer; the mesh key,
  light IDs, names and types are read from it
- **ESPHome generation and install** - builds the controller firmware with bundled FastCon components and
  installs it over USB or Wi-Fi
- **Fast, reliable control** - newest-state-wins command scheduling with repeats, an "All Lights" broadcast
  entity, no BLE scanning competing with the commands
- **Effects and scenes** - paced to what BLE can deliver
- **Map View** - place lights and controllers on a map
- **Multi-Controller** - every controller can reach every light through the mesh

Lights are paired with the BRMesh app; pairing from the ESP32 is not supported.

## Add-ons

This repository contains the following add-on:

### [ESP BLE Bridge](./esp-ble-bridge)

Control Fastcon/BRMesh protocol BLE lights through Home Assistant with advanced features and GUI configuration.

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]
![Supports armhf Architecture][armhf-shield]
![Supports armv7 Architecture][armv7-shield]
![Supports i386 Architecture][i386-shield]

## Installation

1. Click the button below to add this repository to Home Assistant:

   [![Add Repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https://github.com/tofuweasel/ha-esp-ble-bridge)

   Or manually:
   - Go to **Settings** → **Add-ons** → **Add-on Store**
   - Click **⋮** (menu) → **Repositories**
   - Add: `https://github.com/tofuweasel/ha-esp-ble-bridge`
   - Click **Add** → **Close**

2. Find "ESP BLE Bridge" in the add-on store and click **Install**

3. Start the add-on and open the Web UI to configure

## Documentation

- [Quick Start Guide](./brmesh-bridge/QUICK_START.md) - Get started in 5 minutes
- [GUI Configuration Guide](./brmesh-bridge/GUI_CONFIGURATION.md) - Complete configuration reference
- [Setup Guide](./brmesh-bridge/SETUP_GUIDE.md) - Detailed setup instructions
- [Changelog](./brmesh-bridge/CHANGELOG.md) - Version history

## Credits

This add-on uses the [esphome-fastcon](https://github.com/scross01/esphome-fastcon) component by [@scross01](https://github.com/scross01) for ESPHome integration with BRMesh/Fastcon protocol lights.

## Support

If you find this add-on useful, please consider starring the repository! ⭐

### Support Development

This project is developed and maintained in my spare time. If you'd like to support continued development and new features:

<a href="https://www.buymeacoffee.com/tofuweasel" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 60px !important;width: 217px !important;" ></a>

[![Donate](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://paypal.me/tofuweasel)
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/tofuweasel)

Every contribution helps keep the lights on (literally! 💡) and enables me to:
- Add new features
- Fix bugs faster
- Test with more hardware
- Write better documentation
- Create video tutorials

### Issues & Feature Requests

For issues and feature requests, please use the [GitHub issue tracker](https://github.com/tofuweasel/ha-brmesh-bridge/issues).

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[armhf-shield]: https://img.shields.io/badge/armhf-yes-green.svg
[armv7-shield]: https://img.shields.io/badge/armv7-yes-green.svg
[i386-shield]: https://img.shields.io/badge/i386-yes-green.svg
