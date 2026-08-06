#!/usr/bin/env bash
# ============================================================
# نشر YJDownloadVideo على أي سيرفر Ubuntu/Debian جديد — بأمر واحد
#   curl -sL <رابط الملف> | bash   أو   bash deploy.sh
# ============================================================
set -e

echo "🚀 YJDownloadVideo — نشر تلقائي"

# 1) تثبيت Docker إن لم يكن موجودًا
if ! command -v docker >/dev/null 2>&1; then
  echo "📦 تثبيت Docker…"
  apt-get update -y
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-compose-plugin
  systemctl enable docker || true
  systemctl start docker || true
fi

# 2) إعداد .env إن لم يوجد
if [ ! -f .env ]; then
  cp .env.example .env
  # 🔐 SECRET_KEY عشوائي تلقائيًا — ممنوع الإقلاع بالقالب الضعيف
  if grep -qE "^SECRET_KEY=change-this" .env; then
    NEW_KEY=$(openssl rand -hex 32)
    sed -i "s|^SECRET_KEY=change-this-to-a-long-random-secret$|SECRET_KEY=$NEW_KEY|" .env
    echo "🔐 تم توليد SECRET_KEY عشوائي."
  fi
  echo "⚠️ أنشأنا .env من القالب — عدّل توكن البوت ومفاتيح API ثم أعد التشغيل: docker compose up -d"
fi

# 3) بناء وتشغيل كل شيء (web + worker + bot + cleanup + redis + خادم 2GB المحلي)
docker compose up --build -d

# 4) فتح المنفذ في الجدار الناري
if command -v ufw >/dev/null 2>&1; then
  ufw allow 8000/tcp >/dev/null 2>&1 || true
fi

IP=$(curl -s --max-time 5 ifconfig.me || echo "SERVER_IP")
echo ""
echo "✅ تم النشر بنجاح!"
echo "   🌐 الواجهة:      http://$IP:8000"
echo "   🤖 البوت:        يعمل تلقائيًا (أرسل له أي رابط)"
echo "   🗑️  الحذف:       المقاطع تُحذف تلقائيًا بعد 24 ساعة"
echo ""
echo "   📌 لتحديث BASE_URL بعنوانك العام اضبطه في .env ثم: docker compose up -d"
