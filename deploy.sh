#!/bin/bash
# 📦 نشر موحّد: ينسخ كل ملفات الكود المعدَّلة لكل الحاويات المعنية + إعادة تشغيل
# الاستخدام: bash deploy.sh
# ⚠️ يضمن أن كل الحاويات (bot + worker + web) على نفس الكود — يمنع "شغال هنا خربان هناك"

set -e
cd "$(dirname "$0")"

# YJ active route: Cloudflare WARP. NordVPN is standby in docker-compose.nordvpn.yml.
COMPOSE=(docker compose --env-file .env)


# Source mounts keep every service on the current repository code.
"${COMPOSE[@]}" up -d --build --remove-orphans
"${COMPOSE[@]}" ps
echo "=== YJ deployed on WARP (NordVPN standby only) ==="
