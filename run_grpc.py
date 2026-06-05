import logging
from pathlib import Path

from app.grpc.server import serve


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


if __name__ == "__main__":
    logger.info("run_grpc_entrypoint_started log_file=%s", LOG_FILE)
    serve()
