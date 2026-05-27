# SMS Adapter

Outbound + inbound SMS via Twilio. Drives the `sms` channel in lead-nurture
cadences, ingests inbound messages through `leads.ingest_lead`, and honours
the STOP / HELP / UNSUBSCRIBE keyword set by exiting matching enrollments.

## Provider

We use the **Twilio REST API** directly over `httpx` with HTTP Basic auth
— no Twilio SDK dependency. One endpoint, one method, no need to pull
in `twilio-python` and its transitive deps.

Twilio is the default because it has the broadest E.164 reach (SG, MY,
US, EU all covered from one account) and a published, stable REST surface.
The `SMS_PROVIDER` switch leaves room for MessageBird / Vonage / AWS SNS
later without touching the dispatch layer.

## Components

| Path | Role |
|---|---|
| `roost/extras/messaging_external/services/sms.py` | Twilio REST client. `send_sms(to, body)` + `verify_twilio_signature(url, params, signature)` (HMAC-SHA1). Fail-closed on missing flag or credentials. |
| `roost/extras/messaging_external/web/api_sms.py` | POST `/api/sms/webhook` — verifies signature, branches on STOP/HELP, marks `last_inbound_at`, forwards to `leads.ingest_lead(channel='sms', source='sms_inbound')`. Returns TwiML. |
| `roost/extras/lead_nurture/services/nurture.py::_dispatch_send` | `sms` branch — pulls `contact_phone` from the enrollment, calls `send_sms`, maps response to the standard `{ok, channel, ref, detail}` envelope. |
| `roost/extras/lead_nurture/services/cadences/loader.py` | `ALLOWED_CHANNELS` accepts `sms` so cadence YAML can use `channel: sms` steps. |
| `roost/extras/lead_nurture/services/cadences/store.py` | `exit_enrollments_by_contact(phone=..., reason='opted_out:sms')` (STOP branch) + `mark_inbound_for_contact(phone=...)` (wait_for_reply hook). |
| `tests/test_sms.py` | Outbound adapter unit tests (disabled / missing creds / happy / 4xx / network exception). |
| `tests/test_sms_inbound.py` | Inbound webhook tests (404/403, ingest, STOP/HELP, lowercase + punctuation, signature verifier). |

## Configuration

```bash
# .env
SMS_ENABLED=true
SMS_PROVIDER=twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=<auth token from console.twilio.com>
TWILIO_FROM_NUMBER=+15551234567        # E.164 or Messaging Service SID
```

A drop-in template lives at `env-templates/sms-twilio.env`.

## Fail-closed posture

`send_sms()` returns an error envelope rather than raising in three cases:

| Condition | Response |
|---|---|
| `SMS_ENABLED=false` | `{"ok": False, "error": "SMS not enabled (SMS_ENABLED=false)"}` |
| Unrecognised `SMS_PROVIDER` | `{"ok": False, "error": "Unsupported SMS provider: <x>"}` |
| Any of SID / token / from-number missing | `{"ok": False, "error": "Twilio not configured ..."}` |

Twilio API 4xx responses are surfaced as `{"ok": False, "error": "Twilio API <code>", "details": {...}}`. Network errors (DNS, timeout, connection reset) come back as `{"ok": False, "error": "<exception str>"}`. The cadence dispatcher folds these into a standard `{ok: False, channel: "sms", detail: ...}` so failures show up in `nurture_enrollment_logs` exactly like email or WhatsApp send failures.

This is the same posture as the WhatsApp / DNC / CDD adapters — documented in `CLAUDE.md` under "Adapters fail closed".

## Cadence usage

Any cadence step can target SMS:

```yaml
- offset_days: 1
  channel: sms
  template_id: warm_sms_followup
  template:
    body: "Hi {{name}} — checking in re: {{topic}}. Reply STOP to opt out."
```

The dispatcher reads `enrollment.contact_phone`. If that's empty for a
lead, the step fails with `no contact_phone` and the enrollment continues
to the next step on the regular tick (it does not retry).

## Inbound webhook

POST `/api/sms/webhook` is the path you configure in the Twilio number's
"A MESSAGE COMES IN" setting. The handler:

1. Returns **404** when `SMS_ENABLED=false`.
2. Computes the expected `X-Twilio-Signature` (base64-HMAC-SHA1 of
   `request.url + concat(sorted(key+value))`) and returns **403** on
   mismatch. Empty body / missing token = fail-closed.
3. Parses `From`, `Body`, `MessageSid`. Empty `Body` (delivery-status
   callbacks hitting the same URL) → silent TwiML ack.
4. Normalises the first token of `Body`. If it's in `{STOP, STOPALL,
   UNSUBSCRIBE, CANCEL, END, QUIT}`, calls `cadences.exit_enrollments_by_contact(phone=From, reason='opted_out:sms')`
   and returns a confirmation TwiML message. `{HELP, INFO}` returns a
   support-contact TwiML message. Both branches skip lead ingest.
5. Otherwise: stamps `last_inbound_at` on matching enrollments
   (`cadences.mark_inbound_for_contact`) and calls
   `leads.ingest_lead(channel='sms', source='sms_inbound', phone=From, message_text=Body, qualifying_identifier=From)`.
   Exceptions are logged and swallowed (returns empty TwiML) so Twilio
   does not retry-storm the endpoint.

The `last_inbound_at` stamp is what the `wait_for_reply` cadence step
reads to decide whether to exit the cadence on engagement. See
`docs/lead-nurture.md` for the gate semantics.

## What's not implemented

- **Long-message segmentation** — Twilio splits >160 GSM-7 chars
  automatically. We pass `body` through unmodified; per-segment billing is
  the operator's problem until cost surfacing lands.
- **Messaging Service SID** — `TWILIO_FROM_NUMBER` is sent as `From=`. If
  it's a `MGxxxx...` Messaging Service SID, Twilio accepts that form too.
- **START / re-subscribe handling** — STOP exits enrollments but does not
  blacklist the phone; a START reply triggers no automatic re-enrollment.
  Re-enrollment is a manual operator action via the MCP / web surface.
