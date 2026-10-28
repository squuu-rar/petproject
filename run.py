import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import uvicorn

BASE_DIR = Path(__file__).resolve().parent
TUNNEL_URL_FILE = BASE_DIR / ".tunnel_url"
TUNNEL_LOG_FILE = Path("/tmp/petproject_tunnel.log")
SSH_TUNNEL_LOG_FILE = Path("/tmp/petproject_ssh_tunnel.log")

TUNNEL_URL_RE = re.compile(
    r"https://[a-zA-Z0-9.-]+\.(?:lhr\.life|localhost\.run|pinggy\.link|a\.pinggy\.online|serveo\.net|trycloudflare\.com)"
)


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_background_tunnel(port: int = 8001) -> list[subprocess.Popen]:
    """Поднимает туннель без VPN (через SSH localhost.run *.lhr.life, который не блокируется ТСПУ на мобильных операторах)
    и резервный cloudflared (--protocol http2)."""
    TUNNEL_URL_FILE.unlink(missing_ok=True)
    TUNNEL_LOG_FILE.unlink(missing_ok=True)
    SSH_TUNNEL_LOG_FILE.unlink(missing_ok=True)

    for pat in (f"cloudflared.*127.0.0.1:{port}", f"ssh.*127.0.0.1:{port}"):
        try:
            subprocess.run(["pkill", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass

    procs: list[subprocess.Popen] = []

    # 1. Основной туннель БЕЗ VPN: SSH localhost.run (*.lhr.life открывается с телефона без VPN у всех мобильных операторов)
    if shutil.which("ssh"):
        try:
            ssh_log = open(SSH_TUNNEL_LOG_FILE, "w", encoding="utf-8")
            ssh_proc = subprocess.Popen(
                [
                    "ssh", "-T", "-n",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "ServerAliveInterval=30",
                    "-o", "ConnectTimeout=10",
                    "-R", f"80:127.0.0.1:{port}",
                    "nokey@localhost.run",
                ],
                stdout=ssh_log,
                stderr=subprocess.STDOUT,
            )
            procs.append(ssh_proc)
        except Exception:
            pass

    # 2. Резервный туннель cloudflared в режиме --protocol http2 (TCP вместо заблокированного QUIC/UDP)
    if shutil.which("cloudflared"):
        try:
            cf_log = open(TUNNEL_LOG_FILE, "w", encoding="utf-8")
            cf_proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}", "--protocol", "http2", "--no-autoupdate"],
                stdout=cf_log,
                stderr=subprocess.STDOUT,
            )
            procs.append(cf_proc)
        except Exception:
            pass

    if not procs:
        return procs

    for _ in range(22):
        time.sleep(0.5)
        # Сначала ищем ссылку *.lhr.life (открывается без VPN!), затем *.trycloudflare.com
        for log_file in (SSH_TUNNEL_LOG_FILE, TUNNEL_LOG_FILE):
            if log_file.exists():
                try:
                    text = log_file.read_text(encoding="utf-8", errors="ignore")
                    matches = TUNNEL_URL_RE.findall(text)
                    valid = [m for m in matches if not m.endswith("admin.localhost.run") and "twitter" not in m]
                    if valid:
                        TUNNEL_URL_FILE.write_text(valid[-1], encoding="utf-8")
                        return procs
                except Exception:
                    pass
    return procs


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8001"))
    lan_ip = get_lan_ip()

    tunnel_procs: list[subprocess.Popen] = []
    if os.environ.get("SKIP_AUTO_TUNNEL") != "1":
        tunnel_procs = start_background_tunnel(port=port)

    tunnel_url = None
    if TUNNEL_URL_FILE.exists():
        try:
            tunnel_url = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            tunnel_url = None

    print("=======================================================")
    print(f"🚀 Сервер Écho на ПК:                 http://127.0.0.1:{port}")
    print(f"🏠 Телефон дома по Wi-Fi (без VPN):   http://{lan_ip}:{port}")
    if tunnel_url:
        print(f"🌍 Телефон вне дома (без VPN):        {tunnel_url}")
    print("=======================================================")

    try:
        uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
    finally:
        for p in tunnel_procs:
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        TUNNEL_URL_FILE.unlink(missing_ok=True)
