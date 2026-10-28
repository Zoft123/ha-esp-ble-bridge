# ESP BLE Bridge for Home Assistant

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

Control BRMesh / FastCon Bluetooth lights from Home Assistant through an ESP32 running ESPHome. You set it up
from the export of the BRMesh phone app: no phone has to stay nearby, and no MQTT broker is needed.

## How it works

```
Home Assistant ──ESPHome API──▶ ESP32 (ESPHome + FastCon) ──BLE advertisements──▶ BRMesh lights (mesh)
       ▲
       └── ESP BLE Bridge add-on: imports the app export, generates/builds/installs the ESP32 firmware,
           dashboard, scenes and effects
```

- The **ESP32** sends the lights' Bluetooth commands. Each light becomes a normal light entity in Home Assistant
  (through the ESPHome integration), plus an **All Lights** entity that switches every lamp with one packet.
- The **add-on** reads the mesh key and the lights (IDs, names, types) from the BRMesh app's export, writes the
  ESPHome configuration, builds the firmware and installs it on the ESP32.

## Features

- **Import from the BRMesh app**: upload the app's export file from your phone or computer in the add-on's web
  UI. The add-on reads the mesh key and every light's ID, name and type. The app's share code and its
  `setting.xml` (mesh key only) are accepted too. Devices that are not lights (sockets, curtains, panels,
  sensors, gateways) are listed and skipped.
- **All BRMesh light types**: RGB + cold/warm white, RGBW, RGB, cold/warm white and dimmable lamps each get the
  matching controls in Home Assistant.
- **Responsive, reliable control**: the newest state for each light replaces older pending commands, every
  light gets its first packet before any repeats, and each command is repeated (configurable) so lamps that
  miss one advertisement still get it. The radio is not shared with a BLE scanner (the ESP32 only scans
  while pairing).
