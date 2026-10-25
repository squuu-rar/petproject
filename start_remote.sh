#!/usr/bin/env bash
cd "$(dirname "$0")"
rm -f .tunnel_url

if command -v cloudflared >/dev/null 2>&1; then
    echo "🌐 Запускаем внешний HTTPS-туннель для доступа с телефона вне дома..."
    cloudflared tunnel --url http://127.0.0.1:8001 --no-autoupdate > /tmp/petproject_tunnel.log 2>&1 &
    TUNNEL_PID=$!
    trap "kill $TUNNEL_PID 2>/dev/null; rm -f .tunnel_url" EXIT

    for i in {1..15}; do
        URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" /tmp/petproject_tunnel.log | head -n 1)
        if [ -n "$URL" ]; then
            echo "$URL" > .tunnel_url
            echo "================================================================"
            echo "📱 ССЫЛКА ДЛЯ ТЕЛЕФОНА ВНЕ ДОМА: $URL"
            echo "================================================================"
            break
        fi
        sleep 1
    done
else
    echo "ℹ️ Для доступа вне дома через интернет установите cloudflared: sudo pacman -S cloudflared"
fi

python3 run.py
