from __future__ import annotations

import json
import urllib.request

from intentproof.models import Decision, PaymentIntent


def slack_hold(webhook_url: str, decision_id: str, intent: PaymentIntent, decision: Decision) -> None:
    if not webhook_url:
        return
    text = (
        f"IntentProof hold {decision_id}: {intent.amount} {intent.currency} "
        f"to {intent.recipient} via {intent.rail} ({decision.rule}). "
        f"{decision.reasons[0] if decision.reasons else ''}"
    )
    body = json.dumps({"text": text[:500]}).encode()
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
    except Exception:
        # A missed Slack ping must not turn into an approval.
        return
