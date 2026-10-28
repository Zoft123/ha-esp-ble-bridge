# Changelog

All notable changes to the ESP BLE Bridge add-on will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

## [0.50.10] - 2025-11-29

### Added
- **Enhanced Device Scanner** - Now scans all BLE devices and categorizes them into "Ready to Pair" (BRMesh devices in pairing mode) and "Other Devices".
- **Ignore Functionality** - Added ability to ignore specific devices from future scans to reduce noise.
- **Device Info** - Added "Info" button to view detailed technical data (Manufacturer ID, RSSI, Raw Data) for any discovered device.

### Fixed
- **Ingress Compatibility** - Fixed absolute path issue in API calls that caused errors when accessing via Home Assistant Ingress.

## [0.50.9] - 2025-11-29

### Changed
- **Prevent Config Overwrite** - The addon no longer automatically overwrites existing ESPHome configuration files on startup.
- **Manual Config Protection** - Added `# manual_config: true` flag support to prevent any overwrite of specific files.
- **Explicit Regeneration** - "Regenerate YAML" button in Web UI is now required to apply configuration changes to ESPHome files.
- **Update Notifications** - Startup logs now indicate if updates are available instead of silently applying them.

## [0.50.8] - 2025-11-28

### Added
- Added `wifi_domain` configuration option to support custom DNS search domains.
- Updated ESPHome generator to inject `wifi_domain` into firmware configurations.

## [0.28.0] - 2025-11-26

### 🎉 MAJOR FEATURE: Native BRMesh Pairing (No Android App!)

Native implementation of BRMesh device pairing without requiring the Android app. Complete protocol analysis enabling direct device pairing.

### Added

#### Protocol Implementations
- **brmesh_pairing.py** - Complete pairing protocol (12/18-byte responses)
  - `create_pairing_response()` - Generate pairing responses for new devices
  - `package_disc_res()` - Standard 12-byte pairing format
  - `package_disc_res2()` - Extended 18-byte pairing format
  - Pure Python implementation, no external crypto libraries needed
  
- **brmesh_control.py** - Complete control protocol with encryption
  - `create_control_command()` - Generate encrypted control commands
  - `decode_control_command()` - Decrypt received commands
  - `package_ble_fastcon_body()` - Full encryption with checksum
  - `package_ble_fastcon_body_with_header()` - Header encryption variant
  - `package_ble_fastcon_body_without_encrty()` - Unencrypted commands
  - XOR-based encryption with 4-byte repeating mesh key
  - Magic constant: 0xc47b365e for header checksum

#### API Endpoints
- `GET /api/pairing/discover` - Discover unpaired BRMesh devices in pairing mode
- `POST /api/pairing/pair` - Pair a device without Android app
  - Auto-generates pairing response (12 or 18 bytes)
  - Adds device to lights configuration
  - Configurable address, group_id, mesh_key
- `POST /api/control/send` - Send encrypted control commands
  - Supports on/off/brightness/color commands
  - Auto-encrypts with mesh key
  - Command types: status(0), control(1), pairing(2), group(3), scene(4)

#### UI - New "🔗 Pairing" Tab
- **Step-by-step pairing instructions** - Built-in guide for factory reset and pairing
- **Device discovery** - Scan for unpaired BRMesh devices
- **Device list** - Shows discovered devices with MAC address, RSSI, manufacturer
- **Pairing settings** - Configure device address (1-255), group ID, mesh key
- **One-click pairing** - Pair discovered devices with single button click
- **Test controls** - Test ON/OFF/Brightness commands to verify pairing
- **Status messages** - Real-time feedback (info/success/warning/error)
- **Auto-increment address** - Automatically increments address after pairing
- **Dark mode support** - Full styling for light and dark themes

### Changed
- Updated version to v0.28.0 (major feature release)
- Enhanced addon description to highlight native pairing capability
- Added cache busting for CSS (v0.28.0)

### Technical Details

#### Pairing Protocol
- **12-byte format**: Device MAC (6) + Address (1) + Constant (1) + Mesh Key (4)
- **18-byte format**: Same + Group ID (1) + Padding (5)
- No encryption needed - breakthrough simplification

