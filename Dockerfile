FROM aiogram/telegram-bot-api:10.2

USER root
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/opt/venv/bin:${PATH}" \
    BGUTIL_PROVIDER_HOME="/opt/bgutil-ytdlp-pot-provider/server"

RUN apk add --no-cache \
      build-base ca-certificates cairo-dev chromium curl ffmpeg giflib-dev git \
      jpeg-dev nodejs npm pango-dev pixman-dev pkgconf python3 python3-dev \
      py3-pip py3-virtualenv

RUN git clone --depth 1 --branch 1.3.1 \
      https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
      /opt/bgutil-ytdlp-pot-provider \
    && cd "$BGUTIL_PROVIDER_HOME" \
    && npm ci --no-audit --no-fund \
    && npx tsc \
    && npm prune --omit=dev --no-audit --no-fund \
    && test -f build/main.js \
    && rm -rf /root/.npm /opt/bgutil-ytdlp-pot-provider/.git

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
    && python -c "import importlib.metadata as m; assert m.version('bgutil-ytdlp-pot-provider') == '1.3.1'" \
    && python -c "import curl_cffi, yt_dlp, yt_dlp_ejs" \
    && python -m compileall -q app scripts \
    && python -m pytest -q

EXPOSE 10000
ENTRYPOINT []
CMD ["/app/docker/start-render.sh"]
