FROM aiogram/telegram-bot-api:10.2

USER root
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

RUN apk add --no-cache \
      ca-certificates chromium curl ffmpeg python3 py3-pip py3-virtualenv

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/venv/bin/pip install --no-cache-dir -U yt-dlp

COPY . .
RUN mkdir -p /app/data /app/storage \
    && chmod 0755 /app/docker/start-render.sh \
    && pytest -q

EXPOSE 10000
CMD ["/app/docker/start-render.sh"]
