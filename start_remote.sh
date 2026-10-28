#!/usr/bin/env bash
cd "$(dirname "$0")"
rm -f .tunnel_url /tmp/petproject_tunnel.log /tmp/petproject_ssh_tunnel.log

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

PORT="${PORT:-8001}"
SSH_LOG="/tmp/petproject_ssh_tunnel.log"
CF_LOG="/tmp/petproject_tunnel.log"

pkill -f "cloudflared.*127.0.0.1:${PORT}" 2>/dev/null || true
pkill -f "ssh.*127.0.0.1:${PORT}" 2>/dev/null || true

cleanup() {
    pkill -P $$ 2>/dev/null || true
    pkill -f "cloudflared.*127.0.0.1:${PORT}" 2>/dev/null || true
    pkill -f "ssh.*127.0.0.1:${PORT}" 2>/dev/null || true
    rm -f .tunnel_url
}
trap cleanup EXIT INT TERM

# Запускаем SSH-туннель localhost.run (*.lhr.life — работает в РФ на мобильном интернете БЕЗ VPN!)
if command -v ssh >/dev/null 2>&1; then
    echo "🌐 Запускаем прямой HTTPS-туннель без VPN (localhost.run)..."
    (
        while true; do
            rm -f "$SSH_LOG"
            ssh -T -n -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 -R "80:127.0.0.1:${PORT}" nokey@localhost.run >> "$SSH_LOG" 2>&1 &
            SSH_PID=$!
            for j in {1..25}; do
                NEW_URL=$(grep -oE "https://[a-zA-Z0-9-]+\.lhr\.life" "$SSH_LOG" 2>/dev/null | tail -n 1 || true)
                if [ -n "$NEW_URL" ]; then
                    echo "$NEW_URL" > .tunnel_url
                    break
                fi
                sleep 1
            done
            wait $SSH_PID 2>/dev/null || true
            sleep 3
        done
    ) &
fi

# Резервный туннель cloudflared по TCP (--protocol http2)
if command -v cloudflared >/dev/null 2>&1; then
    (
        while true; do
            rm -f "$CF_LOG"
            cloudflared tunnel --url "http://127.0.0.1:${PORT}" --protocol http2 --no-autoupdate >> "$CF_LOG" 2>&1 &
            CF_PID=$!
            for j in {1..25}; do
                if [ ! -s .tunnel_url ]; then
                    CF_URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" "$CF_LOG" 2>/dev/null | tail -n 1 || true)
                    if [ -n "$CF_URL" ]; then
                        echo "$CF_URL" > .tunnel_url
                        break
                    fi
                fi
                sleep 1
            done
            wait $CF_PID 2>/dev/null || true
            sleep 3
        done
    ) &
fi

for i in {1..20}; do
    if [ -s .tunnel_url ]; then
        URL=$(cat .tunnel_url)
        echo "======================================================="
        echo "📱 ССЫЛКА ДЛЯ ТЕЛЕФОНА (БЕЗ VPN): $URL"
        echo "======================================================="
        if command -v qrencode >/dev/null 2>&1; then
            qrencode -t ansiutf8 "$URL" || true
        fi
        break
    fi
    sleep 1
done

SKIP_AUTO_TUNNEL=1 python3 run.py
