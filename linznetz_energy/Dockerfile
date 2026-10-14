# Built from the repository root by .github/workflows/addon-image.yml:
#   docker build -f linznetz_energy/Dockerfile .
FROM python:3.12-slim

ARG BUILD_ARCH=aarch64
ARG BUILD_VERSION=dev
LABEL io.hass.type="addon" io.hass.arch="${BUILD_ARCH}" io.hass.version="${BUILD_VERSION}" \
      org.opencontainers.image.source="https://github.com/ulrichlehner/homebase"

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY linznetz_energy/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY databot/linznetz.py databot/databot.py databot/disaggregate.py databot/fusion.py databot/ha.py \
     databot/addon.py /app/

CMD [ "python3", "/app/addon.py" ]
