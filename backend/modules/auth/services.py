# -*- coding: utf-8 -*-
"""منطق أعمال المصادقة: تسجيل الدخول، إصدار/تحديث/إلغاء الرموز، إدارة المستخدمين.

معايير أمنية مطبَّقة:
    • bcrypt لتشفير كلمات المرور (cost factor افتراضي 12).
    • JWT (HS256) مع jti و iat/exp؛ الوصول قصير الأجل والـ refresh طويل الأجل.
    • إبطال الجلسات بعد تغيير كلمة المرور عبر مقارنة iat بـ password_changed_at.
    • قفل مؤقت للحساب بعد عدد محاولات فاشلة (brute-force protection).
    • سجل تدقيق (Audit Log) لكل حدث دخول/خروج/تغيير كلمة مرور/إنشاء مستخدم.
"""
from __future__ import annotations

import datetime as dt
import secrets
import uuid

import bcrypt
from fastapi import HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.modules.auth import models, schemas

settings = get_settings()

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15

DEFAULT_ROLES = [
    ("admin", "مدير النظام", "صلاحية كاملة على جميع الأقسام وإدارة المستخدمين"),
    ("accountant", "محاسب", "القيود والأستاذ والتقارير المالية واعتماد المستندات"),
    ("storekeeper", "أمين مخزن", "الحركات المخزنية والجرد وصرف المواد"),
    ("sales", "مندوب مبيعات", "فواتير البيع والعملاء والتحصيلات"),
    ("purchasing", "أخصائي مشتريات", "أوامر الشراء وأذون الاستلام وفواتير الموردين"),
    ("hr", "شئون عاملين", "الموظفون والحضور والسلف ومسير الرواتب"),
    ("cashier", "أمين خزينة", "سندات القبض والصرف والشيكات ووردية الخزينة"),
    ("auditor", "مراجع", "اطلاع فقط على جميع التقارير والمستندات"),
]


def _now_utc() -> dt.datetime:
    """UTC الآن بدقة الثواني (تقطيع للأسفل).

    السبب: حقل password_changed_at يُخزَّن بدقة الميكروثانية، بينما iat داخل
    JWT عدد صحيح ثوانٍ (int يسقط الكسور). بدون التقطيع قد تصبح:
        pca(ث+0.4) > int(iat)(ث)  →  يُلغى التوكن الصادرة بعد التغيير مباشرة!
    بتقطيع pca إلى بداية الثانية يصبح دائمًا: pca <= أي iat صدر في نفس الثانية
    أو بعدها، وتبقى مقارنة الإبطال دقيقة للتوكنات الصادرة قبل التغيير الفعلي.
    """
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


# ---------------- كلمات المرور ----------------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ---------------- التدقيق ----------------
def audit(db: Session, event: str, username: str | None = None,
          ip: str | None = None, ua: str | None = None, detail: str | None = None) -> None:
    db.add(models.AuditLog(event=event, username=username, ip_address=ip,
                           user_agent=(ua or "")[:255] or None, detail=detail))
    # لا نعمل commit هنا؛ المتحكم هو الذي ينهي المعاملة


# ---------------- الرموز ----------------
def _make_token(user: models.User, token_use: str, expires_delta: dt.timedelta) -> tuple[str, str]:
    jti = uuid.uuid4().hex
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "roles": user.role_names,
        "type": token_use,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    return token, jti


def create_access_token(user: models.User) -> str:
    delta = dt.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token, _ = _make_token(user, "access", delta)
    return token


def create_refresh_token(db: Session, user: models.User) -> str:
    """يُصدر refresh token ويكتبه في الجدول — commit فوري ومغلق.

    الكتابة تتم بـ session مستقلة (commit+close قبل الإرجاع) حتى لا يبقى أي
    UPDATE معلّق في جلسة الطلب يُفلَش لاحقًا بعد إصدار التوكنات (وهو ما كان
    يسبق password_changed_at زمنيًا ويُفسد مقارنة إبطال الجلسات).
    """
    from backend.core.database import SessionLocal
    delta = dt.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    token, jti = _make_token(user, "refresh", delta)
    own = SessionLocal()
    try:
        own.add(models.RefreshToken(jti=jti, user_id=user.id,
                                    expires_at=_now_utc() + delta))
        own.commit()
    finally:
        own.close()
    return token


def issue_token_pair(db: Session, user: models.User) -> schemas.TokenPair:
    return schemas.TokenPair(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(db, user),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        must_change_password=user.must_change_password,
    )


