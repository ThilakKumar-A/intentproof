from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response

from intentproof.config import Settings
from intentproof.engine import evaluate
from intentproof.models import AllowRecipient, ApprovalBody, PaymentIntent
from intentproof.notify import slack_hold
from intentproof.store import Store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        store.close()

    app = FastAPI(title="IntentProof", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store

    def authorized(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        if token not in settings.api_keys:
            raise HTTPException(status_code=401, detail="invalid api key")

    page = (Path(__file__).parent / "console.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/v1/intents", dependencies=[Depends(authorized)])
    def create_intent(intent: PaymentIntent) -> dict:
        store.expire(settings.approval_timeout_seconds)
        snapshot = store.snapshot(
            intent, velocity_window=settings.policy.velocity_window_seconds
        )
        decision = evaluate(intent, settings.policy, snapshot)
        decision_id = store.save(intent, decision)
        if decision.action == "hold":
            slack_hold(settings.slack_webhook_url, decision_id, intent, decision)
        exposure = store.exposure(intent.agent_id)
        return {
            "id": decision_id,
            "action": decision.action,
            "rule": decision.rule,
            "reasons": decision.reasons,
            "exposure": exposure,
        }

    @app.post("/v1/approvals/{decision_id}", dependencies=[Depends(authorized)])
    def resolve_approval(decision_id: str, body: ApprovalBody) -> dict:
        row = store.resolve(decision_id, body.action, settings.approval_timeout_seconds)
        if row is None:
            raise HTTPException(status_code=404, detail="decision not found")
        return row

    @app.post("/v1/allowlist", dependencies=[Depends(authorized)])
    def allow(body: AllowRecipient) -> dict:
        store.allow(body.recipient, body.rail)
        return {"recipient": body.recipient, "rail": body.rail}

    @app.get("/v1/agents/{agent_id}/exposure", dependencies=[Depends(authorized)])
    def exposure(agent_id: str) -> dict:
        return store.exposure(agent_id)

    @app.get("/v1/audit", dependencies=[Depends(authorized)])
    def audit(agent_id: str | None = None, limit: int = 50) -> dict:
        return {"decisions": store.audit(agent_id, min(limit, 200))}

    @app.get("/v1/summary", dependencies=[Depends(authorized)])
    def summary() -> dict:
        agents = []
        for agent_id in store.agent_ids():
            agents.append(
                {
                    **store.exposure(agent_id),
                    "decisions": store.audit(agent_id, limit=20),
                }
            )
        return {"agents": agents}

    return app


app = create_app()


def main() -> None:
    from intentproof.cli import main as cli_main

    cli_main(["serve"])