- **Colors as the app sends them**: brightness, RGB and cold/warm white are encoded exactly like the BRMesh
  app (verified against the app's own code), plus one-tap white and color presets in the web UI.
- **Pair new lamps from the ESP32**: reset a lamp and press **Start pairing**; the controller gives it a free
  light ID and your mesh key, the way the app does, and the lamp is added to the light list.
- **No surprises on reboot**: restarting the ESP32 does not switch any lamps.
- **Build and install from the add-on**: firmware is compiled in the add-on with the bundled FastCon
  components and installed on the ESP32 you choose: over USB on the Home Assistant machine, from the computer
  you are browsing on, or over Wi-Fi, with live build output.
- **Several controllers**: every ESP32 can reach every light through the mesh; you can also give each
  controller its own subset of lights (for coverage, or for large meshes; one ESP32 comfortably handles about
  100 lights).
- **Scenes and effects**, paced to what Bluetooth can deliver (stop them any time, or just switch a light off),
  an **Identify** button that blinks a lamp, and a **map view** to place lights and controllers.

## Requirements

- Home Assistant OS or Supervised on a **64-bit** machine (amd64 or aarch64, e.g. Raspberry Pi 4/5).
- An **ESP32** with Bluetooth: ESP32 (WROOM/WROVER dev boards), ESP32-S3, ESP32-C3 or ESP32-C6.
  The ESP32-S2 (no Bluetooth) and ESP32-H2 (no Wi-Fi) cannot be used.
- Your lights paired in the **BRMesh** app, and its export file.
- The **ESPHome** integration in Home Assistant (it is offered automatically once the ESP32 is online).

## Installation

1. Add this repository to Home Assistant:

   [![Add Repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FZoft123%2Fha-esp-ble-bridge)

   Or manually: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add
   `https://github.com/Zoft123/ha-esp-ble-bridge`.
2. Install **ESP BLE Bridge** and start it. The first install builds the add-on image on your machine and takes
   a few minutes.
3. Open the add-on's web UI.

## Setup

1. **Export from the BRMesh app.** Pair your lights in the app as usual, then use the app's export. It creates a
   file named `BRMESH_BACKUP…` (you can rename it).
2. **Import it.** In the add-on, click **📱 Import from BRMesh App** and choose the file. Check the preview (number
   of lights, types, skipped devices) and click **Import**.
3. **Add a controller.** In **Controllers**, create a controller: pick your board and Wi-Fi network.
4. **Install the firmware.** Click **⚡ Install** on the controller card and choose the ESP32 to install on:
   - **USB on the Home Assistant machine**: plug the ESP32 into the HA machine; it is listed by its USB adapter
     name (click Refresh after plugging it in).
   - **This computer**: plug the ESP32 into the computer you are browsing from. In Chrome or Edge with Home
     Assistant opened over HTTPS, the page flashes it directly; otherwise download the firmware and flash it
     with [web.esphome.io](https://web.esphome.io).
   - **Wi-Fi (OTA)**: for an ESP32 that already runs this firmware (later updates).

   The first build downloads the ESP32 toolchain and takes 5-15 minutes; later builds are much faster. The add-on
   rebuilds automatically when the lights or settings changed since the last build.
5. **Add it to Home Assistant.** Under **Settings → Devices & Services**, add the discovered ESPHome device
   (the API key is in `/config/esphome/secrets.yaml`). Your lights appear as light entities.

**Pairing new lamps without the app:** in the **Pairing** tab, pick a controller, reset the lamps (as for the
BRMesh app) and click **Start pairing**. Paired lamps are added to the light list; click **⚡ Install** on each
controller afterwards. Lamps paired this way do not appear in the BRMesh app.

When you add, rename or remove lights later, import a new export, pair them, or edit the lights in the add-on,
then click **⚡ Install** on each controller again.

## Settings

- **Advertising time per packet / repeats per command**: raise these if lights far from the ESP32 miss
  commands; lower them for faster response with many lights changing at once.
- **"All Lights" entity**: one Bluetooth packet for all lamps.
- **ESP32 web server**: off by default; it costs memory and CPU on the ESP32.
- To manage a controller's ESPHome YAML yourself, add a line reading exactly `# manual_config: true`; the
  add-on then leaves that file alone.

## Limitations

- **Pairing from the ESP32 covers lamps with light IDs 1-255.** Lamps that answer with the app's extended
  protocol (gateways, very large meshes) are listed but not paired. The BRMesh app does not see lamps paired
  by the add-on, and does not know the IDs the add-on handed out: if you also pair with the app, import its
  export first.
- **One mesh per add-on.** The add-on uses one mesh key (the app's normal and "radar" meshes have separate keys;
  you choose which to import).
- **Light IDs 1-255.** Lights above 255 use the app's extended addressing, which is not supported yet; they are
  listed as skipped on import.
- **No state feedback.** BRMesh lights do not report their state, so Home Assistant shows the last state it
  sent.
- Colors through the **All Lights** entity are verified on RGB + cold/warm white lamps. With mixed lamp types,
  the add-on sends colors light by light and uses All Lights for on/off and brightness.

## Troubleshooting

- **Build fails with "Secret ... not defined"**: open **Settings** in the add-on and save, or re-import; the
  add-on writes the needed secrets to `/config/esphome/secrets.yaml`.
- **First install over Wi-Fi fails**: a controller that ran older (Arduino-based) firmware needs one install
  over USB.
- **A light shows "not in Home Assistant yet"**: install the firmware on a controller that has the light and add
  the ESPHome device in Home Assistant.
- **Build output**: **Controllers → 📋 Output**, or the add-on log.

## Development

- `rootfs/app/`: the add-on (Flask web UI, importer, ESPHome generator and builder).
- `esphome_components/`: the ESPHome components installed to `/config/esphome/esp_ble_bridge_components`.
- Tests: `python3 -m unittest discover tests` (importer and generator) and `tests/fastcon_scheduler/run.sh`
  (the ESP32 command scheduler, built with the host compiler).

See the [changelog](CHANGELOG.md) for what changed in each version.

## Credits

- **Chris Valentino** ([@tofuweasel](https://github.com/tofuweasel)) created ESP BLE Bridge:
  [tofuweasel/ha-esp-ble-bridge](https://github.com/tofuweasel/ha-esp-ble-bridge). This project continues his
  work, and much of the web UI is still his. Thank you, Chris!
- The ESPHome components build on [esphome-fastcon](https://github.com/dennispg/esphome-fastcon) by
  **Dennis George** ([@dennispg](https://github.com/dennispg)), via the forks of
  [@brett-todd](https://github.com/brett-todd/esphome-fastcon) and
  [@scross01](https://github.com/scross01/esphome-fastcon).

## License

MIT for this project's own code: see [LICENSE](LICENSE). Parts by other authors keep their own terms; in
particular the original project by Chris Valentino was published without a license, so his code that remains
here is not covered by the MIT license. [NOTICE.md](NOTICE.md) lists who wrote what.

## Issues

Report problems and ideas in the [issue tracker](https://github.com/Zoft123/ha-esp-ble-bridge/issues).
Maintained by [@Zoft123](https://github.com/Zoft123).

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
