FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Built from the repository root: docker build -f databot/Dockerfile .
# The shared library first (portal client, readings archive, fusion, HA client) ...
COPY lib /tmp/lib
RUN pip install --no-cache-dir /tmp/lib && rm -rf /tmp/lib

# ... then the command line tools (charts, chart OCR, HA import test)
COPY databot/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY databot/*.py databot/*.sh /app/
RUN chmod 0644 /app/*.sh && chmod +x /app/start.sh /app/databot.py \
    && ln -s /app/databot.py /usr/local/bin/databot

# Fetch new readings into the CSV files and render the chart every 30 minutes
RUN echo "*/30 * * * * /bin/bash /app/cron_current.sh > /proc/1/fd/1 2>/proc/1/fd/2" > /etc/cron.d/databot \
    # Archive the chart of the finished week (at 00:00 on Monday)
    && echo "0 0 * * 1 /bin/bash /app/cron_archive.sh > /proc/1/fd/1 2>/proc/1/fd/2" >> /etc/cron.d/databot \
    && chmod 0644 /etc/cron.d/databot && crontab /etc/cron.d/databot

CMD [ "/app/start.sh" ]
