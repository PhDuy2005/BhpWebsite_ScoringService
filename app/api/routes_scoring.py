from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.schemas.scoring import ApiResponse
from app.services.answer_detection import (
    AnswerDetectionError,
    generate_pdf_template_points_preview_archive,
    generate_template_points_preview,
    get_pdf_page_count,
    process_answer_sheet,
    process_pdf_answer_sheets,
)


router = APIRouter(prefix="/scoring", tags=["scoring"])


@router.post("/read", response_model=ApiResponse)
async def read_answer_sheet(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        if (file.filename or "").lower().endswith(".pdf"):
            result = process_pdf_answer_sheets(content, filename=file.filename)
        else:
            result = process_answer_sheet(content, filename=file.filename)
    except AnswerDetectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ApiResponse(
        statusCode=200,
        message="Read OMR data",
        data=result,
    )


@router.post("/test")
async def preview_answer_sheet_points(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        if (file.filename or "").lower().endswith(".pdf"):
            page_count = get_pdf_page_count(content)
            if page_count > 1:
                archive_bytes = generate_pdf_template_points_preview_archive(
                    content,
                    filename=file.filename,
                )
                return Response(content=archive_bytes, media_type="application/zip")

        image_bytes = generate_template_points_preview(content, filename=file.filename)
    except AnswerDetectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(content=image_bytes, media_type="image/png")
