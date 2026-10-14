# Image of the command line tools, built by ./homebase from the repository root:
#   docker build -f databot/Dockerfile .
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The shared library first (portal client, readings archive, fusion, HA client) ...
COPY lib /tmp/lib
RUN pip install --no-cache-dir /tmp/lib && rm -rf /tmp/lib

# ... then the command line tools (charts, chart OCR, HA import test)
COPY databot/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY databot/*.py /app/
RUN ln -s /app/databot.py /usr/local/bin/databot

CMD [ "python3", "/app/databot.py", "--help" ]
