"""Commands available after pip install: serve, summary, config."""

from __future__ import annotations

import argparse
import os

from intentproof.config import POLICY_FIELDS, Settings, config_path, write_config_value
from intentproof.store import Store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="intentproof")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the local API and homepage")
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))

    summary = sub.add_parser("summary", help="show approved spend and recent decisions")
    summary.add_argument("--agent", help="one agent id; omit to list every agent")

    config = sub.add_parser("config", help="show or change intentproof.toml")
    config_sub = config.add_subparsers(dest="config_command")
    setter = config_sub.add_parser("set", help="write one setting into intentproof.toml")
    setter.add_argument("key")
    setter.add_argument("value")

    args = parser.parse_args(argv)
    if args.command == "serve":
        _serve(args.port)
    elif args.command == "summary":
        _summary(args.agent)
    elif args.command == "config" and args.config_command == "set":
        path = write_config_value(args.key, args.value)
        print(f"Updated {path}")
        print("Restart intentproof serve so a running server picks this up.")
    else:
        _print_config()


def _serve(port: int) -> None:
    import uvicorn

    uvicorn.run("intentproof.api:app", host="0.0.0.0", port=port, reload=False)


def _summary(agent: str | None) -> None:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    try:
        ids = [agent] if agent else store.agent_ids()
        if not ids:
            print(f"No payments recorded in {settings.db_path}")
            return
        for agent_id in ids:
            exposure = store.exposure(agent_id)
            print(f"agent {exposure['agent_id']}")
            print(f"  today ${exposure['daily_usd']}    week ${exposure['weekly_usd']}")
            rails = exposure["by_rail_usd"] or {"(none)": "0"}
            for rail, total in rails.items():
                print(f"  {rail} ${total}")
            print("  recent")
            for row in store.audit(agent_id, limit=10):
                status = row["approval_status"]
                extra = f" ({status})" if status not in (None, "n/a") else ""
                print(
                    f"    {row['action']:7} ${row['amount']} {row['rail']} "
                    f"{row['recipient']}  {row['rule']}{extra}"
                )
    finally:
        store.close()


def _print_config() -> None:
    settings = Settings.from_env()
    path = config_path()
    print(f"file {path} {'(in use)' if path.is_file() else '(not created yet)'}")
    print(f"database {settings.db_path}")
    print(f"api_keys {', '.join(sorted(settings.api_keys))}")
    print(f"approval_timeout_seconds {settings.approval_timeout_seconds}")
    print(f"slack_webhook_url {settings.slack_webhook_url or '(empty)'}")
    policy = settings.policy
    for field in POLICY_FIELDS:
        print(f"{field} {getattr(policy, field)}")
    print("Change a limit with: intentproof config set daily_ceiling 750")
    print("A GUARD_* environment variable overrides the file.")
