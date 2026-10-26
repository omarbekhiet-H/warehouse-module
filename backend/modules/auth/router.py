# -*- coding: utf-8 -*-
"""قسم المصادقة — نقاط النهاية (Router مستقل بالكامل).

المسارات العامة (بدون توكن): POST /auth/login ، POST /auth/refresh
باقي المسارات محمية، وإدارة المستخدمين مقصورة على دور admin.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.core.config import get_current_user, require_roles
from backend.core.database import get_db
from backend.core.schemas import ApiResponse
from backend.modules.auth import models, schemas, services

router = APIRouter(prefix="/auth", tags=["المصادقة والأمن"])


def _client_info(request: Request) -> tuple[str | None, str | None]:
    ip = request.headers.get("x-forwarded-for")
    if not ip:
        ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


@router.post("/login", response_model=ApiResponse[schemas.TokenPair], summary="تسجيل الدخول")
def login(data: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip, ua = _client_info(request)
    pair = services.authenticate(db, data, ip, ua)
    return ApiResponse.ok(pair, message="تم تسجيل الدخول بنجاح")


@router.post("/refresh", response_model=ApiResponse[schemas.TokenPair], summary="تحديث رمز الوصول")
def refresh(data: dict, request: Request, db: Session = Depends(get_db)):
    rt = data.get("refresh_token")
    if not rt:
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "refresh_token مطلوب")
    pair = services.refresh_tokens(db, rt)
    return ApiResponse.ok(pair, message="تم تحديث الرموز")


@router.post("/logout", response_model=ApiResponse[bool], summary="تسجيل الخروج")
def logout(data: dict | None = None, request: Request = None,
           db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    ip, ua = _client_info(request)
    rt = (data or {}).get("refresh_token")
    services.logout(db, rt, user.get("username"), ip, ua)
    return ApiResponse.ok(True, message="تم تسجيل الخروج")


@router.get("/me", response_model=ApiResponse[schemas.CurrentUserOut], summary="بيانات المستخدم الحالي")
def me(db: Session = Depends(get_db), user_obj: models.User = Depends(require_roles())):
    return ApiResponse.ok(schemas.CurrentUserOut(
        id=user_obj.id, username=user_obj.username, full_name=user_obj.full_name,
        email=user_obj.email, is_active=user_obj.is_active,
        roles=user_obj.role_names, last_login_at=user_obj.last_login_at))


@router.post("/change-password", response_model=ApiResponse[bool], summary="تغيير كلمة المرور")
def change_password(data: schemas.PasswordChange, request: Request,
                    db: Session = Depends(get_db),
                    user_obj: models.User = Depends(require_roles())):
    ip, ua = _client_info(request)
    services.change_password(db, user_obj, data, ip, ua)
    return ApiResponse.ok(True, message="تم تغيير كلمة المرور — أعد تسجيل الدخول")


# ---------- إدارة المستخدمين (admin فقط) ----------
@router.get("/users", response_model=ApiResponse[list[schemas.UserWithRoles]],
            summary="قائمة المستخدمين", dependencies=[Depends(require_roles("admin"))])
def list_users(search: str | None = None, db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.UserWithRoles.model_validate(u)
                           for u in services.list_users(db, search)])


@router.post("/users", response_model=ApiResponse[schemas.UserWithRoles],
             summary="إنشاء مستخدم", dependencies=[Depends(require_roles("admin"))])
def create_user(data: schemas.UserCreate, request: Request,
                db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    ip, ua = _client_info(request)
    u = services.create_user(db, data, actor=user.get("username"), ip=ip, ua=ua)
    return ApiResponse.ok(schemas.UserWithRoles.model_validate(u), message="تم إنشاء المستخدم")


@router.put("/users/{user_id}", response_model=ApiResponse[schemas.UserWithRoles],
            summary="تعديل مستخدم", dependencies=[Depends(require_roles("admin"))])
def update_user(user_id: int, data: schemas.UserUpdate, request: Request,
                db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    ip, _ = _client_info(request)
    u = services.update_user(db, user_id, data, actor=user.get("username"), ip=ip)
    return ApiResponse.ok(schemas.UserWithRoles.model_validate(u), message="تم التعديل")


@router.post("/users/{user_id}/reset-password", response_model=ApiResponse[bool],
             summary="إعادة تعيين كلمة مرور (مدير)",
             dependencies=[Depends(require_roles("admin"))])
def reset_password(user_id: int, data: schemas.AdminPasswordReset, request: Request,
                   db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    ip, _ = _client_info(request)
    services.admin_reset_password(db, user_id, data.new_password,
                                  actor=user.get("username"), ip=ip)
    return ApiResponse.ok(True, message="تم إعادة التعيين — سيُطلب تغييرها عند أول دخول")


@router.get("/roles", response_model=ApiResponse[list[schemas.RoleOut]], summary="الأدوار المتاحة")
def roles(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return ApiResponse.ok([schemas.RoleOut.model_validate(r) for r in services.list_roles(db)])


@router.get("/audit", response_model=ApiResponse[list[dict]], summary="سجل تدقيق الدخول",
            dependencies=[Depends(require_roles("admin"))])
def audit(limit: int = 100, db: Session = Depends(get_db)):
    rows = services.recent_audit(db, limit)
    return ApiResponse.ok([{
        "event": r.event, "username": r.username, "ip": r.ip_address,
        "detail": r.detail, "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows])
