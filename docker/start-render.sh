#!/bin/sh
set -eu

: "${TELEGRAM_API_ID:?TELEGRAM_API_ID is required}"
: "${TELEGRAM_API_HASH:?TELEGRAM_API_HASH is required}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is required}"

PORT="${PORT:-10000}"
LOCAL_API="${TELEGRAM_BOT_API_BASE:-http://127.0.0.1:8081}"
WORK_DIR="${TELEGRAM_WORK_DIR:-/tmp/telegram-bot-api}"
TEMP_DIR="${TELEGRAM_TEMP_DIR:-/tmp/telegram-bot-api-temp}"

rm -rf /app/storage/* /tmp/yjdownload-* /tmp/yjpreview-* 2>/dev/null || true
mkdir -p /app/storage /app/data "$WORK_DIR" "$TEMP_DIR"

curl -sS --max-time 30 -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/logOut" \
  >/dev/null 2>&1 || true

telegram-bot-api \
  --api-id="${TELEGRAM_API_ID}" \
  --api-hash="${TELEGRAM_API_HASH}" \
  --dir="$WORK_DIR" \
  --temp-dir="$TEMP_DIR" \
  --http-port=8081 \
  --http-ip-address=127.0.0.1 \
  --local \
  --verbosity="${TELEGRAM_VERBOSITY:-1}" \
  &
TBA_PID=$!

cleanup() {
  kill "${APP_PID:-}" "$TBA_PID" >/dev/null 2>&1 || true
  wait "${APP_PID:-}" "$TBA_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

ready=false
for _ in $(seq 1 90); do
  if ! kill -0 "$TBA_PID" >/dev/null 2>&1; then
    echo "Telegram local Bot API exited during startup" >&2
    exit 1
  fi
  if curl -fsS --max-time 3 "${LOCAL_API}/bot${TELEGRAM_BOT_TOKEN}/getMe" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done

if [ "$ready" != "true" ]; then
  echo "Telegram local Bot API did not become ready" >&2
  exit 1
fi

export TELEGRAM_BOT_API_BASE="$LOCAL_API"
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
APP_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$APP_PID" >/dev/null 2>&1; then
    echo "Application exited during startup" >&2
    exit 1
  fi
  sleep 1
done

WEBHOOK_URL="${TELEGRAM_WEBHOOK_URL:-}"
if [ -z "$WEBHOOK_URL" ] && [ -n "${RENDER_EXTERNAL_URL:-}" ]; then
  WEBHOOK_URL="${RENDER_EXTERNAL_URL%/}/webhook/telegram"
fi
if [ -z "$WEBHOOK_URL" ] && [ -n "${RENDER_EXTERNAL_HOSTNAME:-}" ]; then
  WEBHOOK_URL="https://${RENDER_EXTERNAL_HOSTNAME}/webhook/telegram"
fi
if [ -z "$WEBHOOK_URL" ]; then
  echo "TELEGRAM_WEBHOOK_URL is required" >&2
  exit 1
fi
export TELEGRAM_WEBHOOK_URL="$WEBHOOK_URL"

WEBHOOK_SECRET=$(python - <<'PY'
import hashlib
import os
material = f"yjdownload-local-webhook-v1\0{os.environ['TELEGRAM_BOT_TOKEN']}"
print(hashlib.sha256(material.encode()).hexdigest(), end="")
PY
)

curl -fsS --max-time 30 -X POST \
  "${LOCAL_API}/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WEBHOOK_URL}" \
  --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
  --data-urlencode 'allowed_updates=["message","edited_message","callback_query"]' \
  --data-urlencode "max_connections=10" \
  >/dev/null

wait "$APP_PID"
