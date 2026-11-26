"""角色卡：导入 / 我的角色 / 导出（JSON + PNG）/ 从角色开会话。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse

from app.auth.deps import AuthUser, get_current_user
from app.domain.character_card import CardParseError
from app.services.character_service import CharacterService

router = APIRouter(prefix="/api/v1/characters", tags=["characters"])


def _service(request: Request) -> CharacterService:
    return request.app.state.character_service


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CardParseError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/import")
async def import_card(
    request: Request,
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    service = _service(request)
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="文件为空")
    try:
        char = await service.import_card(user.user_id, file.filename or "", data)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)
    return {"character": char.public()}


@router.get("/mine")
async def my_characters(request: Request, user: AuthUser = Depends(get_current_user)):
    service = _service(request)
    rows = await service.list_mine(user.user_id)
    return {"characters": [c.public() for c in rows]}


@router.get("/{character_id}")
async def character_detail(
    character_id: int, request: Request, user: AuthUser = Depends(get_current_user)
):
    service = _service(request)
    try:
        return await service.detail(user.user_id, character_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)


@router.get("/{character_id}/export.json")
async def export_json(
    character_id: int, request: Request, user: AuthUser = Depends(get_current_user)
):
    service = _service(request)
    try:
        card = await service.export_json(user.user_id, character_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)
    name = f"{(card.get('data') or {}).get('name') or 'character'}.json"
    return JSONResponse(
        content=card,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.get("/{character_id}/export.png")
async def export_png(
    character_id: int, request: Request, user: AuthUser = Depends(get_current_user)
):
    service = _service(request)
    try:
        data, filename = await service.export_png(user.user_id, character_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)
    return Response(
        content=data,
        media_type="image/png",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/{character_id}/sessions")
async def create_session(
    character_id: int, request: Request, user: AuthUser = Depends(get_current_user)
):
    service = _service(request)
    try:
        return await service.create_session(user.user_id, character_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)


@router.delete("/{character_id}")
async def delete_character(
    character_id: int, request: Request, user: AuthUser = Depends(get_current_user)
):
    service = _service(request)
    try:
        await service.require_owned(user.user_id, character_id)
        await service.store.soft_delete(character_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)
    return {"deleted": True}
