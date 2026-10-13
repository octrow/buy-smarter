"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(title="buy-smarter", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
