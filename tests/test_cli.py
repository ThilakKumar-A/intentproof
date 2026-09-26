from decimal import Decimal

from intentproof.cli import main
from intentproof.config import Settings
from intentproof.models import Origin, PaymentIntent
from intentproof.engine import evaluate
from intentproof.models import SpendSnapshot
from intentproof.store import Store


def test_config_set_changes_the_limit(tmp_path, monkeypatch):
    path = tmp_path / "intentproof.toml"
    monkeypatch.setenv("PAYGUARD_CONFIG", str(path))
    monkeypatch.delenv("GUARD_DAILY_CEILING", raising=False)
    main(["config", "set", "daily_ceiling", "750"])
    settings = Settings.from_env()
    assert settings.policy.daily_ceiling == Decimal("750")
    assert path.is_file()


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    path = tmp_path / "intentproof.toml"
    monkeypatch.setenv("PAYGUARD_CONFIG", str(path))
    main(["config", "set", "daily_ceiling", "750"])
    monkeypatch.setenv("GUARD_DAILY_CEILING", "10")
    assert Settings.from_env().policy.daily_ceiling == Decimal("10")


def test_summary_lists_cross_rail_spend(tmp_path, monkeypatch, capsys):
    db = tmp_path / "guard.db"
    monkeypatch.setenv("GUARD_DB", str(db))
    monkeypatch.setenv("PAYGUARD_CONFIG", str(tmp_path / "missing.toml"))
    store = Store(db)
    intent = PaymentIntent(
        agent_id="demo",
        rail="stripe",
        amount="12",
        recipient="acme hosting",
        origin=Origin.user,
        instruction="pay the hosting bill",
    )
    decision = evaluate(
        intent,
        Settings.from_env().policy,
        SpendSnapshot(recipient_allowlisted=True),
    )
    store.save(intent, decision)
    store.close()

    main(["summary", "--agent", "demo"])
    out = capsys.readouterr().out
    assert "today $12.00" in out
    assert "stripe $12.00" in out
    assert "approve" in out
