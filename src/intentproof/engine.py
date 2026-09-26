"""Deterministic payment decisions. First matching rule wins."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from intentproof.config import Policy
from intentproof.models import UNTRUSTED_ORIGINS, Decision, Origin, PaymentIntent, SpendSnapshot

_NORM = re.compile(r"[^a-z0-9.$]+")
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    folded = text.casefold().replace("$", " $ ")
    cleaned = _NORM.sub(" ", folded)
    return _SPACE.sub(" ", cleaned).strip()


def instruction_in_context(instruction: str, contexts: list[str], policy: Policy) -> bool:
    """True when the payment instruction closely matches untrusted text."""
    needle = normalize(instruction)
    if len(needle) < policy.injection_min_chars:
        return False
    for raw in contexts:
        hay = normalize(raw)[:4000]
        if not hay:
            continue
        if needle in hay:
            return True
        if _window_ratio(needle, hay, policy.injection_ratio) >= policy.injection_ratio:
            return True
    return False


def _window_ratio(needle: str, hay: str, threshold: float) -> float:
    width = min(len(hay), int(len(needle) * 1.3) + 8)
    if width < len(needle) // 2:
        return 0.0
    step = max(8, len(needle) // 4)
    matcher = SequenceMatcher(a=needle)
    best = 0.0
    last = max(1, len(hay) - width + 1)
    for start in range(0, last, step):
        matcher.set_seq2(hay[start : start + width])
        if matcher.real_quick_ratio() < threshold:
            continue
        score = matcher.ratio()
        if score > best:
            best = score
            if best >= threshold:
                return best
    return best


def _trusted(snapshot: SpendSnapshot) -> bool:
    return snapshot.recipient_known or snapshot.recipient_allowlisted


def evaluate(intent: PaymentIntent, policy: Policy, snapshot: SpendSnapshot) -> Decision:
    amount = intent.amount
    origin = intent.origin
    trusted = _trusted(snapshot)
    echoed = instruction_in_context(intent.instruction, intent.untrusted_context, policy)

    if amount <= 0:
        return Decision(action="block", rule="invalid_amount", reasons=["amount must be positive"])

    if snapshot.attempts_in_window >= policy.velocity_max:
        return Decision(
            action="block",
            rule="velocity",
            reasons=[
                f"{snapshot.attempts_in_window} attempts in the last "
                f"{policy.velocity_window_seconds}s (max {policy.velocity_max})"
            ],
        )

    untrusted = origin in UNTRUSTED_ORIGINS
    if untrusted and (echoed or amount > policy.non_user_block_above):
        why = "instruction matches untrusted content" if echoed else (
            f"amount {amount} is above {policy.non_user_block_above}"
        )
        return Decision(
            action="block",
            rule="untrusted_origin",
            reasons=[f"origin {origin.value}; {why}"],
        )

    if echoed and origin is Origin.user and not trusted:
        return Decision(
            action="block",
            rule="injection_new_recipient",
            reasons=["instruction matches content the agent read, and the recipient is not trusted"],
        )

    if echoed:
        return Decision(
            action="hold",
            rule="injection_review",
            reasons=["instruction matches recent tool output or fetched content"],
        )

    if untrusted:
        return Decision(
            action="hold",
            rule="untrusted_origin_low",
            reasons=[
                f"origin {origin.value} and amount {amount} is at or under "
                f"{policy.non_user_block_above}; needs a person"
            ],
        )

    if not trusted and amount > policy.unknown_recipient_block_above:
        return Decision(
            action="block",
            rule="unknown_recipient",
            reasons=[
                f"recipient is not trusted and amount {amount} is above "
                f"{policy.unknown_recipient_block_above}"
            ],
        )

    if origin is None or origin is Origin.ambiguous:
        return Decision(
            action="hold",
            rule="ambiguous_origin",
            reasons=["intent origin is missing or ambiguous"],
        )

    if intent.currency != "USD":
        return Decision(
            action="hold",
            rule="unsupported_currency",
            reasons=[f"currency {intent.currency} is not aggregated in v1"],
        )

    projected_day = snapshot.daily_total + amount
    if projected_day > policy.daily_ceiling:
        return Decision(
            action="hold",
            rule="daily_ceiling",
            reasons=[f"daily total would be {projected_day}, ceiling is {policy.daily_ceiling}"],
        )

    projected_week = snapshot.weekly_total + amount
    if projected_week > policy.weekly_ceiling:
        return Decision(
            action="hold",
            rule="weekly_ceiling",
            reasons=[f"weekly total would be {projected_week}, ceiling is {policy.weekly_ceiling}"],
        )

    if amount > policy.per_tx_cap:
        return Decision(
            action="hold",
            rule="per_tx_cap",
            reasons=[f"amount {amount} is above the per-transaction cap {policy.per_tx_cap}"],
        )

    if not trusted:
        return Decision(
            action="hold",
            rule="new_recipient",
            reasons=["recipient has not been approved before"],
        )

    if origin is Origin.user and amount <= policy.auto_approve_max:
        return Decision(
            action="approve",
            rule="auto_approve",
            reasons=["user origin, trusted recipient, amount under the auto-approve floor"],
        )

    return Decision(
        action="hold",
        rule="above_auto_approve",
        reasons=[
            f"amount {amount} is above the auto-approve floor {policy.auto_approve_max}"
        ],
    )