# ---------------- تسجيل الدخول ----------------
def authenticate(db: Session, data: schemas.LoginRequest,
                 ip: str | None = None, ua: str | None = None) -> schemas.TokenPair:
    """يتحقق من بيانات الدخول ويصدر زوج الرموز.

    ترتيب زمني حرج وموثّق (سبب أخطاء سابقة في إبطال الجلسات):
      1) تُقرأ البصمة والتوكينز المعلقة وتُلغى، ويُكتب last_login_at — كلها commit.
      2) حساب bcrypt البطيء (~200ms) يتم بعد الـ commit ولا يلمس أي حقل ORM.
      3) تُخرَج الرموز (iat = الآن) بعد كل كتابات password_changed_at المحتملة،
         لذا يكون iat >= pca دائمًا للتوكنات الصادرة لاحقًا — لا إباطال ذاتي.
    """
    user = db.scalar(select(models.User).where(models.User.username == data.username))
    now = _now_utc()

    def fail(detail: str):
        audit(db, "login_fail", data.username, ip, ua, detail)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail)

    if not user:
        # رسالة موحدة حتى لا نكشف وجود اسم المستخدم من عدمه
        fail("بيانات الدخول غير صحيحة")

    if user.locked_until and user.locked_until > now:
        fail(f"الحساب مقفل مؤقتًا بسبب محاولات متكررة خاطئة — أعد المحاولة بعد {LOCK_MINUTES} دقيقة")

    if not user.is_active:
        fail("الحساب موقوف — راجع مدير النظام")

    hashed = user.hashed_password          # قراءة قبل الحساب البطيء
    uid = user.id

    if not verify_password(data.password, hashed):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + dt.timedelta(minutes=LOCK_MINUTES)
            user.failed_login_count = 0
        fail("بيانات الدخول غير صحيحة")

    # نجاح: إعادة تحميل الكائن (قد يكون expunged/منتهي بعد fail path) وتحديث الحالة
    user = db.get(models.User, uid)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    _purge_expired_tokens(db, user.id)     # تنظيف منتهي الصلاحية فقط — لا يمس الجلسات الحية
    audit(db, "login_ok", user.username, ip, ua)
    db.commit()                            # كل الكتابات انتهت قبل إصدار الرموز

    return issue_token_pair(db, user)      # iat >= pca مضمون


def refresh_tokens(db: Session, refresh_token_str: str) -> schemas.TokenPair:
    try:
        payload = jwt.decode(refresh_token_str, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="رمز التحديث غير صالح أو منتهي")
    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="نوع الرمز غير صحيح")

    stored = db.scalar(select(models.RefreshToken).where(models.RefreshToken.jti == payload.get("jti")))
    if not stored or stored.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="رمز التحديث ملغى")

    user = db.get(models.User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="المستخدم غير فعال")

    # تدوير رمز التحديث (Refresh Token Rotation): إلغاء القديم وإصدار زوج جديد
    stored.revoked_at = _now_utc()
    pair = issue_token_pair(db, user)
    db.commit()
    return pair


def logout(db: Session, refresh_token_str: str | None, username: str | None,
           ip: str | None = None, ua: str | None = None) -> None:
    if refresh_token_str:
        try:
            payload = jwt.decode(refresh_token_str, settings.SECRET_KEY, algorithms=["HS256"])
            stored = db.scalar(select(models.RefreshToken).where(
                models.RefreshToken.jti == payload.get("jti")))
            if stored and stored.revoked_at is None:
                stored.revoked_at = _now_utc()
        except JWTError:
            pass
    audit(db, "logout", username, ip, ua)
    db.commit()


def revoke_all_user_tokens(db: Session, user_id: int) -> None:
    """يُلغي كل رموز التحديث للمستخدم (بعد تغيير كلمة المرور أو إيقاف الحساب)."""
    now = _now_utc()
    for tok in db.scalars(select(models.RefreshToken).where(
            models.RefreshToken.user_id == user_id,
            models.RefreshToken.revoked_at.is_(None))):
        tok.revoked_at = now


def _purge_expired_tokens(db: Session, user_id: int) -> None:
    """يحذف رموز التحديث المنتهية صلاحيته للمستخدم (تنظيف داخلي عند الدخول).

    لا يلمس الرموز السارية حتى لا يُبطل جلسات أخرى نشطة لنفس المستخدم.
    """
    now = _now_utc()
    for tok in db.scalars(select(models.RefreshToken).where(
            models.RefreshToken.user_id == user_id,
            models.RefreshToken.expires_at < now)):
        db.delete(tok)


# ---------------- إدارة المستخدمين ----------------
def ensure_roles(db: Session) -> None:
    for code, name_ar, desc in DEFAULT_ROLES:
        if not db.scalar(select(models.Role).where(models.Role.code == code)):
            db.add(models.Role(code=code, name_ar=name_ar, description=desc))
    db.commit()


def _resolve_roles(db: Session, codes: list[str]) -> list[models.Role]:
    roles = []
    for c in codes:
        r = db.scalar(select(models.Role).where(models.Role.code == c))
        if not r:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"دور غير معروف: {c}")
        roles.append(r)
    return roles


