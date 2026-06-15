"""Pydantic v2 kérés-modellek."""
from typing import Optional

from pydantic import BaseModel, Field


# ---- Auth ----
class SetupRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# ---- Felhasználók ----
class UserCreate(BaseModel):
    email: str
    password: str
    name: str
    role: str = Field("viewer", description="admin | viewer")


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class ResetPasswordRequest(BaseModel):
    new_password: str


class UserScopeUpdate(BaseModel):
    """Viewer hatóköre: a hozzárendelt API kulcs- és workspace-id-k (teljes csere)."""
    api_key_ids: list[str] = Field(default_factory=list)
    workspace_ids: list[str] = Field(default_factory=list)


# ---- Admin API kulcsok ----
class AdminKeyCreate(BaseModel):
    label: str
    value: str


class AdminKeyUpdate(BaseModel):
    label: Optional[str] = None
    is_active: Optional[bool] = None


# ---- Sync ----
class SyncRunRequest(BaseModel):
    source: str = Field(..., description="usage | cost | claude_code | metadata")


class BackfillRequest(BaseModel):
    source: str
    start: str = Field(..., description="YYYY-MM-DD (inkluzív)")
    end: str = Field(..., description="YYYY-MM-DD (inkluzív)")


# ---- Beállítások ----
class SettingsUpdate(BaseModel):
    values: dict


# ---- Modell-árazás (becsült költség) ----
class PricingItem(BaseModel):
    model_pattern: str = Field(..., pattern=r"^[a-z0-9][a-z0-9._-]*$",
                               description="usage_facts.model előtag-illesztés (leghosszabb prefix nyer)")
    display_name: str
    input_usd_per_mtok: float = Field(..., ge=0)
    cache_write_5m_usd_per_mtok: float = Field(..., ge=0)
    cache_write_1h_usd_per_mtok: float = Field(..., ge=0)
    cache_read_usd_per_mtok: float = Field(..., ge=0)
    output_usd_per_mtok: float = Field(..., ge=0)
    sort_order: int = Field(100, ge=0)
    source: str = Field("manual", pattern=r"^(seed|manual|official)$",
                        description="provenance: honnan származik az ár (csak informatív)")


class PricingUpdate(BaseModel):
    items: list[PricingItem]


# ---- Szervezet egyenlege (kézi horgony) ----
class BalanceUpdate(BaseModel):
    amount_usd: float = Field(..., ge=0, description="egyenleg a horgony időpontján (USD)")
    anchor_ts: str = Field(..., description="UTC ISO időbélyeg (a frontend toISOString()-zal küldi)")
