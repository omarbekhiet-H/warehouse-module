# -*- coding: utf-8 -*-
"""قسم المخازن — مخططات Pydantic."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    tax_account_id: int | None = None
    inventory_account_id: int | None = None
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
    is_active: bool | None = None


class ItemOut(ItemBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class MovementCreate(BaseModel):
    item_id: int
    warehouse_id: int
    movement_type: Literal["in", "out", "transfer", "adjust"]
    qty: float = Field(gt=0)
    unit_cost: float = Field(ge=0, default=0)
    remarks: str | None = None


class MovementOut(MovementCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doc_no: str
    date: dt.datetime
    created_by: str | None


class StockLevel(BaseModel):
    item_id: int
    sku: str
    name_ar: str
    warehouse_id: int
    warehouse_name: str
    qty: float
    value: float
    below_reorder: bool
