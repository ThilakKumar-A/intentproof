from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


def config_path() -> Path:
    explicit = os.environ.get("PAYGUARD_CONFIG")
    if explicit:
        return Path(explicit)
    return Path("intentproof.toml")


@dataclass(frozen=True)
class Policy:
    non_user_block_above: Decimal = Decimal("5")
    unknown_recipient_block_above: Decimal = Decimal("10")
    auto_approve_max: Decimal = Decimal("25")
    per_tx_cap: Decimal = Decimal("100")
    daily_ceiling: Decimal = Decimal("500")
    weekly_ceiling: Decimal = Decimal("2000")
    velocity_max: int = 5
    velocity_window_seconds: int = 300
    injection_min_chars: int = 12
    injection_ratio: float = 0.86

    @classmethod
    def load(cls, file_policy: dict | None = None) -> Policy:
        file_policy = file_policy or {}
        return cls(
            non_user_block_above=_pick_decimal("GUARD_NON_USER_BLOCK_ABOVE", file_policy, "non_user_block_above", "5"),
            unknown_recipient_block_above=_pick_decimal("GUARD_UNKNOWN_RECIPIENT_BLOCK_ABOVE", file_policy, "unknown_recipient_block_above", "10"),
            auto_approve_max=_pick_decimal("GUARD_AUTO_APPROVE_MAX", file_policy, "auto_approve_max", "25"),
            per_tx_cap=_pick_decimal("GUARD_PER_TX_CAP", file_policy, "per_tx_cap", "100"),
            daily_ceiling=_pick_decimal("GUARD_DAILY_CEILING", file_policy, "daily_ceiling", "500"),
            weekly_ceiling=_pick_decimal("GUARD_WEEKLY_CEILING", file_policy, "weekly_ceiling", "2000"),
            velocity_max=_pick_int("GUARD_VELOCITY_MAX", file_policy, "velocity_max", 5),
            velocity_window_seconds=_pick_int("GUARD_VELOCITY_WINDOW_SECONDS", file_policy, "velocity_window_seconds", 300),
        )

    @classmethod
    def from_env(cls) -> Policy:
        return cls.load()


@dataclass(frozen=True)
class Settings:
    api_keys: frozenset[str]
    db_path: Path
    policy: Policy
    approval_timeout_seconds: int
    slack_webhook_url: str

    @classmethod
    def from_env(cls) -> Settings:
        data = read_config_file()
        policy_file = data.get("policy") or {}
        if "GUARD_API_KEYS" in os.environ:
            raw_keys = os.environ["GUARD_API_KEYS"]
        elif data.get("api_keys"):
            raw_keys = ",".join(str(key) for key in data["api_keys"])
        else:
            raw_keys = "dev"
        keys = frozenset(part.strip() for part in raw_keys.split(",") if part.strip())
        if "GUARD_DB" in os.environ:
            db = Path(os.environ["GUARD_DB"])
        elif data.get("db_path"):
            db = Path(str(data["db_path"]))
        else:
            db = Path("data/intentproof.db")
        if "SLACK_WEBHOOK_URL" in os.environ:
            slack = os.environ["SLACK_WEBHOOK_URL"].strip()
        else:
            slack = str(data.get("slack_webhook_url") or "").strip()
        return cls(
            api_keys=keys,
            db_path=db,
            policy=Policy.load(policy_file),
            approval_timeout_seconds=_pick_int(
                "GUARD_APPROVAL_TIMEOUT_SECONDS", data, "approval_timeout_seconds", 900
            ),
            slack_webhook_url=slack,
        )


POLICY_FIELDS = (
    "non_user_block_above",
    "unknown_recipient_block_above",
    "auto_approve_max",
    "per_tx_cap",
    "daily_ceiling",
    "weekly_ceiling",
    "velocity_max",
    "velocity_window_seconds",
)

INT_FIELDS = {"velocity_max", "velocity_window_seconds", "approval_timeout_seconds"}


def read_config_file() -> dict:
    path = config_path()
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    return loaded if isinstance(loaded, dict) else {}


def write_config_value(key: str, value: str) -> Path:
    path = config_path()
    data = read_config_file()
    policy = dict(data.get("policy") or {})
    if key in POLICY_FIELDS:
        policy[key] = int(value) if key in INT_FIELDS else value
        data["policy"] = policy
    elif key == "api_keys":
        data["api_keys"] = [part.strip() for part in value.split(",") if part.strip()]
    elif key == "approval_timeout_seconds":
        data[key] = int(value)
    elif key in {"db_path", "slack_webhook_url"}:
        data[key] = value
    else:
        known = ", ".join([*POLICY_FIELDS, "approval_timeout_seconds", "api_keys", "db_path", "slack_webhook_url"])
        raise SystemExit(f"Unknown setting {key}. Use one of: {known}")
    path.parent.mkdir(parents=True, exist_ok=True) if path.parent != Path("") else None
    path.write_text(_render_toml(data), encoding="utf-8")
    return path


def _render_toml(data: dict) -> str:
    lines = [
        f"db_path = {json.dumps(str(data.get('db_path', 'data/intentproof.db')))}",
        f"approval_timeout_seconds = {int(data.get('approval_timeout_seconds', 900))}",
        f"slack_webhook_url = {json.dumps(str(data.get('slack_webhook_url', '')))}",
        "api_keys = [" + ", ".join(json.dumps(str(key)) for key in data.get("api_keys") or ["dev"]) + "]",
        "",
        "[policy]",
    ]
    policy = data.get("policy") or {}
    defaults = Policy()
    for field in POLICY_FIELDS:
        current = policy.get(field, getattr(defaults, field))
        if field in INT_FIELDS:
            lines.append(f"{field} = {int(current)}")
        else:
            lines.append(f"{field} = {json.dumps(str(current))}")
    lines.append("")
    return "\n".join(lines)


def _pick_decimal(env_name: str, file_data: dict, file_key: str, default: str) -> Decimal:
    if env_name in os.environ:
        return Decimal(os.environ[env_name])
    if file_key in file_data:
        return Decimal(str(file_data[file_key]))
    return Decimal(default)


def _pick_int(env_name: str, file_data: dict, file_key: str, default: int) -> int:
    if env_name in os.environ:
        return int(os.environ[env_name])
    if file_key in file_data:
        return int(file_data[file_key])
    return default
