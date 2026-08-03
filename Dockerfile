FROM aiogram/telegram-bot-api:10.2

USER root
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/opt/venv/bin:${PATH}"

RUN apk add --no-cache \
      ca-certificates chromium curl ffmpeg nodejs python3 py3-pip py3-virtualenv

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data /app/storage \
    && chmod 0755 /app/docker/start-render.sh \
    && ffmpeg -version >/dev/null \
    && node --version \
    && python -c "import curl_cffi, yt_dlp, yt_dlp_ejs" \
    && python -m compileall -q app \
    && python -m pytest -q

EXPOSE 10000
ENTRYPOINT []
CMD ["/app/docker/start-render.sh"]
