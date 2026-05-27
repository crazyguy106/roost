# Telegram Customer Channel

Telegram as a **customer-facing** lead-capture and nurture channel — the
same bot that serves operators also doubles as the inbound surface for
prospects, leads, and any non-allowlisted user who DM's it.

This is the lowest-friction customer channel in Roost: no Meta Business
verification, no Twilio approval, no SIM card. Spin up a bot in
@BotFather, set `TELEGRAM_BOT_TOKEN`, and the bot can receive customer
DM's the same minute.

## Why a separate "customer" mode

The Roost Telegram bot is normally operator-facing — `/tasks`, `/inbox`,
`/lead`, `/napprove`, daily summaries. We split audiences by **user ID**:

| User ID is in `TELEGRAM_ALLOWED_USERS` | … is NOT in allowlist |
|----------------------------------------|------------------------|
| Full operator command set, daily summaries, draft approvals. | Customer fallback: STOP/HELP, qualification, mark_inbound, lead ingest. |

Customers can't issue operator commands (security.py blocks them), and
operator messages skip the customer fallback entirely.

## Architecture

```
Telegram DM ─► PTB Application
                   │
                   │ handler groups, only one handler per group fires
                   ▼
   group -3:  customer.handle_customer_message  ◄── this doc
                   │
                   │ raise ApplicationHandlerStop on hit
                   ▼
   group -2:  linking.handle_link_message       (LINK <code>)
   group -1:  qualify.* + capture.*             (operator stack)
   group  0:  CommandHandlers (/tasks, /lead, …)
```

PTB only fires the **first matching handler per group**, so customer
sits alone in group -3 and is guaranteed to run. It explicitly defers:
- slash commands (group 0 dispatchers handle them)
- `LINK <code>` messages (handled at group -2)
- operator user IDs (returns silently → group -1 stack handles)

## Customer message flow

The non-operator branch in `customer.py::handle_customer_message`
applies these checks in order; each terminal branch raises
`ApplicationHandlerStop` to keep the message out of later groups:

| Check | Action | Skip rest? |
|-------|--------|------------|
| Empty / no text | return silently | n/a |
| Slash command (`/foo`) | return silently → CommandHandler runs | n/a |
| `LINK <code>` prefix | return silently → group -2 handles | n/a |
| Operator (in allowlist) | return silently → group -1 stack | n/a |
| **STOP** keyword (STOP/STOPALL/UNSUBSCRIBE/CANCEL/END/QUIT) | `exit_enrollments_by_contact(telegram_chat_id=…, reason="opted_out:telegram")` + confirmation DM | yes |
| **HELP** keyword (HELP/INFO) | support-info DM | yes |
| `qualification.process_answer` returns `handled=True` | qualification engine sends the next question | yes |
| `mark_inbound_for_contact` touches ≥1 enrollment | bump `last_inbound_at` for `wait_for_reply` gate | yes — no re-ingest |
| Otherwise | first-contact `leads.ingest_lead(channel="telegram", …)` | yes |

Keyword matching is first-token-after-punctuation, uppercased: `stop.`,
` Stop! `, `UNSUBSCRIBE`, `cancel` all fire STOP.

## Components

| Path | Role |
|------|------|
| `roost/extras/lead_nurture/bot/customer.py` | The group=-3 handler. Routes inbound DMs through STOP/HELP/qualification/mark_inbound/ingest. |
| `roost/extras/messaging_external/services/telegram_out.py` | Outbound Bot API adapter for customer DMs. Mirrors `sms.py`/`whatsapp.py` shape. Used by `nurture._dispatch_send` and the STOP/HELP confirmation replies. |
| `roost/extras/lead_nurture/services/cadences/store.py` | `enroll_lead` writes `contact_telegram_chat_id`; `exit_enrollments_by_contact` + `mark_inbound_for_contact` accept a `telegram_chat_id=` kwarg. |
| `roost/extras/lead_nurture/services/leads.py::ingest_lead` | Accepts `channel="telegram"` + `telegram_chat_id`. CRM upsert is **skipped** for telegram-only contacts (no email/phone to dedupe on); the enrollment row still gets `contact_telegram_chat_id` so STOP, mark_inbound, and dispatch all work. |
| `roost/extras/lead_nurture/services/nurture.py::_dispatch_send` | `telegram` branch reads `contact_telegram_chat_id` off the enrollment and calls `telegram_out.send_text_message`. |
| `roost/bot/main.py` | Registers the customer handler at group=-3 before the linking/qualify handlers. |

