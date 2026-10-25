FROM python:3.13-slim-bookworm

# ffmpeg: une/convierte los vídeos HLS · megatools: enlaces de MEGA · tzdata: zona horaria del cron
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg megatools tzdata ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

ENV ANIMEDL_SERIES_DIR=/series \
    ANIMEDL_MOVIES_DIR=/movies \
    ANIMEDL_DOWNLOAD_DIR=/downloads \
    ANIMEDL_DATABASE=/data/animedl.db \
    ANIMEDL_CONFIG=/data/config.toml \
    HOME=/data \
    PYTHONUNBUFFERED=1

VOLUME ["/data", "/series", "/movies"]
ENTRYPOINT ["tini", "--", "entrypoint.sh"]
CMD ["daemon"]
