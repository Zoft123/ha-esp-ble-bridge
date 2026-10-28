# Authorship and licensing

ESP BLE Bridge is licensed under the [MIT license](LICENSE), Copyright (c) 2026 Zoft123, with the exception of
the FastCon component code described below, which carries its own MIT license.

## Origins: Chris Valentino (@tofuweasel)

This project grew out of ESP BLE Bridge by **Chris Valentino**
([@tofuweasel](https://github.com/tofuweasel)), published as
[tofuweasel/ha-esp-ble-bridge](https://github.com/tofuweasel/ha-esp-ble-bridge) (November 2025, up to
version 0.50.11) without a license. Credit for the original idea and project is his.

Since version 0.62.0 none of his code or files are part of this repository's current version: the web UI,
the add-on code and the repository files were rewritten or removed, and his standalone research scripts were
removed. His work remains available in his repository, and in this repository's git history up to version
0.50.11.

## FastCon ESPHome components

The components in `esphome_components/` are based on
[esphome-fastcon](https://github.com/dennispg/esphome-fastcon) by **Dennis George**
([@dennispg](https://github.com/dennispg)), by way of the forks of
[@brett-todd](https://github.com/brett-todd/esphome-fastcon) and
[@scross01](https://github.com/scross01/esphome-fastcon). MIT License, Copyright (c) 2025 Dennis George: see
[esphome_components/LICENSE.fastcon](esphome_components/LICENSE.fastcon). Changes made here (command
scheduler, pairing, light state encoding, broadcast and group fixes) are by Zoft123 under the same terms.

## BRMesh protocol

The BRMesh/FastCon protocol details (packet encoding, pairing) were worked out from the BRMesh Android app
for interoperability. No code or binaries from the app are included in this repository.
