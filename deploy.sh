#!/bin/bash
# 📦 نشر موحّد: ينسخ كل ملفات الكود المعدَّلة لكل الحاويات المعنية + إعادة تشغيل
# الاستخدام: bash deploy.sh
# ⚠️ يضمن أن كل الحاويات (bot + worker + web) على نفس الكود — يمنع "شغال هنا خربان هناك"

set -e
cd "$(dirname "$0")"

# كل ملفات app/ — انسخها لكل الحاويات (لو نسيت ملف، حاوية بتشتغل بكود قديم)
FILES=(
  app/config.py
  app/extractor.py
  app/bot.py
  app/worker.py
  app/status.py
  app/main.py
  app/db.py
  app/security.py
)

CONTAINERS=(yjdownloadvideo-bot-1 yjdownloadvideo-worker-1 yjdownloadvideo-web-1)

for c in "${CONTAINERS[@]}"; do
  if docker ps --format '{{.Names}}' | grep -q "^${c}$"; then
    for f in "${FILES[@]}"; do
      [ -f "$f" ] && docker cp "$f" "$c:/app/$f"
    done
    docker exec "$c" ln -sf /usr/bin/qjs /usr/bin/quickjs 2>/dev/null || true
    docker restart "$c"
    echo "✅ $c محدّث"
  fi
done

# .env (إن تغيّر)
for c in "${CONTAINERS[@]}"; do
  if docker ps --format '{{.Names}}' | grep -q "^${c}$"; then
    [ -f .env ] && docker cp .env "$c:/app/.env"
  fi
done

echo "=== اكتمل النشر الموحّد ==="
