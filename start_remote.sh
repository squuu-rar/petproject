#!/usr/bin/env bash
cd "$(dirname "$0")"
rm -f .tunnel_url

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

PORT="${PORT:-8001}"
TUNNEL_LOG="/tmp/petproject_tunnel.log"
rm -f "$TUNNEL_LOG"

cleanup() {
    if [ -n "${TUNNEL_SUPERVISOR_PID:-}" ]; then
        kill "$TUNNEL_SUPERVISOR_PID" 2>/dev/null || true
    fi
    pkill -P $$ 2>/dev/null || true
    rm -f .tunnel_url
}
trap cleanup EXIT INT TERM

if command -v tailscale >/dev/null 2>&1; then
    TS_IP=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
    if [ -n "$TS_IP" ]; then
        echo "🔒 Tailscale активен! Постоянная ссылка для телефона: http://${TS_IP}:${PORT}"
    fi
fi

if command -v cloudflared >/dev/null 2>&1; then
    echo "🌐 Запускаем внешний HTTPS-туннель для доступа с телефона вне дома (с автоперезапуском)..."
    (
        while true; do
            cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate >> "$TUNNEL_LOG" 2>&1 || true
            sleep 3
        done
    ) &
    TUNNEL_SUPERVISOR_PID=$!

    for i in {1..20}; do
        URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" "$TUNNEL_LOG" 2>/dev/null | tail -n 1 || true)
        if [ -n "$URL" ]; then
            echo "$URL" > .tunnel_url
            echo "======================================================="
            echo "📱 ССЫЛКА ДЛЯ ТЕЛЕФОНА ВНЕ ДОМА: $URL"
            echo "======================================================="
            if command -v qrencode >/dev/null 2>&1; then
                qrencode -t ansiutf8 "$URL" || true
            fi
            break
        fi
        sleep 1
    done
else
    echo "ℹ️ Для доступа вне дома через интернет установите cloudflared: sudo pacman -S cloudflared"
fi

python3 run.py
