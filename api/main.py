"""1C Analytics AI Help — FastAPI entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="1C Analytics AI Help", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/widget", StaticFiles(directory="widget"), name="widget")
app.mount("/web", StaticFiles(directory="web", html=True), name="web")


@app.get("/health")
async def health():
    return {"status": "ok"}
