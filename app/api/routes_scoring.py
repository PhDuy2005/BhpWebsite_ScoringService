from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.scoring import ApiResponse
from app.services.answer_detection import AnswerDetectionError, process_answer_sheet


router = APIRouter(prefix="/scoring", tags=["scoring"])


@router.post("/read", response_model=ApiResponse)
async def read_answer_sheet(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        result = process_answer_sheet(content, filename=file.filename)
    except AnswerDetectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ApiResponse(
        statusCode=200,
        message="Read OMR data",
        data=result,
    )

