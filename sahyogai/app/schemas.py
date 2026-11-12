"""Pydantic models. Validation happens BEFORE your code runs.

Send user_id: "abc" and FastAPI rejects it with 422 automatically. You never
write validation code yourself.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    phone_number: str = Field(..., min_length=8, max_length=20)
    age: int | None = Field(None, ge=16, le=110)
    state: str | None = Field(None, max_length=60)
    occupation: str | None = Field(None, max_length=60)
    dependents: int = Field(0, ge=0, le=20)
    monthly_income_self_reported: float | None = Field(None, ge=0)
    language: str = "en"

    # Consent is STRUCTURALLY required, not a checkbox we hope someone ticked.
    consent: bool = Field(..., description="Explicit consent to process financial data")


class UserOut(BaseModel):
    id: int
    name: str
    phone_number: str
    age: int | None = None
    state: str | None = None
    occupation: str | None = None
    dependents: int = 0
    consent: bool

    model_config = {"from_attributes": True}


class QueryRequest(BaseModel):
    user_id: int
    query: str = Field(..., min_length=1, max_length=2000)
    channel: str = "web"


class QueryResponse(BaseModel):
    answer: str
    tool_used: str
    tool_args: dict[str, Any] = {}
    routing_mode: str
    synthesis_mode: str
    data: dict[str, Any] = {}
    latency_ms: int


class LoanOfferCreate(BaseModel):
    lender_name: str = Field(..., max_length=120)
    principal: float = Field(..., gt=0)
    annual_rate_pct: float = Field(..., ge=0, le=200)
    tenure_months: int = Field(..., gt=0, le=600)
    processing_fee: float = Field(0, ge=0)
    insurance_fee: float = Field(0, ge=0)


class GoalCreate(BaseModel):
    goal_name: str = Field(..., max_length=120)
    target_amount: float = Field(..., gt=0)
    months: int = Field(..., gt=0, le=600)


class EmiRequest(BaseModel):
    principal: float = Field(..., gt=0)
    annual_rate_pct: float = Field(..., ge=0, le=200)
    tenure_months: int = Field(..., gt=0, le=600)
    fees: float = Field(0, ge=0)


class UploadResult(BaseModel):
    parsed: int
    inserted: int
    duplicates: int
    recurring: int
