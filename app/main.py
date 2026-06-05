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
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Scoring Service")
app.include_router(scoring_router, prefix="/api/v1")


@app.on_event("startup")
def on_startup() -> None:
    logger.info("rest_server_starting title=%s log_file=%s", app.title, LOG_FILE)


@app.on_event("shutdown")
def on_shutdown() -> None:
    logger.info("rest_server_stopped")


@app.get("/health")
def health_check():
    logger.info("health_check_ok")
    return {"status": "ok"}
