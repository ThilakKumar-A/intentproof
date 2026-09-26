from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Origin(str, Enum):
    user = "user"
    tool_output = "tool_output"
    fetched_content = "fetched_content"
    ambiguous = "ambiguous"


UNTRUSTED_ORIGINS = frozenset({Origin.tool_output, Origin.fetched_content})


class PaymentIntent(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    rail: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0, le=Decimal("1000000000"))
    currency: str = "USD"
    recipient: str = Field(min_length=1, max_length=256)
    origin: Origin | None = None
    instruction: str = Field(default="", max_length=4000)
    untrusted_context: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("rail", "recipient", "agent_id")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("amount")
    @classmethod
    def _cents(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    @field_validator("untrusted_context")
    @classmethod
    def _clip_context(cls, value: list[str]) -> list[str]:
        return [item[:4000] for item in value[:20]]


class Decision(BaseModel):
    action: Literal["approve", "hold", "block"]
    rule: str
    reasons: list[str]


class SpendSnapshot(BaseModel):
    attempts_in_window: int = 0
    daily_total: Decimal = Decimal("0")
    weekly_total: Decimal = Decimal("0")
    by_rail: dict[str, Decimal] = Field(default_factory=dict)
    recipient_known: bool = False
    recipient_allowlisted: bool = False


class AllowRecipient(BaseModel):
    recipient: str = Field(min_length=1, max_length=256)
    rail: str = "*"

    @field_validator("recipient", "rail")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ApprovalBody(BaseModel):
    action: Literal["approve", "deny"]
