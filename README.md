# IntentProof

An AI agent must ask IntentProof before it sends money. If the pay instruction came from a page, email, tweet, or tool result, the payment is blocked.

## Install

```bash
pip install intentproof
intentproof serve
```

Open http://127.0.0.1:8080

The top of the page is money already approved. The form under it is a manual test.

- **Block** means do not pay.
- **Hold** means a person must approve.
- **Approve** means the agent may pay.

From this folder before the package is on PyPI: `pip install .`

## In your agent

```python
from intentproof.sdk import IntentProof, PaymentBlocked

guard = IntentProof("http://127.0.0.1:8080", "dev", agent_id="demo")
guard.tracker.note_user("pay the hosting bill")
guard.tracker.note_fetched(page_or_tool_text)

try:
    guard.pay(rail="stripe", amount="12", recipient="acme hosting", instruction="pay the hosting bill")
except PaymentBlocked:
    pass  # do not call Stripe or the wallet
```

`note_user` is what the person typed. `note_fetched` or `note_tool_output` is anything the agent read. Call the rail only when `pay()` returns.

```bash
intentproof summary
intentproof config
intentproof config set daily_ceiling 750
```

## Configuration

Settings live in `intentproof.toml` in the current folder. If the file is missing, the defaults below are used. A `GUARD_*` environment variable overrides the file. Restart `intentproof serve` after a change.

`intentproof config` prints the active values.

**Where it runs**

| Setting | Default | What it does |
|---|---|---|
| `api_keys` | `dev` | Bearer token the agent must send. Separate several keys with commas. |
| `db_path` | `data/intentproof.db` | SQLite file for decisions, the allowlist, and spend. |
| `slack_webhook_url` | empty | A hold posts a short Slack message. Empty means no Slack call. The hold still stands. |
| `approval_timeout_seconds` | `900` | A hold with no answer becomes a deny after 15 minutes. |
| `PORT` | `8080` | Port for `intentproof serve`. Environment variable only. |

**When a payment is blocked, held, or approved**

Amounts are US dollars.

| Setting | Default | What it does |
|---|---|---|
| `non_user_block_above` | `5` | Block when the instruction came from a tool result, page, email, or tweet and the amount is above this. At or under it, the payment is held. |
| `unknown_recipient_block_above` | `10` | Block when this recipient was never trusted and the amount is above this. At or under it, the payment is held as a new recipient. |
| `auto_approve_max` | `25` | Approve only when the user typed the instruction, the recipient is trusted, and the amount is at or under this. |
| `per_tx_cap` | `100` | Hold a single payment above this, even for a trusted recipient. |
| `daily_ceiling` | `500` | Hold when today's approved spend, across every rail, would go over this. |
| `weekly_ceiling` | `2000` | Hold when this week's approved spend, across every rail, would go over this. |
| `velocity_max` | `5` | Block after this many payment attempts from the same agent. |
| `velocity_window_seconds` | `300` | The window for that count: 5 minutes. |

Trusted means the recipient is on the allowlist, or a person already approved a payment to them.

Two match settings are fixed in code: the copied instruction must be at least 12 characters, and a fuzzy match must score at least 0.86. That catches a payment whose words were copied from text the agent just read.

```bash
intentproof config set daily_ceiling 750
intentproof config set api_keys your-real-key
```
