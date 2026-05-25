from fastapi import FastAPI

from app.api.routes_scoring import router as scoring_router


app = FastAPI(title="Scoring Service")
app.include_router(scoring_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok"}
