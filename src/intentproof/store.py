from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from intentproof.models import Decision, PaymentIntent, SpendSnapshot


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def recipient_key(value: str) -> str:
    return " ".join(value.casefold().split())


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        self._conn.close()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
              id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              rail TEXT NOT NULL,
              amount TEXT NOT NULL,
              currency TEXT NOT NULL,
              recipient TEXT NOT NULL,
              origin TEXT,
              instruction TEXT NOT NULL,
              action TEXT NOT NULL,
              rule TEXT NOT NULL,
              reasons TEXT NOT NULL,
              approval_status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              resolved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_agent_time
              ON decisions(agent_id, created_at);
            CREATE TABLE IF NOT EXISTS allowlist (
              recipient TEXT NOT NULL,
              rail TEXT NOT NULL,
              PRIMARY KEY (recipient, rail)
            );
            CREATE TABLE IF NOT EXISTS known_recipients (
              agent_id TEXT NOT NULL,
              recipient TEXT NOT NULL,
              PRIMARY KEY (agent_id, recipient)
            );
            """
        )
        self._conn.commit()

    def snapshot(self, intent: PaymentIntent, *, velocity_window: int) -> SpendSnapshot:
        now = _now()
        window_start = _iso(now - timedelta(seconds=velocity_window))
        day_start = _iso(now - timedelta(days=1))
        week_start = _iso(now - timedelta(days=7))
        key = recipient_key(intent.recipient)
        with self._lock:
            attempts = self._conn.execute(
                "SELECT COUNT(*) AS n FROM decisions WHERE agent_id = ? AND created_at >= ?",
                (intent.agent_id, window_start),
            ).fetchone()["n"]
            rows = self._conn.execute(
                """
                SELECT rail, amount, created_at FROM decisions
                WHERE agent_id = ? AND created_at >= ? AND currency = 'USD'
                  AND (action = 'approve' OR (action = 'hold' AND approval_status = 'approved'))
                """,
                (intent.agent_id, week_start),
            ).fetchall()
            known = self._conn.execute(
                "SELECT 1 FROM known_recipients WHERE agent_id = ? AND recipient = ?",
                (intent.agent_id, key),
            ).fetchone()
            listed = self._conn.execute(
                """
                SELECT 1 FROM allowlist
                WHERE recipient = ? AND (rail = ? OR rail = '*')
                """,
                (key, intent.rail),
            ).fetchone()
        daily = Decimal("0")
        weekly = Decimal("0")
        by_rail: dict[str, Decimal] = {}
        for row in rows:
            amount = Decimal(row["amount"])
            weekly += amount
            by_rail[row["rail"]] = by_rail.get(row["rail"], Decimal("0")) + amount
            if row["created_at"] >= day_start:
                daily += amount
        return SpendSnapshot(
            attempts_in_window=int(attempts),
            daily_total=daily,
            weekly_total=weekly,
            by_rail=by_rail,
            recipient_known=known is not None,
            recipient_allowlisted=listed is not None,
        )

    def save(
        self,
        intent: PaymentIntent,
        decision: Decision,
    ) -> str:
        decision_id = uuid.uuid4().hex
        status = "pending" if decision.action == "hold" else "n/a"
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO decisions (
                  id, agent_id, rail, amount, currency, recipient, origin, instruction,
                  action, rule, reasons, approval_status, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    intent.agent_id,
                    intent.rail,
                    str(intent.amount),
                    intent.currency,
                    intent.recipient,
                    intent.origin.value if intent.origin else None,
                    intent.instruction[:500],
                    decision.action,
                    decision.rule,
                    json.dumps(decision.reasons),
                    status,
                    _iso(_now()),
                    _iso(_now()) if decision.action != "hold" else None,
                ),
            )
            if decision.action == "approve":
                self._mark_known_locked(intent.agent_id, intent.recipient)
            self._conn.commit()
        return decision_id

    def allow(self, recipient: str, rail: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO allowlist (recipient, rail) VALUES (?, ?)",
                (recipient_key(recipient), rail.strip() or "*"),
            )
            self._conn.commit()

    def resolve(self, decision_id: str, action: str, timeout_seconds: int) -> dict | None:
        with self._lock:
            self._expire_locked(timeout_seconds)
            row = self._conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if row is None:
                return None
            if row["action"] != "hold" or row["approval_status"] != "pending":
                return self._public(row)
            status = "approved" if action == "approve" else "denied"
            self._conn.execute(
                """
                UPDATE decisions
                SET approval_status = ?, resolved_at = ?
                WHERE id = ?
                """,
                (status, _iso(_now()), decision_id),
            )
            if action == "approve":
                self._mark_known_locked(row["agent_id"], row["recipient"])
            self._conn.commit()
            fresh = self._conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        return self._public(fresh)

    def get(self, decision_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        return self._public(row) if row else None

    def expire(self, timeout_seconds: int) -> None:
        with self._lock:
            self._expire_locked(timeout_seconds)
            self._conn.commit()

    def agent_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT agent_id FROM decisions ORDER BY agent_id"
            ).fetchall()
        return [row["agent_id"] for row in rows]

    def exposure(self, agent_id: str) -> dict:
        now = _now()
        day_start = _iso(now - timedelta(days=1))
        week_start = _iso(now - timedelta(days=7))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT rail, amount, created_at FROM decisions
                WHERE agent_id = ? AND created_at >= ? AND currency = 'USD'
                  AND (action = 'approve' OR (action = 'hold' AND approval_status = 'approved'))
                """,
                (agent_id, week_start),
            ).fetchall()
        daily = Decimal("0")
        weekly = Decimal("0")
        by_rail: dict[str, Decimal] = {}
        for row in rows:
            amount = Decimal(row["amount"])
            weekly += amount
            by_rail[row["rail"]] = by_rail.get(row["rail"], Decimal("0")) + amount
            if row["created_at"] >= day_start:
                daily += amount
        return {
            "agent_id": agent_id,
            "daily_usd": str(daily),
            "weekly_usd": str(weekly),
            "by_rail_usd": {rail: str(total) for rail, total in sorted(by_rail.items())},
        }

    def audit(self, agent_id: str | None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM decisions"
        args: tuple = ()
        if agent_id:
            query += " WHERE agent_id = ?"
            args = (agent_id,)
        query += " ORDER BY created_at DESC LIMIT ?"
        args = (*args, limit)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [self._public(row) for row in rows]

    def _expire_locked(self, timeout_seconds: int) -> None:
        cutoff = _iso(_now() - timedelta(seconds=timeout_seconds))
        self._conn.execute(
            """
            UPDATE decisions
            SET approval_status = 'expired', resolved_at = ?
            WHERE action = 'hold' AND approval_status = 'pending' AND created_at < ?
            """,
            (_iso(_now()), cutoff),
        )

    def _mark_known_locked(self, agent_id: str, recipient: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO known_recipients (agent_id, recipient) VALUES (?, ?)",
            (agent_id, recipient_key(recipient)),
        )

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "agent_id": row["agent_id"],
            "rail": row["rail"],
            "amount": row["amount"],
            "currency": row["currency"],
            "recipient": row["recipient"],
            "origin": row["origin"],
            "instruction": row["instruction"],
            "action": row["action"],
            "rule": row["rule"],
            "reasons": json.loads(row["reasons"]),
            "approval_status": row["approval_status"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }
