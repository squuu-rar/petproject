import socket
import uvicorn

def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    lan_ip = get_lan_ip()
    print(f"🚀 Сервер запущен на ПК:       http://127.0.0.1:8001")
    print(f"📱 Вход с телефона (Wi-Fi):    http://{lan_ip}:8001")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
