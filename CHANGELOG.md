# Changelog

What changed in each version of ESP BLE Bridge, newest first. Versions follow [Semantic Versioning](https://semver.org).
Releases before 0.60.0 belong to the original project by Chris Valentino; their history is in
[tofuweasel/ha-esp-ble-bridge](https://github.com/tofuweasel/ha-esp-ble-bridge).

## [0.62.0] - 2026-10-28

Nothing from the original project's code is left: the web UI and every file that still contained it were
rewritten or removed. See [NOTICE.md](NOTICE.md).

### Changed
- **New web UI**, written from scratch: the same features in a lighter page (about 1,800 lines instead of
  4,600) with light and dark themes and a layout that works on phones. Lights can be renamed from their card.
  The map lists lights and controllers that are not placed yet; click one to put it on the map.
- The add-on's Python code (web API, ESPHome generator, builder, bridge) was reorganized; the generated
  ESPHome YAML is unchanged.
- Christmas and Halloween effects color neighbouring lights differently and shift the colors along.
- A GitHub workflow runs the tests on every push.

### Removed
- NSPanel UI integration (it came from the original project and was never tested with this version).
- The one-time cleanup of the original version's MQTT entities (it could not run: the MQTT client library was
  not installed), the add-on's MQTT service request, and the migration from the original version's storage
  (`options.json`, Home Assistant's `secrets.yaml`).
- API endpoints only the old web UI used: `firmware/check`, `esphome/devices`, `esphome/status`,
  `esphome/install`, `diagnostics/*`, `controllers/<id>/signal`, `map/satellite`, `lights/<id>/reset`,
  `import/app`, and `DELETE controllers/<id>` (use `POST controllers/<name>/reset`).
- `hacs.json` (HACS does not install add-ons), the unused Docker image publishing workflow and leftover
  files of the original project.

## [0.61.2] - 2026-10-28

### Fixed
- Firmware download and "built" status found no firmware after a successful build: ESPHome 2026 writes
  the images to `.esphome/build/<controller>/build/` (native ESP-IDF build) instead of PlatformIO's
  `.pioenvs/<controller>/`.

## [0.61.1] - 2026-10-28

### Changed
- **ESPHome 2026.10.1** builds the controller firmware (was 2025.10.3), on a Debian trixie base image
  (Python 3.13). The pairing scan uses ESPHome's current BLE event API, so 0.61.0 could not build its own
  firmware. The first build after updating downloads the newer ESP32 toolchain and takes longer.

## [0.61.0] - 2026-10-28

Controller firmware 2.1.0: install it on every controller (Controllers tab) after updating.

### Added
- **Pairing reset lamps from the ESP32** (Pairing tab). Reverse engineered from the brMesh app and its
  native library: the controller broadcasts the app's discovery request, answers each reset lamp with a
  free light ID and your mesh key (encrypted with the key the lamp announces), and confirms the pairing by
  the lamp's heartbeat. Confirmed lamps are added to the Lights tab with their type. The controller scans
  only while pairing (2 minutes), so day-to-day commands keep the radio to themselves. Lamps that answer
  with the app's extended protocol (light IDs above 255, gateways) are listed as not supported yet.
- **White and color presets.** Every light card has one-tap whites (Cool White 6500 K, Daylight, Neutral
  White, Warm White 2700 K, Candle 2000 K), and the color dialog and a new "All lights" bar on the Lights
  tab have every preset (whites plus red, orange, yellow, green, cyan, blue, purple, pink). Whites use the
  lamp's white LEDs: color temperature on CW/WW lamps, the white channel on RGBW lamps, mixed RGB only on
  RGB-only lamps. Before this, a lamp set to a color from the web UI could not be put back to white there.
- **Stopping effects.** The Effects tab lists running effects with a Stop button each and a Stop all button,
  and a "⏹ Stop effects" button appears in the header whenever an effect runs. Light cards show which
  effect a light is in.
- 🔦 **Identify** on each light card blinks the lamp three times and restores its previous state.

### Fixed
- **Colors and brightness now match the brMesh app** (checked against the app's own code and native
  library). The firmware put the brightness into every color channel *and* into the brightness byte, so
  lamps were dimmed twice: 50 % in Home Assistant gave about 25 %, and low brightness lost color precision.
  The color bytes now carry only the color, as the app sends them. Color temperature is split the way the
  app does (warm + cold = 255); before, a mid temperature drove both white LED strings at full power.
  RGB-only lamps get the app's RGB white (85/85/85). This applies to single lights, "All Lights" and groups.
- **Effects could not be stopped and switched lights back on.** Nothing in the UI called the stop endpoint,
  so an effect ran until the add-on restarted, and every step of an effect sent "on", which undid turning
  the light off in Home Assistant. Now an effect gives up a light when you set it on the Lights tab, apply a
  scene to it, or switch it (or "All Lights") off anywhere in Home Assistant. An effect left without lights
  stops.
- The light card's On/Off button no longer resends the lamp's RGB color when switching it on, which forced
  lamps out of white mode.
- Scenes saved colors as `{r, g, b}` objects that could not be applied; old scenes are read either way.
  Effect scenes listed effects that do not exist and did nothing when applied; they now start the effect.
  Scenes can be deleted.

### Removed
- The old scan/pairing UI and its endpoints (`/api/scan`, `/api/pairing/discover`, `/api/pairing/pair`,
  `/api/pairing/ignore`, `/api/control/send`), which never worked, and the log filters for pairing/BLE scan.
  Pairing now lives in the Pairing tab (see Added).

## [0.60.1] - 2026-10-15

### Added
- **Choose the ESP32 to install on**: ⚡ Install opens a dialog with the USB serial devices on the Home
  Assistant machine (by adapter name, with Refresh), **this computer** (flashes the ESP32 plugged into the
  computer you are browsing from with ESP Web Tools; without HTTPS it offers the firmware download for
  web.esphome.io) and Wi-Fi (OTA). Previously Install silently fell back to OTA when no USB device was found.
- Firmware download (factory image) and ESP Web Tools manifest endpoints.

### Fixed
- Install never flashes a firmware that is older than the controller's YAML, secrets or components; it
  rebuilds first.

### Removed
- The "Firmware Management / Check for Updates" card, the signal strength matrix and the "Show Signal
  Strength" map option (they showed version 1.1 of the old firmware and mock data), and the outdated text in
  the new-controller dialog.

## [0.60.0] - 2026-10-15

### Added
- **Upload your BRMesh export from the browser.** "Import from BRMesh App" opens a dialog where you pick
  the app's export (`BRMESH_BACKUP…`, any file name) from your phone or computer. The add-on decrypts it,
  shows the lights it found and imports the mesh key, every light ID, name and type (RGB+CW/WW vs RGBW).
  The app's share code and `setting.xml` are accepted too. Nothing has to be copied into `/share` any more
  (that path was never mapped into the add-on, so the old import could not work).
- **"All Lights" entity**: one BLE broadcast packet switches every lamp, instead of one packet per lamp.
- Build and install firmware from the controller card (⚡ Install over USB or Wi-Fi); progress and output
  are shown while it runs.
- **All brMesh lamp types**: the importer knows every light type of the app and maps it to RGB+CW/WW, RGBW,
  RGB, CW/WW white or dimmable; the firmware's `light_type` option gives each the right controls (CW/WW
  lamps get color temperature, dimmable lamps brightness only). Sockets, curtains, panels, sensors and
  gateways in the export are listed as skipped instead of becoming fake lights. Unknown light types are
  imported as full-color lamps and marked as assumed.
- **Lights per controller**: choose which lights each ESP32 handles (default: all). Useful with several
  controllers, and for large meshes: the UI warns above 100 lights on one controller (~350 bytes of heap
  per light entity on the ESP32).
- **Board choice** when creating a controller (ESP32, S3, C3, C6).
- Exports with the app's second ("radar") mesh can import either mesh.

### Changed
- **Bundled FastCon ESPHome components** (based on scross01/esphome-fastcon, MIT) are installed to
  `/config/esphome/esp_ble_bridge_components`. Builds no longer clone a GitHub fork.
- **New command scheduler on the ESP32**: the newest state for a light replaces older pending ones, lights
  are served round-robin, and every command is repeated (default 3x) so lamps that miss one advertisement
  still get it. Previously every change (including each frame of a 1 s transition) was queued FIFO and
  advertised once for 50 ms, so commands piled up and lamps missed them.
- Duplicate light names (common in app exports) get the light ID appended; ESPHome rejects duplicates.
- Light IDs above 255 (the app's extended addressing) are listed as not supported instead of being dropped.
- The scheduler sends every light's first transmission before any repeats, so the time until the last
  lamp reacts grows with one packet per changed light; broadcast and group commands use their own queue
  keys and groups never interleave with each other.
- Generated firmware: no BLE scanner or GATT server (they shared the radio with the command advertisements),
  INFO logging, 0 s transitions, ESP-IDF framework, no web server by default.
  *Switching an existing controller from Arduino to ESP-IDF may need one install over USB.*
- Lights no longer switch when an ESP32 reboots (the restored state is not sent).
- The web UI controls lights through their ESPHome entities in Home Assistant. Effects and scenes work again,
  paced to what BLE can deliver.
- State is stored in `/data/brmesh_state.json`; existing lights/controllers are migrated from `options.json`
  (which the Supervisor overwrites when add-on options are saved).
- Secrets are written to `/config/esphome/secrets.yaml`, where ESPHome looks for them (the mesh key used to
  end up only in `/config/secrets.yaml`, so builds failed with an undefined secret).
- MQTT is optional. The fake MQTT lights from older versions (they never sent anything) are removed once.
- The web UI polls far less (no GitHub request every 5 seconds, no mDNS probes per refresh).

### Removed
- BLE scanning and pairing from the ESP32. It read a `/logs` page ESPHome does not serve, and reset lamps
  only answer after the app's scan request; the pairing reply is built by the app's native library, which
  has not been reverse engineered. Pair lamps with the BRMesh app and import the export instead.
- Diagnostics endpoints that returned the API encryption key and secrets file to the browser.
