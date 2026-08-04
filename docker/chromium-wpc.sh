#!/bin/sh
exec /usr/bin/chromium-browser \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --window-size=1280,720 \
  "$@"
