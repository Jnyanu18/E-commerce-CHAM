from fastapi import FastAPI

app = FastAPI(title="engine")


@app.get("/health")
def health() -> dict:
    return {"ok": True}
