# -*- coding: utf-8 -*-
"""نماذج قاعدة بيانات المستخدمين والصلاحيات ورموز التحديث وسجل التدقيق.

الجداول:
    auth_users         — ملف المستخدم (اسم الدخول، الاسم الكامل، بصمة bcrypt لكلمة المرور).
    auth_roles         — الأدوار الوظيفية (admin / accountant / storekeeper / hr / cashier ...).
    auth_user_roles    — ربط متعدد بين المستخدم والدور.
    auth_refresh_tokens— رموز التحديث الصالحة، تُلغى عند تسجيل الخروج أو تغيير كلمة المرور.
    auth_audit_log     — سجل تدقيق لأحداث الدخول الفاشلة/الناجحة وحماية كلمات المرور.

ملاحظات مهنية:
    • لا تُخزَّن كلمة المرور مطلقًا؛ تُخزَّن بصمة bcrypt فقط.
    • password_changed_at يقارَن بـ iat داخل JWT لإبطال الجلسات القديمة بعد
      تغيير كلمة المرور — لذا يجب أن يُقرأ/يُخزَّن كوقت UTC مع المنطقة الزمنية
      على SQLite وPostgreSQL alike عبر النوع UTCDatetime أدناه.
    • revoked_at في جدول refresh tokens يضمن عدم استخدام الرمز بعد الإلغاء.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (Boolean, Column, ForeignKey, Integer, String, Table,
                        UniqueConstraint)
from sqlalchemy.types import TypeDecorator, TEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class UTCDatetime(TypeDecorator):
    """تخزين/قراءة تواريخ UTC مع معلومات المنطقة على جميع قواعد البيانات.

    المشكلة المحلولة: SQLAlchemy يعيد من SQLite تاريخًا مجردًا (بدون tz)،
    بينما iat داخل JWT عددي UTC — المقارنة بينهما بدون تطبيع تسبب إبطالًا
    خاطئًا للجلسات. هنا نحوّل كل شيء إلى aware UTC في الاتجاهين.
    """
    impl = TEXT            # نص ISO متوافق مع SQLite وPostgreSQL (timestamptz يستقبله)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:                  # نفترض أن المجرد هو UTC
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, dt.datetime):        # drivers قد تعيد datetime مباشرة
            return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
        parsed = dt.datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


user_roles = Table(
    "auth_user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("auth_users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("auth_roles.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "auth_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(128), unique=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    hashed_password: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(UTCDatetime)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(UTCDatetime)
    # يُستخدم لإبطال الـ JWT الصادرة قبل هذه اللحظة عند تغيير كلمة المرور
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(UTCDatetime)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDatetime, default=utcnow)

    roles: Mapped[list["Role"]] = relationship(
        secondary=user_roles, back_populates="users", lazy="joined")

    @property
    def role_names(self) -> list[str]:
        return [r.code for r in self.roles]

    def has_any(self, codes: list[str]) -> bool:
        if not codes:
            return True
        return any(r.code in codes for r in self.roles)


class Role(Base):
    __tablename__ = "auth_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255))

    users: Mapped[list["User"]] = relationship(secondary=user_roles, back_populates="roles")


class RefreshToken(Base):
    __tablename__ = "auth_refresh_tokens"
    __table_args__ = (UniqueConstraint("jti", name="uq_auth_refresh_jti"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("auth_users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDatetime)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UTCDatetime)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDatetime, default=utcnow)

    user: Mapped["User"] = relationship()


class AuditLog(Base):
    __tablename__ = "auth_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[str] = mapped_column(String(32), index=True)  # login_ok/login_fail/logout/pw_change/user_created...
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDatetime, default=utcnow, index=True)
