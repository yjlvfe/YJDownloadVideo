#!/bin/sh
exec /usr/bin/chromium-browser \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --disable-software-rasterizer \
  --disable-extensions \
  --disable-background-networking \
  --disable-component-update \
  --disable-default-apps \
  --disable-sync \
  --metrics-recording-only \
  --no-first-run \
  --mute-audio \
  --renderer-process-limit=1 \
  --window-size=960,540 \
  "$@"
