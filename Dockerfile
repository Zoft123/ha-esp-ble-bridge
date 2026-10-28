# ESP BLE Bridge add-on image. Home Assistant builds it on the user's machine (see build.yaml).
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:trixie
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Debian rather than Alpine: the ESP32 toolchains that ESPHome downloads are built for glibc.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git python3 python3-dev python3-venv \
    && rm -rf /var/lib/apt/lists/*

# ESPHome is pinned to the release the bundled components are built and tested with. 2026.10 needs
# Python 3.12+ (Debian trixie) and has the esp32_ble callback API the pairing scan uses.
ENV VIRTUAL_ENV=/opt/venv PATH="/opt/venv/bin:${PATH}"
RUN python3 -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "esphome==2026.10.1" \
        "flask>=3.0,<4" \
        "waitress>=3.0,<4" \
        "ruamel.yaml>=0.17,<1" \
        "requests>=2.31,<3" \
        cryptography

# Toolchains live in /config so they survive add-on updates.
ENV PLATFORMIO_CORE_DIR=/config/.platformio PIO_INSTALL_DEVPLATFORM_SILENT=yes

COPY rootfs /
# Copied to /config/esphome/esp_ble_bridge_components when the add-on starts.
COPY esphome_components /app/esphome_components
RUN chmod a+x /etc/services.d/esp-ble-bridge/run /etc/services.d/esp-ble-bridge/finish

ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}" \
    maintainer="Zoft123" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.authors="Zoft123" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://github.com/Zoft123/ha-esp-ble-bridge" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${BUILD_REF}" \
    org.opencontainers.image.version="${BUILD_VERSION}"
