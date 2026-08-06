FROM aiogram/telegram-bot-api:10.2 AS provider-builder

USER root
ENV BGUTIL_PROVIDER_HOME="/opt/bgutil-ytdlp-pot-provider/server"

RUN apk add --no-cache \
      build-base ca-certificates cairo-dev curl ffmpeg giflib-dev git jpeg-dev \
      nodejs npm pango-dev pixman-dev pkgconf python3 python3-dev py3-pip \
      py3-virtualenv

RUN git clone --depth 1 --branch 1.3.1 \
      https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
      /opt/bgutil-ytdlp-pot-provider \
    && cd "$BGUTIL_PROVIDER_HOME" \
    && sed -i \
         -e 's/host: "::"/host: "127.0.0.1"/g' \
         -e 's/host: "0.0.0.0"/host: "127.0.0.1"/g' \
         -e 's/new SessionManager();/new SessionManager(false);/' \
         src/main.ts \
    && npm ci --no-audit --no-fund \
    && npx tsc \
    && npm prune --omit=dev --no-audit --no-fund \
    && test -f build/main.js \
    && grep -q '127.0.0.1' build/main.js \
    && grep -q 'new SessionManager(false)' build/main.js \
    && rm -rf /root/.npm /opt/bgutil-ytdlp-pot-provider/.git

COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements-runtime.txt


FROM aiogram/telegram-bot-api:10.2 AS runtime-base

USER root
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/opt/venv/bin:${PATH}" \
    BGUTIL_PROVIDER_HOME="/opt/bgutil-ytdlp-pot-provider/server"

RUN apk add --no-cache \
      ca-certificates chromium curl ffmpeg nodejs python3 xvfb

COPY --from=provider-builder /opt/venv /opt/venv
COPY --from=provider-builder \
     /opt/bgutil-ytdlp-pot-provider \
     /opt/bgutil-ytdlp-pot-provider

WORKDIR /app
RUN addgroup -S app \
    && adduser -S -G app -h /app app \
    && mkdir -p \
         /app/data \
         /app/storage \
         /var/lib/telegram-bot-api \
         /tmp/telegram-bot-api \
    && chown -R app:app \
         /app \
         /opt/bgutil-ytdlp-pot-provider \
         /var/lib/telegram-bot-api \
         /tmp/telegram-bot-api

COPY --chown=app:app docker /app/docker
RUN chmod 0755 /app/docker/start-render.sh /app/docker/chromium-wpc.sh \
    && ln -sf /app/docker/chromium-wpc.sh /usr/local/bin/chromium-wpc \
    && test -x /usr/local/bin/chromium-wpc \
    && ffmpeg -version >/dev/null \
    && ffprobe -version >/dev/null \
    && node --version \
    && command -v Xvfb >/dev/null \
    && /opt/venv/bin/python -c "import importlib.metadata as m; assert m.version('bgutil-ytdlp-pot-provider') == '1.3.1'; assert m.version('yt-dlp-getpot-wpc') == '1.0.0'" \
    && /opt/venv/bin/python -c "import curl_cffi, nodriver, yt_dlp, yt_dlp_ejs"


FROM runtime-base AS test

USER root
COPY requirements-runtime.txt requirements-dev.txt /tmp/
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements-dev.txt
COPY . /app
RUN chmod 0755 /app/docker/start-render.sh /app/docker/chromium-wpc.sh \
    && /opt/venv/bin/python -m compileall -q app scripts \
    && /opt/venv/bin/python -m pytest -q


FROM runtime-base AS runtime

COPY --chown=app:app app /app/app
COPY --chown=app:app scripts /app/scripts
RUN /opt/venv/bin/python -m compileall -q app scripts \
    && find /app -type d -name __pycache__ -prune -exec rm -rf '{}' + \
    && test ! -d /app/tests \
    && chown -R app:app /app

USER app
EXPOSE 10000
ENTRYPOINT []
CMD ["/app/docker/start-render.sh"]
