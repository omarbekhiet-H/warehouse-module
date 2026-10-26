# -*- coding: utf-8 -*-
"""نموذج استجابة موحّد لكل الأقسام — يضمن شكل JSON واحدًا في كل الواجهات."""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = ""
    data: T | None = None

    @classmethod
    def ok(cls, data: Any = None, message: str = "") -> "ApiResponse":
        return cls(success=True, message=message, data=data)

    @classmethod
    def fail(cls, message: str) -> "ApiResponse":
        return cls(success=False, message=message, data=None)


class PageParams(BaseModel):
    page: int = 1
    page_size: int = 20
    search: str | None = None
