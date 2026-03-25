from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.dependencies.auth import verify_api_key
from api.schemas.sheets import (
    AppendTaskRequest,
    AppendTaskResponse,
    DeleteRowResponse,
    GetAllTasksResponse,
    MoveTaskRequest,
    MoveTaskResponse,
    UpdateCellRequest,
    UpdateCellResponse,
    WriteLogRequest,
    WriteLogResponse,
)
from api.services.sheets_service import GoogleSheetsService, SheetsServiceError


router = APIRouter(prefix="/sheets", tags=["sheets"])


def get_sheets_service(request: Request) -> GoogleSheetsService:
    service = getattr(request.app.state, "sheets_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервис Google Sheets не инициализирован.",
        )
    return service


SheetsDep = Annotated[GoogleSheetsService, Depends(get_sheets_service)]


@router.get(
    "/tasks/{sheet_name}",
    response_model=GetAllTasksResponse,
    dependencies=[Depends(verify_api_key)],
)
async def get_all_tasks(sheet_name: str, service: SheetsDep) -> GetAllTasksResponse:
    try:
        tasks = await service.get_all_tasks(sheet_name)
    except SheetsServiceError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return GetAllTasksResponse(tasks=tasks)


@router.post(
    "/tasks/append",
    response_model=AppendTaskResponse,
    dependencies=[Depends(verify_api_key)],
)
async def append_task(body: AppendTaskRequest, service: SheetsDep) -> AppendTaskResponse:
    try:
        await service.append_task(body.sheet_name, body.row_data)
    except SheetsServiceError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return AppendTaskResponse()


@router.patch(
    "/cell",
    response_model=UpdateCellResponse,
    dependencies=[Depends(verify_api_key)],
)
async def update_cell(body: UpdateCellRequest, service: SheetsDep) -> UpdateCellResponse:
    try:
        await service.update_cell(body.sheet_name, body.row_index, body.col_index, body.value)
    except SheetsServiceError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return UpdateCellResponse()


@router.delete(
    "/rows",
    response_model=DeleteRowResponse,
    dependencies=[Depends(verify_api_key)],
)
async def delete_row(
    sheet_name: str,
    row_index: int,
    service: SheetsDep,
) -> DeleteRowResponse:
    try:
        await service.delete_row(sheet_name, row_index)
    except SheetsServiceError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return DeleteRowResponse()


@router.post(
    "/move",
    response_model=MoveTaskResponse,
    dependencies=[Depends(verify_api_key)],
)
async def move_task(body: MoveTaskRequest, service: SheetsDep) -> MoveTaskResponse:
    try:
        await service.move_task(
            body.from_sheet,
            body.to_sheet,
            body.row_index,
            body.extra_data,
        )
    except SheetsServiceError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return MoveTaskResponse()


@router.post(
    "/log",
    response_model=WriteLogResponse,
    dependencies=[Depends(verify_api_key)],
)
async def write_log(body: WriteLogRequest, service: SheetsDep) -> WriteLogResponse:
    await service.write_log(
        body.who,
        body.action,
        body.task_name,
        body.sheet_name,
        body.details,
    )
    return WriteLogResponse()
