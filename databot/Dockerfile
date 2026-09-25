FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py *.sh /app/
RUN chmod 0644 /app/*.sh && chmod +x /app/start.sh /app/databot.py \
    && ln -s /app/databot.py /usr/local/bin/databot

# Load new smart meter data and render the chart every 30 minutes
RUN echo "*/30 * * * * /bin/bash /app/cron_current.sh > /proc/1/fd/1 2>/proc/1/fd/2" > /etc/cron.d/databot \
    # Archive charts weekly (at 00:00 on Thursday)
    && echo "0 0 * * 4 /bin/bash /app/cron_archive.sh > /proc/1/fd/1 2>/proc/1/fd/2" >> /etc/cron.d/databot \
    # Backfill older data and repair gaps once a day at 03:00
    && echo "0 3 * * * /bin/bash /app/cron_integrity.sh > /proc/1/fd/1 2>/proc/1/fd/2" >> /etc/cron.d/databot \
    && chmod 0644 /etc/cron.d/databot && crontab /etc/cron.d/databot

CMD [ "/app/start.sh" ]
