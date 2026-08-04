#!/bin/sh
set -eu

: "${TELEGRAM_API_ID:?TELEGRAM_API_ID is required}"
: "${TELEGRAM_API_HASH:?TELEGRAM_API_HASH is required}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is required}"

PORT="${PORT:-10000}"
LOCAL_API="http://127.0.0.1:8081"
POT_PORT="${BGUTIL_PROVIDER_PORT:-4416}"
POT_URL="http://127.0.0.1:${POT_PORT}"
POT_HOME="${BGUTIL_PROVIDER_HOME:-/opt/bgutil-ytdlp-pot-provider/server}"
POT_LOG="/tmp/bgutil-pot-provider.log"
WORK_DIR="${TELEGRAM_WORK_DIR:-/tmp/telegram-bot-api-data}"
TEMP_DIR="${TELEGRAM_TEMP_DIR:-/tmp/telegram-bot-api-temp}"
TBA_LOG="/tmp/telegram-bot-api.log"
TBA_CHECK="/tmp/telegram-bot-api-check.txt"

redact() {
  sed "s#${TELEGRAM_BOT_TOKEN}#[REDACTED_BOT_TOKEN]#g" "$1" 2>/dev/null || true
}

show_tba_diagnostics() {
  echo "--- Telegram Local Bot API diagnostics ---" >&2
  echo "binary=$(command -v telegram-bot-api || echo missing)" >&2
  echo "local_api=${LOCAL_API}" >&2
  echo "work_dir=${WORK_DIR}" >&2
  echo "temp_dir=${TEMP_DIR}" >&2
  if [ -s "$TBA_CHECK" ]; then
    echo "last readiness response:" >&2
    redact "$TBA_CHECK" >&2
  fi
  if [ -s "$TBA_LOG" ]; then
    echo "server output:" >&2
    tail -n 120 "$TBA_LOG" | sed "s#${TELEGRAM_BOT_TOKEN}#[REDACTED_BOT_TOKEN]#g" >&2
  fi
  echo "--- End diagnostics ---" >&2
}

show_pot_diagnostics() {
  echo "--- PO Token provider diagnostics ---" >&2
  echo "home=${POT_HOME}" >&2
  echo "url=${POT_URL}" >&2
  if [ -s "$POT_LOG" ]; then
    tail -n 120 "$POT_LOG" >&2
  fi
  echo "--- End diagnostics ---" >&2
}

rm -rf \
  /tmp/yjdownload-* \
  /tmp/yjpreview-* \
  "$WORK_DIR" \
  "$TEMP_DIR" \
  "$TBA_LOG" \
  "$TBA_CHECK" \
  "$POT_LOG" \
  2>/dev/null || true
mkdir -p /app/storage /app/data "$WORK_DIR" "$TEMP_DIR"

if [ "${TELEGRAM_FORCE_LOGOUT_ON_START:-0}" = "1" ]; then
  curl -sS --max-time 30 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/logOut" \
    >/dev/null 2>&1 || true
fi

node "$POT_HOME/build/main.js" --port "$POT_PORT" >"$POT_LOG" 2>&1 &
POT_PID=$!

pot_ready=false
for _ in $(seq 1 90); do
  if ! kill -0 "$POT_PID" >/dev/null 2>&1; then
    echo "PO Token provider exited during startup" >&2
    show_pot_diagnostics
    exit 1
  fi
  if curl -fsS --max-time 2 "$POT_URL/ping" >/dev/null 2>&1; then
    pot_ready=true
    break
  fi
  sleep 1
done
if [ "$pot_ready" != "true" ]; then
  echo "PO Token provider did not become ready" >&2
  show_pot_diagnostics
  exit 1
fi

telegram-bot-api \
  --api-id="${TELEGRAM_API_ID}" \
  --api-hash="${TELEGRAM_API_HASH}" \
  --dir="$WORK_DIR" \
  --temp-dir="$TEMP_DIR" \
  --http-port=8081 \
  --http-ip-address=127.0.0.1 \
  --local \
  --verbosity="${TELEGRAM_VERBOSITY:-0}" \
  >"$TBA_LOG" 2>&1 &
TBA_PID=$!

cleanup() {
  if [ -n "${APP_PID:-}" ]; then
    kill "$APP_PID" >/dev/null 2>&1 || true
    wait "$APP_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${TBA_PID:-}" ]; then
    kill "$TBA_PID" >/dev/null 2>&1 || true
    wait "$TBA_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${POT_PID:-}" ]; then
    kill "$POT_PID" >/dev/null 2>&1 || true
    wait "$POT_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

ready=false
for _ in $(seq 1 120); do
  if ! kill -0 "$TBA_PID" >/dev/null 2>&1; then
    echo "Telegram local Bot API exited during startup" >&2
    show_tba_diagnostics
    exit 1
  fi
  if curl -sS --max-time 2 -o /dev/null "${LOCAL_API}/" 2>"$TBA_CHECK"; then
    ready=true
    break
  fi
  sleep 1
done

if [ "$ready" != "true" ]; then
  echo "Telegram local Bot API did not become ready" >&2
  show_tba_diagnostics
  exit 1
fi
rm -f "$TBA_CHECK"

export TELEGRAM_BOT_API_BASE="$LOCAL_API"
export BGUTIL_PROVIDER_URL="$POT_URL"
uvicorn app.asgi:app --host 0.0.0.0 --port "$PORT" &
APP_PID=$!

app_ready=false
for _ in $(seq 1 60); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    app_ready=true
    break
  fi
  if ! kill -0 "$APP_PID" >/dev/null 2>&1; then
    echo "Application exited during startup" >&2
    show_tba_diagnostics
    show_pot_diagnostics
    exit 1
  fi
  sleep 1
done
if [ "$app_ready" != "true" ]; then
  echo "Application health endpoint did not become ready" >&2
  exit 1
fi

WEBHOOK_URL="${TELEGRAM_WEBHOOK_URL:-https://yj-download-video.vercel.app/webhook/telegram}"
export TELEGRAM_WEBHOOK_URL="$WEBHOOK_URL"

WEBHOOK_SECRET=$(python - <<'PY'
import hashlib
import os
material = f"yjdownload-local-webhook-v1\0{os.environ['TELEGRAM_BOT_TOKEN']}"
print(hashlib.sha256(material.encode()).hexdigest(), end="")
PY
)

if ! curl -fsS --max-time 30 -X POST \
  "${LOCAL_API}/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WEBHOOK_URL}" \
  --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
  --data-urlencode 'allowed_updates=["message","edited_message","callback_query"]' \
  --data-urlencode "max_connections=10" \
  >/dev/null; then
  echo "Telegram webhook setup deferred; repair it through authenticated admin API" >&2
fi

wait "$APP_PID"
