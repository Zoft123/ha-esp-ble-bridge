ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Debian (glibc), not Alpine: the ESP32 toolchains PlatformIO downloads are glibc binaries and do not
# run on musl, so firmware could not be compiled inside an Alpine-based add-on.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev \
        build-essential git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# ESPHome is pinned to the version the bundled components were compiled with.
RUN python3 -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "esphome==2025.10.3" \
        "flask>=3.0.0,<4.0.0" \
        "waitress>=3.0.0,<4.0.0" \
        "ruamel.yaml>=0.17.0,<1.0.0" \
        "requests>=2.31,<3" \
        "cryptography"

# PlatformIO keeps its toolchains in /config so they survive add-on updates.
ENV PLATFORMIO_CORE_DIR=/config/.platformio
ENV PIO_INSTALL_DEVPLATFORM_SILENT=yes

# Copy addon files
COPY rootfs /
# FastCon ESPHome components; copied to /config/esphome/esp_ble_bridge_components on start
COPY esphome_components /app/esphome_components

# Make service scripts executable
RUN chmod a+x /etc/services.d/brmesh-bridge/run /etc/services.d/brmesh-bridge/finish

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}" \
    maintainer="Zoft123" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Zoft123" \
    org.opencontainers.image.authors="Zoft123" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://github.com/Zoft123/ha-esp-ble-bridge" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${BUILD_REF}" \
    org.opencontainers.image.version="${BUILD_VERSION}"
