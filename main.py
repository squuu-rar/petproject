from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from db import init_db

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

@asynccontextmanager
async def lifespan(app: FastAPI):
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield

app = FastAPI(title="Petproject Music Player", lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "ok"}

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8090, reload=True)