#### Control Protocol  
- **24-byte commands** (typical): 4-byte header + 20-byte payload
- Header: `retry|cmd_type|forward` + sequence + mesh_byte + checksum
- Checksum XORed with magic constant: 0xc47b365e
- Payload XORed with 4-byte mesh key (repeating pattern)

### Documentation
- **BRMESH_PROTOCOL_COMPLETE.md** - Complete protocol documentation
- **PAIRING_INTEGRATION_COMPLETE.md** - Integration guide and testing checklist

### Innovation
This open-source implementation enables native BRMesh pairing:
- No Android app required for initial pairing
- Complete Python implementation available
- Community-contributed protocol analysis

### Next Steps
- ESP32 BLE scan implementation for device discovery
- ESP32 BLE write implementation for pairing/control transmission
- ESPHome YAML integration for pairing support
- Testing with factory-reset devices

## [0.19.0] - 2025-11-25

### 🔄 Reset & Recovery Package

Complete reset and recovery functionality for lights, controllers, and system configuration.

### Added

#### Reset Operations
- **Factory Reset Light** - Send BLE command to clear pairing data and return light to pairing mode
- **Remove Light (Unpair)** - Remove light from Home Assistant without factory reset
- **Reset Controller** - Remove ESP32 controller from configuration and delete YAML files
- **Full System Reset** - Danger zone feature to remove all lights, controllers, scenes, and effects

#### API Endpoints
- `POST /api/lights/<id>/reset` - Factory reset a specific light
- `POST /api/lights/<id>/unpair` - Remove light from system (preserves mesh pairing)
- `POST /api/controllers/<name>/reset` - Reset controller configuration
- `POST /api/system/reset` - Full system reset (requires double confirmation)

#### UI Controls
- **Reset Button** on each light card in Lights tab
- **Remove Button** on each light card for quick removal
- **Reset Button** on each controller card in Controllers tab
- **Danger Zone** section in Settings tab with Full System Reset
- **Confirmation Dialogs** with clear warnings for destructive operations

#### Documentation
- **RESET_GUIDE.md** - Comprehensive 50+ page reset and recovery guide
  - Factory reset procedures
  - Recovery from accidental deletions
  - Troubleshooting common issues
  - API reference
  - Best practices
  - Common scenarios

### Changed
- Updated version banner to v0.19.0
- Enhanced light card UI with reset/remove actions
- Enhanced controller card UI with reset action
- Improved error handling for reset operations

### Technical Details
- BLE factory reset command: `0xF0 0xFF <light_id> 0x00 0x00 0x00 0x00 0x00`
- MQTT discovery unpublish support (empty payload to remove entities)
- ESPHome YAML file cleanup on controller reset
- Preserves mesh key and MQTT settings during full system reset

### Security
- Double confirmation required for system reset (button + text input "RESET")
- Warning dialogs explain consequences of each action
- Backup recommendations before destructive operations

## [0.18.3] - 2025-11-25

### Added
- ESPHome device picker in "Add Existing Controller" modal
- Controller status badges (Config, Online, Build Ready)
- Persisted esphome_path in controller configuration

## [0.9.0] - 2025-11-24

### 🎉 Beta Release: GUI Configuration & Phone-Free Operation

This is a complete rewrite of the BRMesh Bridge add-on with a focus on user experience and automation.

### Added

#### Web UI & Configuration
- **Complete GUI Configuration** - All settings configurable through web interface, no manual file editing required
- **Settings Tab** - Dedicated interface for mesh key, MQTT, map, and feature configuration
- **Auto-detect Home Assistant Location** - Automatically uses your HA home location for map view
- **Import/Export Configuration** - Backup and restore your complete setup as JSON

#### Light Management
- **BLE Device Discovery** - Automatically scan for and register new lights without phone
- **Phone-Free Operation** - Add multiple lights without ever touching the Android app
- **Dynamic Light Management** - Add, remove, and rename lights without recompiling ESP32 firmware
- **BRMesh App Sync** - Import device names from Android app via ADB or JSON export

