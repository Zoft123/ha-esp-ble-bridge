# Authorship and licensing

ESP BLE Bridge combines work by several people, under different terms. The [MIT license](LICENSE) covers
the work of Zoft123; the parts below that come from other authors keep their own terms.

## Original project: Chris Valentino (@tofuweasel)

ESP BLE Bridge was created by **Chris Valentino** ([@tofuweasel](https://github.com/tofuweasel)) as
[tofuweasel/ha-esp-ble-bridge](https://github.com/tofuweasel/ha-esp-ble-bridge) (November 2025, up to
version 0.50.11). This repository continues that project; its history up to 0.50.11 is his work.

The original repository was published **without a license**. Code and files written by Chris Valentino that
remain here are © Chris Valentino, and the MIT license in [LICENSE](LICENSE) does not apply to them. This
includes much of the web UI (`rootfs/app/static/`, `rootfs/app/templates/index.html`),
`rootfs/app/nspanel_ui.py`, and the research scripts in the repository root. `git blame` shows the author
of every line.

## FastCon ESPHome components

The components in `esphome_components/` are based on
[esphome-fastcon](https://github.com/dennispg/esphome-fastcon) by **Dennis George**
([@dennispg](https://github.com/dennispg)), by way of the forks of
[@brett-todd](https://github.com/brett-todd/esphome-fastcon) and
[@scross01](https://github.com/scross01/esphome-fastcon). MIT License, Copyright (c) 2025 Dennis George: see
[esphome_components/LICENSE.fastcon](esphome_components/LICENSE.fastcon). Changes made here (command
scheduler, pairing, light state encoding, broadcast and group fixes) are by Zoft123 under the same terms.

## Zoft123

Everything added or rewritten by Zoft123 from version 0.60.0 on (BRMesh export importer, ESPHome generator
and builder, Home Assistant control, pairing, presets, effects engine, tests): MIT, see [LICENSE](LICENSE).

## BRMesh protocol

The BRMesh/FastCon protocol details (packet encoding, pairing) were worked out from the BRMesh Android app
for interoperability. No code or binaries from the app are included in this repository.
