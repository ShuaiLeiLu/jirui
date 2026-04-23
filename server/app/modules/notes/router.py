from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.modules.notes.schemas import FolderItem, NoteItem, NoteUpsertRequest
from app.modules.notes.service import NoteService
from app.schemas.common import ApiResponse, ListResponse

router = APIRouter(prefix="/notes", tags=["notes"])
service = NoteService()


@router.get("/folders")
async def list_folders() -> ApiResponse[ListResponse[FolderItem]]:
    items: list[FolderItem] = []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("")
async def list_notes(folder_id: str | None = None) -> ApiResponse[ListResponse[NoteItem]]:
    del folder_id
    items: list[NoteItem] = []
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.post("")
async def create_note(payload: NoteUpsertRequest) -> ApiResponse[NoteItem]:
    del payload
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="笔记接口暂未接入真实数据源")


@router.put("/{note_id}")
async def update_note(note_id: str, payload: NoteUpsertRequest) -> ApiResponse[NoteItem]:
    del note_id, payload
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="笔记接口暂未接入真实数据源")
