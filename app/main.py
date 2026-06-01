import logging
from pathlib import Path

from fastapi import FastAPI

from app.api.routes_scoring import router as scoring_router


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "scoring_service.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

app = FastAPI(title="Scoring Service")
app.include_router(scoring_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok"}
