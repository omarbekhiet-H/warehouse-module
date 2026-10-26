# -*- coding: utf-8 -*-
"""مخططات Pydantic لوحدة المصادقة."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool = False


class CurrentUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str | None = None
    email: str | None = None
    is_active: bool
    roles: list[str] = []
    last_login_at: dt.datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=128)
    email: str | None = None
    phone: str | None = None
    roles: list[str] = []
    is_active: bool = True

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        """سياسة كلمة مرور دنيا: 8 أحرف مع حرف كبير ورقم على الأقل."""
        if not any(c.isupper() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("كلمة المرور يجب أن تحتوي حرفًا كبيرًا ورقمًا على الأقل (8 أحرف)")
        return v


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    is_active: bool | None = None
    roles: list[str] | None = None


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("كلمة المرور الجديدة يجب أن تحتوي حرفًا كبيرًا ورقمًا على الأقل")
        return v


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name_ar: str
    description: str | None = None


class UserWithRoles(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    is_active: bool
    must_change_password: bool
    last_login_at: dt.datetime | None = None
    created_at: dt.datetime
    roles: list[RoleOut] = []
