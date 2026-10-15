# Built from the repository root by .github/workflows/addon-image.yml:
#   docker build -f linznetz_energy/Dockerfile .
FROM python:3.12-slim

ARG BUILD_ARCH=aarch64
ARG BUILD_VERSION=dev
LABEL io.hass.type="addon" io.hass.arch="${BUILD_ARCH}" io.hass.version="${BUILD_VERSION}" \
      org.opencontainers.image.source="https://github.com/ulrichlehner/homebase"

ENV PYTHONUNBUFFERED=1 ADDON_VERSION=${BUILD_VERSION}
WORKDIR /app

# The shared library (portal client, readings archive, fusion, HA client) ...
COPY lib /tmp/lib
RUN pip install --no-cache-dir /tmp/lib "paho-mqtt>=2.1,<3" && rm -rf /tmp/lib

# ... and the add-on itself: main loop and its page in HA
COPY linznetz_energy/app/*.py /app/

CMD [ "python3", "/app/main.py" ]
