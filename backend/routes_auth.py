"""Auth és felhasználó-kezelés végpontjai."""
from fastapi import APIRouter, Request, Response, HTTPException

import auth
from auth import COOKIE_NAME, SESSION_EXPIRY_DAYS, get_client_ip
from activity_logger import log_activity
from schemas import (
    SetupRequest, LoginRequest, ChangePasswordRequest,
    UserCreate, UserUpdate, ResetPasswordRequest, UserScopeUpdate,
)

router = APIRouter(prefix="/api", tags=["auth"])


def _is_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "")
    if proto:
        return proto.split(",")[0].strip() == "https"
    return request.url.scheme == "https"


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_is_secure(request),
        samesite="lax",
        max_age=SESSION_EXPIRY_DAYS * 86400,
        path="/",
    )


# ============================================================
# AUTH
# ============================================================

@router.get("/auth/status")
def auth_status(request: Request):
    user = auth.get_current_user_optional(request)
    if user:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            auth.update_session_activity(token)
    return {
        "authenticated": user is not None,
        "needs_setup": not auth.has_any_user(),
        "user": user,
    }


@router.post("/auth/setup")
def setup(req: SetupRequest, request: Request, response: Response):
    if auth.has_any_user():
        raise HTTPException(400, "A rendszer már inicializálva van")
    try:
        user = auth.create_user(req.email, req.password, req.name, role="admin")
    except ValueError as e:
        raise HTTPException(400, str(e))
    ip = get_client_ip(request)
    token, _ = auth.create_session(user["id"], ip, request.headers.get("user-agent"))
    _set_session_cookie(request, response, token)
    log_activity(user["id"], "setup", ip=ip)
    return {"user": user}


@router.post("/auth/login")
def login(req: LoginRequest, request: Request, response: Response):
    ip = get_client_ip(request)
    user = auth.get_user_by_email((req.email or "").strip().lower())
    # Brute-force védelem: zárolt fióknál a jelszót nem is ellenőrizzük → 429.
    if user and user.get("is_active") and auth.is_locked(user):
        log_activity(user["id"], "login_locked", detail={"email": req.email}, ip=ip)
        raise HTTPException(429, "Túl sok sikertelen próbálkozás. Próbáld újra később.")
    if (not user or not user.get("is_active")
            or not auth.verify_password(req.password, user["password_hash"])):
        # Létező aktív fióknál a hibás jelszó beleszámít a zárolásba.
        if user and user.get("is_active"):
            auth.register_failed_login(user["id"])
        log_activity(None, "login_failed", detail={"email": req.email}, ip=ip)
        raise HTTPException(401, "Hibás email-cím vagy jelszó")
    auth.clear_failed_login(user["id"])
    token, _ = auth.create_session(user["id"], ip, request.headers.get("user-agent"))
    _set_session_cookie(request, response, token)
    log_activity(user["id"], "login", ip=ip)
    safe = {k: v for k, v in user.items() if k != "password_hash"}
    return {"user": safe}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        auth.revoke_session(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(req: ChangePasswordRequest, request: Request):
    user = auth.get_current_user(request)
    full = auth.get_user_by_email(user["email"])
    if not full or not auth.verify_password(req.old_password, full["password_hash"]):
        raise HTTPException(400, "A jelenlegi jelszó hibás")
    try:
        auth.update_user_password(user["id"], req.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(user["id"], "password_change", ip=get_client_ip(request))
    return {"ok": True}


# ============================================================
# FELHASZNÁLÓK (admin)
# ============================================================

@router.get("/users")
def list_users(request: Request):
    auth.get_current_admin(request)
    return auth.list_users()


@router.post("/users")
def create_user(req: UserCreate, request: Request):
    admin = auth.get_current_admin(request)
    try:
        user = auth.create_user(req.email, req.password, req.name, req.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(admin["id"], "user_create", target_type="user", target_id=str(user["id"]),
                 detail={"email": user["email"], "role": user["role"]})
    return user


@router.patch("/users/{user_id}")
def update_user(user_id: int, req: UserUpdate, request: Request):
    admin = auth.get_current_admin(request)
    target = auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Felhasználó nem található")
    # Az utolsó aktív admin ne legyen lefokozható/inaktiválható
    demoting = (req.role is not None and req.role != "admin" and target["role"] == "admin")
    deactivating = (req.is_active is False and target["role"] == "admin")
    if (demoting or deactivating) and auth.count_admins(exclude_user_id=user_id) == 0:
        raise HTTPException(400, "Legalább egy aktív adminisztrátornak maradnia kell")
    try:
        auth.update_user(user_id, name=req.name, role=req.role, is_active=req.is_active)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(admin["id"], "user_update", target_type="user", target_id=str(user_id),
                 detail=req.model_dump(exclude_none=True))
    return auth.get_user_by_id(user_id)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, req: ResetPasswordRequest, request: Request):
    admin = auth.get_current_admin(request)
    if not auth.get_user_by_id(user_id):
        raise HTTPException(404, "Felhasználó nem található")
    try:
        auth.update_user_password(user_id, req.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(admin["id"], "user_password_reset", target_type="user", target_id=str(user_id))
    return {"ok": True}


@router.get("/users/{user_id}/scope")
def get_user_scope(user_id: int, request: Request):
    """A viewer hatóköre (hozzárendelt API kulcs- és workspace-id-k). Admin-only."""
    auth.get_current_admin(request)
    if not auth.get_user_by_id(user_id):
        raise HTTPException(404, "Felhasználó nem található")
    return auth.get_user_scope(user_id)


@router.put("/users/{user_id}/scope")
def set_user_scope(user_id: int, req: UserScopeUpdate, request: Request):
    """A viewer hatókörének teljes cseréje. Admin-only."""
    admin = auth.get_current_admin(request)
    if not auth.get_user_by_id(user_id):
        raise HTTPException(404, "Felhasználó nem található")
    auth.set_user_scope(user_id, req.api_key_ids, req.workspace_ids)
    log_activity(admin["id"], "user_scope_update", target_type="user", target_id=str(user_id),
                 detail={"api_key_ids": req.api_key_ids, "workspace_ids": req.workspace_ids})
    return auth.get_user_scope(user_id)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request):
    admin = auth.get_current_admin(request)
    if user_id == admin["id"]:
        raise HTTPException(400, "Saját fiók nem törölhető")
    target = auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Felhasználó nem található")
    if target["role"] == "admin" and auth.count_admins(exclude_user_id=user_id) == 0:
        raise HTTPException(400, "Az utolsó adminisztrátor nem törölhető")
    auth.delete_user(user_id)
    log_activity(admin["id"], "user_delete", target_type="user", target_id=str(user_id),
                 detail={"email": target["email"]})
    return {"ok": True}
