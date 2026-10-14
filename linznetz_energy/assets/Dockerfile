# Renderer for the add-on images (render.py): cairo for SVG, DejaVu for the logo text.
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libcairo2 fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "cairosvg>=2.7,<3" "Pillow>=10,<12"
WORKDIR /assets
ENTRYPOINT ["python3", "/assets/render.py"]
