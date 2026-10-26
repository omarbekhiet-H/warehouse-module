# -*- coding: utf-8 -*-
"""قسم المخازن — مخططات Pydantic."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WarehouseBase(BaseModel):
    name_ar: str = Field(min_length=1, max_length=128)
    location: str | None = None
    is_active: bool = True
    notes: str | None = None


class WarehouseCreate(WarehouseBase):
    pass


class WarehouseOut(WarehouseBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ItemBase(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    barcode: str | None = None
    name_ar: str = Field(min_length=1, max_length=255)
    unit: str = "حبة"
    category: str | None = None
    purchase_price: float = Field(ge=0, default=0)
    sale_price: float = Field(ge=0, default=0)
    reorder_level: float = Field(ge=0, default=0)
    standard_cost: float = Field(ge=0, default=0)
    costing_method: Literal["std", "avg", "fifo"] = "std"
    tax_account_id: int | None = None
    inventory_account_id: int | None = None
    cogs_account_id: int | None = None
    expense_account_id: int | None = None
    is_active: bool = True


class ItemCreate(ItemBase):
    pass


class ItemUpdate(BaseModel):
    name_ar: str | None = None
    barcode: str | None = None
    unit: str | None = None
    category: str | None = None
    purchase_price: float | None = Field(ge=0, default=None)
    sale_price: float | None = Field(ge=0, default=None)
    reorder_level: float | None = Field(ge=0, default=None)
    standard_cost: float | None = Field(ge=0, default=None)
    costing_method: Literal["std", "avg", "fifo"] | None = None
    tax_account_id: int | None = None
    inventory_account_id: int | None = None
    cogs_account_id: int | None = None
    expense_account_id: int | None = None
    is_active: bool | None = None


class ItemOut(ItemBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    avg_cost: float
    on_hand_qty: float


class MovementCreate(BaseModel):
    item_id: int
    warehouse_id: int
    movement_type: Literal["in", "out", "transfer", "adjust"]
    qty: float   # موجب لجميع الحركات؛ قد يكون سالبًا في adjust فقط (تسوية نقص)
    unit_cost: float | None = Field(ge=0, default=None)  # None ⇒ تُشتق تلقائيًا حسب طريقة التكلفة المعتمدة للصنف
    dest_warehouse_id: int | None = None   # إلزامية مع transfer (يتحقق في services)
    source_doc: str | None = None          # رقم المستند الأصلي: MR / PO / RECEIPT / SO ...
    remarks: str | None = None

    @field_validator("qty")
    @classmethod
    def _qty_sign(cls, v: float) -> float:
        if v == 0:
            raise ValueError("الكمية لا يمكن أن تكون صفرًا")
        return v


class MovementOut(MovementCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doc_no: str
    date: dt.datetime
    created_by: str | None
    cost_source: str | None
    valuation_value: float
    is_posted: bool


class StockLevel(BaseModel):
    item_id: int
    sku: str
    name_ar: str
    warehouse_id: int
    warehouse_name: str
    qty: float
    unit_cost: float          # التكلفة المطبقة حسب طريقة تقييم الصنف (std/avg)
    value: float              # التقييم بالدلالة المحاسبية = qty × unit_cost (IAS 2: التكلفة أو صافي القيمة القابلة للتحقق أيهما أقل)
    below_reorder: bool


# ---------- الجرد والتسويات ----------
class StockCountLineIn(BaseModel):
    item_id: int
    counted_qty: float = Field(ge=0)


class StockCountCreate(BaseModel):
    warehouse_id: int
    remarks: str | None = None
    auto_post_adjustments: bool = True   # اعتماد التسويات المحاسبية تلقائيًا عند اعتماد الجرد؟
    lines: list[StockCountLineIn] = Field(min_length=1)


class StockCountLineOut(StockCountLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    system_qty: float
    variance_qty: float
    unit_cost: float
    adjustment_moved: bool


class StockCountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    count_no: str
    warehouse_id: int
    date: dt.datetime
    status: str
    auto_post_adjustments: bool
    variance_value: float
    remarks: str | None
    created_by: str | None
    posted_by: str | None
    posted_at: dt.datetime | None
    lines: list[StockCountLineOut]
