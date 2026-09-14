from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from db import init_db

app = FastAPI(title="Petproject Music Player")

@app.on_event("startup")
def startup():
    init_db()

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Монтирование статики для веб-плеера
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
