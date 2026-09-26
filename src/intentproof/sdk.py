"""Thin client. Origin is tagged from context the SDK saw, not from the model."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from intentproof.engine import instruction_in_context, normalize
from intentproof.config import Policy
from intentproof.models import Origin


class PaymentBlocked(Exception):
    def __init__(self, decision: dict) -> None:
        self.decision = decision
        super().__init__(decision.get("rule", "blocked"))


@dataclass
class OriginTracker:
    user_turns: list[str] = field(default_factory=list)
    untrusted: list[str] = field(default_factory=list)
    policy: Policy = field(default_factory=Policy)

    def note_user(self, text: str) -> None:
        if text:
            self.user_turns.append(text[-4000:])
            del self.user_turns[:-20]

    def note_tool_output(self, text: str) -> None:
        self._note_untrusted(text)

    def note_fetched(self, text: str) -> None:
        self._note_untrusted(text)

    def _note_untrusted(self, text: str) -> None:
        if text:
            self.untrusted.append(text[-4000:])
            del self.untrusted[:-20]

    def classify(self, instruction: str) -> tuple[Origin, list[str]]:
        needle = normalize(instruction)
        in_user = bool(needle) and any(needle in normalize(turn) for turn in self.user_turns)
        in_untrusted = instruction_in_context(instruction, self.untrusted, self.policy)
        context = self.untrusted[-8:]
        if in_user and not in_untrusted:
            return Origin.user, context
        if in_untrusted and not in_user:
            return Origin.fetched_content, context
        return Origin.ambiguous, context


class IntentProof:
    def __init__(self, base_url: str, api_key: str, agent_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.agent_id = agent_id
        self.tracker = OriginTracker()

    def pay(
        self,
        *,
        rail: str,
        amount: str | float,
        recipient: str,
        instruction: str,
        currency: str = "USD",
    ) -> dict:
        origin, context = self.tracker.classify(instruction)
        payload = {
            "agent_id": self.agent_id,
            "rail": rail,
            "amount": str(amount),
            "currency": currency,
            "recipient": recipient,
            "origin": origin.value,
            "instruction": instruction,
            "untrusted_context": context,
        }
        decision = self._post("/v1/intents", payload)
        if decision["action"] != "approve":
            raise PaymentBlocked(decision)
        return decision

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            raise RuntimeError(f"IntentProof {exc.code}: {detail}") from exc
