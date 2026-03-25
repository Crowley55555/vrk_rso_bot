from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GetAllTasksResponse(BaseModel):
    tasks: list[dict[str, Any]]


class AppendTaskRequest(BaseModel):
    sheet_name: str = Field(..., min_length=1)
    row_data: list[Any]


class AppendTaskResponse(BaseModel):
    ok: bool = True


class UpdateCellRequest(BaseModel):
    sheet_name: str = Field(..., min_length=1)
    row_index: int = Field(..., ge=1)
    col_index: int = Field(..., ge=1)
    value: Any


class UpdateCellResponse(BaseModel):
    ok: bool = True


class DeleteRowResponse(BaseModel):
    ok: bool = True


class MoveTaskRequest(BaseModel):
    from_sheet: str = Field(..., min_length=1)
    to_sheet: str = Field(..., min_length=1)
    row_index: int = Field(..., ge=1)
    extra_data: dict[str, Any]


class MoveTaskResponse(BaseModel):
    ok: bool = True


class WriteLogRequest(BaseModel):
    who: str
    action: str
    task_name: str
    sheet_name: str
    details: str


class WriteLogResponse(BaseModel):
    ok: bool = True


class ErrorResponse(BaseModel):
    detail: str
