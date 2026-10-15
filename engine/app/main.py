import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router

app = FastAPI(title="engine")
origins = [o for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["authorization", "content-type"])
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
