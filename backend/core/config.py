# -*- coding: utf-8 -*-
"""الإعدادات العامة للنظام — ملف مركزي واحد يشترك فيه كل الأقسام.

كل قسم (محاسبة/مخازن/مبيعات/مشتريات/خزينة/موارد بشرية) لا يقرأ الإعدادات مباشرة،
بل يعتمد على get_current_user / require_roles الموحّدة في هذا الملف.

المصادقة الآن حقيقية (JWT + RBAC):
    • فحص توقيع HS256 ومدة الصلاحية ونوع الرمز (access فقط).
    • إبطال التوكن الصادر قبل آخر تغيير لكلمة المرور (iat < password_changed_at).
    • رفض المستخدم الموقوف أو المحذوف من قاعدة البيانات لحظة الطلب.
    • require_roles(...) لفرض الأدوار على المسارات الحساسة.
"""
from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic_settings import BaseSettings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


class Settings(BaseSettings):
    APP_NAME: str = "ERP System"
    API_V1_PREFIX: str = "/api/v1"

    # ---- قاعدة البيانات ----
    # SQLite للتطوير السريع؛ للإنتاج اضبط DATABASE_URL على PostgreSQL مثلًا:
    #   postgresql+psycopg2://erp_user:erp_pass@localhost:5432/erp_db
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./erp.db")

    # ---- المصادقة ----
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    ADMIN_INITIAL_PASSWORD: str = os.getenv("ADMIN_INITIAL_PASSWORD", "")

    CORS_ORIGINS: list[str] = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> dict:
    """فحص الصلاحيات المشترك بين جميع الأقسام — JWT حقيقي الآن.

    يُرجع dict وليس كائن SQLAlchemy حتى تبقى طبقات الخدمات مستقلة عن موديل auth.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    from jose import JWTError, jwt

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="نوع الرمز غير صحيح")

    # تحقق حيّ من قاعدة البيانات: المستخدم موجود + نشط + كلمة المرور لم تتغير
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="رمز غير صالح")

    from backend.core.database import SessionLocal
    from backend.modules.auth import models as auth_models

    db = SessionLocal()
    try:
        user = db.get(auth_models.User, user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="الحساب غير فعال أو غير موجود")
        pca = user.password_changed_at
        if pca is not None and payload.get("iat"):
            iat_dt = dt.datetime.fromtimestamp(payload["iat"], tz=dt.timezone.utc)
            # SQLite يعيد datetime مجردة من المنطقة الزمنية؛ الـ iat مخزّن UTC
            pca_utc = pca if pca.tzinfo else pca.replace(tzinfo=dt.timezone.utc)
            if iat_dt < pca_utc:
                raise HTTPException(status_code=401,
                                    detail="انتهت صلاحية الجلسة بعد تغيير كلمة المرور")
        return {
            "user_id": str(user.id),
            "username": user.username,
            "full_name": user.full_name,
            "roles": user.role_names,
        }
    finally:
        db.close()


def require_roles(*codes: str):
    """مولّد اعتمادية لفرض الأدوار: require_roles('admin','accountant').

    استدعاؤه بلا أدوار يعني: أي مستخدم مسجّل دخوله يكفيه (للمسارات العامة المحمية).
    """
    async def _dep(user: dict = Depends(get_current_user)):
        if codes and not any(c in user.get("roles", []) for c in codes):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail="ليس لديك صلاحية لهذا الإجراء")
        # إعادة كائن User الكامل لمن يحتاج relationship بالأدوار
        from backend.core.database import SessionLocal
        from backend.modules.auth import models as auth_models
        db = SessionLocal()
        try:
            obj = db.get(auth_models.User, int(user["user_id"]))
            if obj is None:
                raise HTTPException(status_code=401, detail="المستخدم غير موجود")
            return obj
        finally:
            db.close()
    return _dep


def module_deps(prefix: str):
    """مولّد اعتماديات موحّد لكل قسم لضمان نفس شكل الاستجابة والوثائق."""
    async def _health():
        return {"module": prefix, "status": "ok"}
    return _health
