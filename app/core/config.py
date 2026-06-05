import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = BASE_DIR / "app" / "services" / "coordinate_templates"
STORAGE_ROOT_PATH = Path(
    os.getenv(
        "SCORINGSERVICE_STORAGE_ROOT_PATH",
        os.getenv("EXAMSERVICE_STORAGE_ROOT_PATH", "D:/DoAn/DoAn1_storage"),
    )
)
SCORING_STORAGE_DIR = STORAGE_ROOT_PATH / "scoring-service"
DEBUG_IMAGE_DIR = SCORING_STORAGE_DIR / "debug_images"
RAW_IMAGE_DIR = SCORING_STORAGE_DIR / "raw_images"
GRPC_HOST = os.getenv("GRPC_HOST", os.getenv("APP_HOST", "127.0.0.1"))
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))
