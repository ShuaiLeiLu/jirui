from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.modules.tasks.schemas import (
    ScheduleType,
    TaskCreateRequest,
    TaskRunLog,
    TaskRunRecord,
    TaskStatus,
    TaskSummary,
    TaskUpdateRequest,
)
from app.modules.tasks.service import TaskService
from app.schemas.common import ApiResponse, ListResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])
service = TaskService()


@router.get("")
async def list_tasks(
    status: TaskStatus | None = None,
    schedule_type: ScheduleType | None = None,
) -> ApiResponse[ListResponse[TaskSummary]]:
    del status, schedule_type
    items: list[TaskSummary] = []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.post("")
async def create_task(payload: TaskCreateRequest) -> ApiResponse[TaskSummary]:
    del payload
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="任务接口暂未接入真实数据源")


@router.get("/runs")
async def list_runs(task_id: str | None = None) -> ApiResponse[ListResponse[TaskRunRecord]]:
    del task_id
    items: list[TaskRunRecord] = []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/runs/{run_id}/logs")
async def list_run_logs(run_id: str) -> ApiResponse[ListResponse[TaskRunLog]]:
    del run_id
    items: list[TaskRunLog] = []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.patch("/{task_id}")
async def update_task(task_id: str, payload: TaskUpdateRequest) -> ApiResponse[TaskSummary]:
    del task_id, payload
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="任务接口暂未接入真实数据源")


@router.delete("/{task_id}")
async def delete_task(task_id: str) -> ApiResponse[TaskSummary]:
    del task_id
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="任务接口暂未接入真实数据源")


@router.post("/{task_id}/activate")
async def activate_task(task_id: str) -> ApiResponse[TaskSummary]:
    del task_id
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="任务接口暂未接入真实数据源")


@router.post("/{task_id}/pause")
async def pause_task(task_id: str) -> ApiResponse[TaskSummary]:
    del task_id
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="任务接口暂未接入真实数据源")


@router.post("/{task_id}/run")
async def run_task(task_id: str) -> ApiResponse[TaskRunRecord]:
    del task_id
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="任务接口暂未接入真实数据源")


@router.get("/{task_id}/runs")
async def list_task_runs(task_id: str) -> ApiResponse[ListResponse[TaskRunRecord]]:
    del task_id
    items: list[TaskRunRecord] = []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))
