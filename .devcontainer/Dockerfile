# Image of the VS Code dev container (.devcontainer/devcontainer.json): what the
# tests and tools need, so `pytest` runs directly in the terminal. The sources are
# not copied in, the workspace is bind-mounted. The ./homebase wrapper still builds
# its own runtime image from databot/Dockerfile.
FROM python:3.12-slim

# - tesseract-ocr: chart OCR (reconstruct), as in databot/Dockerfile
# - libdigest-sha-perl: `shasum`, which ./homebase uses to hash the sources
# - git, ssh, ca-certificates: the editor's git; shellcheck for the bash scripts
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tzdata tesseract-ocr libdigest-sha-perl git openssh-client ca-certificates shellcheck \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Dependencies of the library (read from lib/pyproject.toml, so they are not listed twice),
# the command line tools (+ pytest, pyyaml) and the add-on (paho-mqtt, as in its Dockerfile).
# The library itself is not installed: PYTHONPATH (devcontainer.json) points at the working tree.
COPY lib/pyproject.toml /tmp/lib/pyproject.toml
COPY databot/requirements.txt databot/requirements-dev.txt /tmp/databot/
RUN python3 -c "import tomllib; print('\n'.join(tomllib.load(open('/tmp/lib/pyproject.toml','rb'))['project']['dependencies']))" > /tmp/lib-deps.txt \
    && pip install --no-cache-dir -r /tmp/lib-deps.txt -r /tmp/databot/requirements-dev.txt "paho-mqtt>=2.1,<3" \
    && rm -rf /tmp/lib /tmp/databot /tmp/lib-deps.txt