#### Visual & Control
- **Interactive Map View** - Visualize and place lights on satellite imagery (ESRI ArcGIS)
- **8 Built-in Effects** - Rainbow, color loop, twinkle, fire, Christmas, Halloween, strobe, breathe
- **Scene Management** - Create and activate multi-light scenes through GUI
- **Signal Strength Matrix** - Monitor controller-to-light signal strength
- **Drag-and-Drop Placement** - Position lights on map with mouse

#### Advanced Features
- **ESPHome Config Generator** - Automatically generate ESP32 controller configs from HA settings
- **Multi-Controller Support** - Manage multiple ESP32 controllers with automatic light assignment
- **NSPanel Integration** - Optional Nextion display touch interface generation
- **MQTT Auto-Discovery** - Automatic Home Assistant entity creation
- **REST API** - Complete API for programmatic control and integration

#### Developer Experience
- **HACS Compatible** - Ready for Home Assistant Community Store installation
- **Docker Multi-Arch** - Support for ARM, ARM64, AMD64, i386 architectures
- **Comprehensive Documentation** - Quick start, GUI guide, setup guide, deployment guide
- **GitHub Actions** - Automated Docker image building and publishing

### Changed
- **Complete UI Rewrite** - Modern web interface with responsive design
- **Configuration Approach** - GUI-first instead of YAML-first
- **MQTT Integration** - Auto-detects Home Assistant's Mosquitto service by default
- **Map Tiles** - Switched to ESRI ArcGIS (free, no API key required) from OpenStreetMap

### Fixed
- Mesh key now properly sanitized in documentation
- IP addresses and personal information removed from examples
- Default configuration values no longer contain user-specific data

### Security
- All secrets and personal information removed from documentation
- Examples use placeholder values
- No hardcoded credentials or mesh keys in code

### Credits
- **[@scross01](https://github.com/scross01)** - Creator and maintainer of [esphome-fastcon](https://github.com/scross01/esphome-fastcon) ESPHome component
- **[Mooody](https://mooody.me/)** - Original Fastcon BLE protocol analysis
- **[ArcadeMachinist](https://github.com/ArcadeMachinist)** - brMeshMQTT reference implementation
- **Home Assistant Community** - Testing, feedback, and support

### Breaking Changes
- Configuration format has changed - migration from v1.x requires reconfiguration
- Web UI port is now 8099 (was not configurable before)
- MQTT topic structure may have changed for some devices

### Migration Notes
From v1.x to v0.9.0:
1. Export your light IDs and names from old config
2. Install v2.0.0
3. Use Settings tab to reconfigure mesh key and MQTT
4. Use "Add Light" in GUI to recreate your lights
5. Test each light before removing old config

---

## [1.0.0] - 2025-03-XX (Historical)

### Added
- Initial release
- Basic BRMesh light control via MQTT
- Manual light configuration
- Single ESP32 controller support
- Basic on/off and color control

### Known Limitations
- Required manual YAML editing
- No BLE discovery
- No web UI
- Single controller only
- Manual light ID discovery via ADB

---

## Future Releases

### [1.0.0] - Planned
- Full stable release
- Production-ready
- Performance optimizations

### [1.1.0] - Planned
- **Automation Templates** - Pre-built automation examples
- **Grouping** - Create light groups through GUI
- **Effect Builder** - Create custom lighting effects
- **Backup/Restore** - One-click backup to cloud services
- **Mobile App** - Companion mobile app for quick control

### [2.2.0] - Planned
- **Voice Control** - Alexa and Google Home integration
- **Schedules** - Time-based lighting schedules
- **Sunrise/Sunset** - Automatic timing based on location
- **Energy Monitoring** - Track estimated power usage
- **Multi-Language** - i18n support for web UI

### Contributions Welcome!
Have an idea? Submit a feature request or pull request on GitHub!

---

## Version Numbering

- **Major (X.0.0)** - Breaking changes, major new features
- **Minor (x.X.0)** - New features, backwards compatible
- **Patch (x.x.X)** - Bug fixes, minor improvements

---

[0.9.0]: https://github.com/YOUR_USERNAME/ha-brmesh-bridge/releases/tag/v0.9.0
[1.0.0]: https://github.com/YOUR_USERNAME/ha-brmesh-bridge/releases/tag/v1.0.0
