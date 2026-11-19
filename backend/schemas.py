"""Pydantic models for the API (request validation and the OpenAPI documentation at /api/docs)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VendorCode = Field(pattern=r"^[Vv]\d{3,}$", description="Anonymised vendor code, e.g. V012 (ordered by spend; V001 is the largest)")


class Health(BaseModel):
    status: Literal["ok"]
    mode: Literal["DEMO", "LIVE"]
    label: str = Field(description='Text for the top-bar badge: "DEMO" or "LIVE · gpt-oss-120b"')
    pinned_model: str
    last_routed_via: str | None = Field(None, description="X-Routed-Via header of the last live LLM call in this process")
    data_as_of: dict[str, Any]
    banner: str | None = Field(None, description="Persistent text for DEMO mode")


class BookingRequest(BaseModel):
    route: str = Field(min_length=1, max_length=30, examples=["AFBAN1"])
    vehicle_type: str = Field(min_length=1, max_length=10, examples=["OB22"])
    vendor: str = Field(pattern=r"^[Vv]\d{3,}$", examples=["V002"], description="Vendor code")
    qty: float = Field(gt=0, le=100, description="Planned load in MT", examples=[3])
    month: int = Field(ge=1, le=12, examples=[3])
    quoted_rs: float | None = Field(None, gt=0, description="The vendor's quote for this movement, in rupees")
    distance: float | None = Field(None, gt=0, le=10000)
    weekday: int | None = Field(None, ge=0, le=6)
    vehicle_capacity: float | None = Field(None, gt=0, le=100)


class BookingResponse(BaseModel):
    run_id: str
    recommended_action: Literal["downsize vehicle", "reassign vendor", "renegotiate rate", "reprice or top up", "verify quote", "proceed"]
    summary: str = Field(description="Two sentences. Numbers come from the tools; in LIVE mode the wording comes from the LLM through placeholders")
    draft: str | None = Field(None, description="Draft message for a person to edit and approve. Nothing is sent automatically")
    draft_template: str | None = None
    banner: str | None
    mode: str
    model_used: str
    guardrail_events: list[dict[str, Any]]
    guardrail_status: Literal["passed", "replaced"]
    inputs: dict[str, Any]
    assumed_defaults: dict[str, Any]
    warnings: list[str]
    loss_risk: dict[str, Any]
    fair_price: dict[str, Any]
    underload: dict[str, Any]
    alternatives: list[dict[str, Any]]
    current_rs_per_mt: float | None
    policy: dict[str, Any]


class AskTurn(BaseModel):
    question: str
    query_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500, examples=["Which vendors lose the most money?"])
    history: list[AskTurn] = Field(default_factory=list, max_length=3, description="Last <=3 turns ({question, query_id, params}), newest last. Never raw rows.")


class AskRerunRequest(BaseModel):
    run_id: str = Field(min_length=6, max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)


class AskFeedbackRequest(BaseModel):
    run_id: str = Field(min_length=6, max_length=40)
    kind: Literal["good", "fix_it", "request_review"]
    reasons: list[str] = Field(default_factory=list, description="For fix_it: wrong_data / wrong_chart / unclear_wording / other")
    note: str | None = Field(None, max_length=1000)


class DraftRequest(BaseModel):
    kind: Literal["hub_alert", "customer_eta", "renegotiation"]
    trip_id: str | None = Field(None, description="Required for hub_alert and customer_eta")
    vendor: str | None = Field(None, pattern=r"^[Vv]\d{3,}$", description="Required for renegotiation")
    route: str | None = Field(None, description="Renegotiation lane; defaults to the vendor's worst lane")
    vehicle_type: str | None = None


class DraftResponse(BaseModel):
    run_id: str
    kind: str
    template_id: str
    title: str
    text: str
    mode: str
    banner: str | None
    note: str = "Nothing is sent automatically. A person edits, approves or rejects this draft."


class DecisionRequest(BaseModel):
    run_id: str = Field(min_length=6, max_length=40)
    decision: Literal["approved", "rejected", "edited"]
    final_text: str | None = Field(None, max_length=8000, description="The text as approved or edited by the person")


class DecisionResponse(BaseModel):
    run_id: str
    decision: str
    recorded: bool = True
    note: str = "Recorded in the audit log only. The app never sends a message or changes a price."


class Page(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[dict[str, Any]]
