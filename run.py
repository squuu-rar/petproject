import socket
import uvicorn

def find_free_port(start_port: int = 8000, max_attempts: int = 20) -> int:
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    raise RuntimeError("Не удалось найти свободный порт")

if __name__ == "__main__":
    port = find_free_port(8000)
    print(f"\n🚀 Сервер запущен: http://127.0.0.1:{port}\n")
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=True)