def create_user(db: Session, data: schemas.UserCreate, actor: str | None = None,
                ip: str | None = None, ua: str | None = None) -> models.User:
    if db.scalar(select(models.User).where(models.User.username == data.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "اسم المستخدم موجود بالفعل")
    if data.email and db.scalar(select(models.User).where(models.User.email == data.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "البريد الإلكتروني مستخدم بالفعل")
    ensure_roles(db)
    user = models.User(
        username=data.username, full_name=data.full_name, email=data.email,
        phone=data.phone, hashed_password=hash_password(data.password),
        is_active=data.is_active, must_change_password=True,
        password_changed_at=_now_utc())
    user.roles = _resolve_roles(db, data.roles)
    db.add(user)
    db.flush()
    audit(db, "user_created", data.username, ip, ua, f"by={actor} roles={data.roles}")
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session, search: str | None = None) -> list[models.User]:
    q = select(models.User).order_by(models.User.username)
    if search:
        like = f"%{search}%"
        q = q.where(models.User.username.ilike(like) | models.User.full_name.ilike(like))
    # roles علاقة eager-loading متعددة ⇒ يجب unique() على النتيجة (سلوك SQLAlchemy 2 إلزامي)
    return list(db.scalars(q).unique())


def get_user_or_404(db: Session, user_id: int) -> models.User:
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "المستخدم غير موجود")
    return user


def update_user(db: Session, user_id: int, data: schemas.UserUpdate,
                actor: str | None = None, ip: str | None = None) -> models.User:
    user = get_user_or_404(db, user_id)
    patch = data.model_dump(exclude_unset=True)
    roles = patch.pop("roles", None)
    for k, v in patch.items():
        setattr(user, k, v)
    if roles is not None:
        ensure_roles(db)
        user.roles = _resolve_roles(db, roles)
    if data.is_active is False:
        revoke_all_user_tokens(db, user.id)
    audit(db, "user_updated", user.username, ip, None, f"by={actor}")
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, user: models.User, data: schemas.PasswordChange,
                    ip: str | None = None, ua: str | None = None) -> None:
    hashed = user.hashed_password          # قراءة قبل الحساب البطيء
    if not verify_password(data.old_password, hashed):
        audit(db, "pw_change_fail", user.username, ip, ua)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "كلمة المرور الحالية غير صحيحة")
    new_hash = hash_password(data.new_password)   # bcrypt خارج نافذة الجلسة النشطة
    user.hashed_password = new_hash
    user.password_changed_at = _now_utc()
    user.must_change_password = False
    revoke_all_user_tokens(db, user.id)   # إبطال كل الجلسات القديمة
    audit(db, "pw_change", user.username, ip, ua)
    db.commit()


def admin_reset_password(db: Session, user_id: int, new_password: str,
                         actor: str | None = None, ip: str | None = None) -> None:
    user = get_user_or_404(db, user_id)
    user.hashed_password = hash_password(new_password)
    user.password_changed_at = _now_utc()
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    revoke_all_user_tokens(db, user.id)
    audit(db, "pw_admin_reset", user.username, ip, None, f"by={actor}")
    db.commit()


def list_roles(db: Session) -> list[models.Role]:
    ensure_roles(db)
    return list(db.scalars(select(models.Role).order_by(models.Role.code)))


def recent_audit(db: Session, limit: int = 100) -> list[models.AuditLog]:
    return list(db.scalars(select(models.AuditLog).order_by(
        models.AuditLog.created_at.desc()).limit(min(limit, 500))))


# ---------------- Seed أول تشغيل ----------------
def bootstrap_admin(db: Session) -> None:
    """ينشئ مديرًا أوليًا إذا لم يوجد أي مستخدم.

    كلمة المرور الافتراضية تُقرأ من متغير البيئة ADMIN_INITIAL_PASSWORD؛ وإن لم تُضبط
    تُولَّد عشوائيًا وتُطبع في اللوج مرة واحدة فقط (سلوك آمن للتطوير الأولي).
    """
    ensure_roles(db)
    if db.scalar(select(models.User).limit(1)):
        return
    generated = None
    pwd = settings.ADMIN_INITIAL_PASSWORD
    if not pwd:
        pwd = generated = "Erp@" + secrets.token_urlsafe(9)
    admin_role = db.scalar(select(models.Role).where(models.Role.code == "admin"))
    user = models.User(
        username="admin", full_name="مدير النظام",
        hashed_password=hash_password(pwd),
        must_change_password=True, password_changed_at=_now_utc(),
        roles=[admin_role])
    db.add(user)
    audit(db, "bootstrap_admin", "admin", None, None, "created on first startup")
    db.commit()
    if generated:
        print("=" * 60)
        print("[BOOTSTRAP] تم إنشاء المستخدم admin بكلمة مرور مؤقتة:")
        print(f"[BOOTSTRAP]   {generated}")
        print("[BOOTSTRAP] غيّرها فور أول تسجيل دخول.")
        print("=" * 60)