## Schema

`nurture_enrollments` gained one column at bundle boot:

```sql
contact_telegram_chat_id TEXT NOT NULL DEFAULT ''
```

Applied idempotently via the bundle's `schema_sql()` — `ALTER TABLE ADD
COLUMN` is wrapped in a try/except that swallows the
`OperationalError "duplicate column"` returned by SQLite when the column
already exists. No migrations directory; bundle schema is the source of
truth.

## Configuration

```bash
# .env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABCDEF…           # from @BotFather
TELEGRAM_ALLOWED_USERS=12345,67890           # operator user IDs (comma-sep)
LEAD_NURTURE_ENABLED=true                    # gate the customer handler too
```

The bot can DM **any chat_id that has previously messaged it**. Telegram
bots cannot cold-DM strangers — the customer must `/start` the bot or
send any message first. Once they have, both the lead-ingest pipeline
and `nurture._dispatch_send` can talk back via
`telegram_out.send_text_message`.

For first contact, point prospects at `https://t.me/<bot_username>` or
share the `tg://resolve?domain=<bot_username>` deep link. The first DM
they send establishes a permanent `chat_id` for that user.

## Outbound dispatch (cadence side)

The cadence loader accepts `channel: telegram` in step YAML. When the
nurture scheduler dispatches such a step:

```python
nurture._dispatch_send(...)
  → enrollment["contact_telegram_chat_id"] = "123456789"
  → telegram_out.send_text_message(chat_id=..., body=rendered_template)
  → returns {"ok": True, "channel": "telegram",
             "ref": "<message_id>", "detail": "sent"}
```

Failure modes (the dispatcher surfaces each as `{ok: False, detail: …}`):
- `no contact_telegram_chat_id` — enrollment is for a contact who never
  initiated chat. Recover by asking them to DM the bot first.
- `Telegram not enabled (TELEGRAM_ENABLED=false)` — flag is off.
- `Telegram bot token not configured` — `TELEGRAM_BOT_TOKEN` missing.
- `Telegram API <403|400>: <description>` — chat not found, bot blocked
  by user, etc. The adapter surfaces Meta-style error envelopes.

## Tests

| File | Covers |
|------|--------|
| `tests/test_telegram_customer.py` | `handle_customer_message` — passthroughs (empty/slash/LINK/operator), STOP + 7 variants + exception swallowing, HELP + INFO, qualification routing (handled / unhandled / exception), existing-enrollment mark_inbound no-re-ingest, first-contact ingest. |
| `tests/test_nurture_dispatch.py` | `_dispatch_send` telegram branch — happy, missing chat_id, adapter error, exception. |

## Limits

- **Cold-DM constraint.** Telegram bots cannot initiate conversation;
  the customer must DM first. There is no equivalent of WhatsApp's
  pre-approved templates.
- **No customer-side media in this adapter.** `telegram_out` ships text
  only — `send_text_message`. Document/image sends to operators
  (daily summary, etc.) still go through `roost.notifications.telegram`.
- **4096-char hard cap per message.** The adapter does not chunk; the
  caller must split long messages.
- **One bot, two audiences.** STOP from a customer must not be confused
  with `/STOP` from an operator. The allowlist check is the
  discriminator — keep `TELEGRAM_ALLOWED_USERS` current.

## Comparison to other customer channels

| | Telegram | WhatsApp | SMS |
|-|----------|----------|-----|
| Setup time | < 5 min | 2–5 business days | Twilio account + number |
| Cost | Free | Free tier 1000/mo | Per-message |
| Approval required | None | Meta Business verification | Twilio toll-free / 10DLC |
| Cold-DM | ✗ | Templates only | ✓ |
| Media (operator → customer) | Not in this adapter | Yes | MMS only |
| Reply-window restriction | None | 24h customer-care window | None |
| STOP/HELP | Implemented (this doc) | Implemented (`api_whatsapp.py`) | Implemented (`api_sms.py`, TwiML) |
| `wait_for_reply` gate | ✓ (`mark_inbound_for_contact`) | ✓ | ✓ |

Telegram is the recommended **day-1 testable** customer channel for SME
pilots and training environments — zero approval friction, full
STOP/HELP parity, and the same nurture/qualification machinery as the
production channels.
