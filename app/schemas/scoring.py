from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    statusCode: int = Field(default=200)
    message: str
    data: dict[str, Any] | list[dict[str, Any]] | None = None
