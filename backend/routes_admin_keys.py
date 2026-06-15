"""Anthropic Admin API kulcsok kezelése (admin)."""
from fastapi import APIRouter, Request, HTTPException

import admin_key_service
import auth
from anthropic_admin_client import test_connection, AdminApiError
from activity_logger import log_activity
from schemas import AdminKeyCreate, AdminKeyUpdate

router = APIRouter(prefix="/api/admin-keys", tags=["admin-keys"])


@router.get("")
def list_keys(request: Request):
    auth.get_current_admin(request)
    return admin_key_service.list_keys()


@router.post("")
def create_key(req: AdminKeyCreate, request: Request):
    a = auth.get_current_admin(request)
    try:
        key_id = admin_key_service.create_key(req.label, req.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(a["id"], "admin_key_create", target_type="admin_key",
                 target_id=str(key_id), detail={"label": req.label})
    return {"id": key_id}


@router.post("/{key_id}/test")
def test_key(key_id: int, request: Request):
    a = auth.get_current_admin(request)
    try:
        value = admin_key_service.get_key_value(key_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    try:
        org = test_connection(value)
    except AdminApiError as e:
        admin_key_service.record_test_result(key_id, False)
        raise HTTPException(400, f"A kapcsolat sikertelen: {e.message}")
    admin_key_service.record_test_result(key_id, True, org.get("id"), org.get("name"))
    log_activity(a["id"], "admin_key_test", target_type="admin_key", target_id=str(key_id),
                 detail={"organization": org.get("name")})
    return {"ok": True, "organization": org}


@router.patch("/{key_id}")
def update_key(key_id: int, req: AdminKeyUpdate, request: Request):
    a = auth.get_current_admin(request)
    if req.label is not None:
        try:
            admin_key_service.update_label(key_id, req.label)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if req.is_active is not None:
        admin_key_service.set_active(key_id, req.is_active)
    log_activity(a["id"], "admin_key_update", target_type="admin_key", target_id=str(key_id),
                 detail=req.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/{key_id}")
def delete_key(key_id: int, request: Request):
    a = auth.get_current_admin(request)
    admin_key_service.soft_delete(key_id)
    log_activity(a["id"], "admin_key_delete", target_type="admin_key", target_id=str(key_id))
    return {"ok": True}
