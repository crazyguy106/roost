# WhatsApp Adapter

WhatsApp Cloud API integration for Roost. Used for inbound client messages
(routed to the AI agent) and outbound notifications (text, documents,
images, templates) — including from RPA flows that download artefacts and
deliver them straight to clients.

## Provider

We use the **official Meta WhatsApp Cloud API**, not Baileys / whatsapp-web.js
or any reverse-engineered client. The unofficial libraries violate Meta's
terms of service and break frequently — unacceptable for any regulated
financial-advisory deployment (e.g. MAS-licensed FAs in Singapore).

Free tier: 1,000 service-initiated conversations / month per phone number.

## Components

| Path | Role |
|---|---|
| `roost/services/whatsapp.py` | Cloud API client. `send_text_message`, `send_template_message`, `send_document`, `send_image`, `upload_media`, `mark_as_read`, `verify_webhook_signature`, `parse_webhook_entry`. |
| `roost/web/api_whatsapp.py` | FastAPI webhook at `/webhook/whatsapp`. GET handles Meta's verify-token handshake; POST receives signed events and routes inbound messages to the agent. |
| `roost/mcp/tools_whatsapp.py` | MCP tools so the agent can decide to send: `whatsapp_send_text`, `whatsapp_send_document`, `whatsapp_send_image`, `whatsapp_send_template`. Gated by `WHATSAPP_ENABLED`. |
| `examples/skills/whatsapp_lead_triage.py` | Reference skill showing inbound-message handling. |
| RPA `whatsapp_send` step op | Lets a flow deliver `last_download` / `last_screenshot` directly. See `docs/rpa.md`. |

## Configuration

```bash
# .env
WHATSAPP_ENABLED=true
WHATSAPP_PHONE_NUMBER_ID=<from Meta Business Manager>
WHATSAPP_ACCESS_TOKEN=<system-user token, never the temporary one>
WHATSAPP_VERIFY_TOKEN=<arbitrary string, must match Meta dashboard>
WHATSAPP_APP_SECRET=<used to verify X-Hub-Signature-256 on inbound>
```

The webhook URL to register in Meta Business Manager is:

```
https://<your-roost-host>/webhook/whatsapp
```

## Sending: three sources

`send_document` and `send_image` accept exactly one of:

- `path=` — local file. Uploaded to Meta's media store automatically; the
  returned media_id is reused for the actual send. Best for one-off
  ad-hoc files.
- `media_id=` — an already-uploaded media reference (Meta keeps these for
  30 days). Use when sending the same artefact to many recipients.
- `link=` — a publicly fetchable URL. Use when the file lives behind a
  signed Drive URL or similar; saves the upload round-trip.

Captions are limited to 1024 chars; document filenames default to the
basename of `path`.

## RPA integration

The `whatsapp_send` step lets a flow finish into a client message:

```yaml
steps:
  - op: download_one
    trigger_selector: "a.policy-pdf"
  - op: whatsapp_send
    to: "$param:client_phone"
    source: last_download
    caption: "$param:caption"
```

`source` resolves to the most recent `last_download`, `last_screenshot`,
or `last_extracted` recorded by earlier steps. The op is fire-and-forget:
Meta API errors are recorded in `$var:last_whatsapp_error` but do not
abort the flow — the rest of the run still completes.

## 24-hour window & templates

Free-form text and media may only be sent **within 24 hours of the
client's last inbound message** (the "customer-service window"). Outside
that window, only Meta-approved template messages can initiate
conversation. The agent should:

1. Try `whatsapp_send_text` first.
2. If Meta returns the 24-hour-window error, fall back to
   `whatsapp_send_template` with an approved template name.
3. Once the client replies, free-form is unlocked again for 24 hours.

## Security

- **Allowlist via webhook handler.** Inbound messages from numbers not on
  the configured allowlist are dropped at the API layer. Default policy
  is closed — explicitly add WhatsApp IDs (E.164 without `+`) of
  authorised senders.
- **Signed payload verification.** Every webhook POST is verified against
  `WHATSAPP_APP_SECRET` using HMAC-SHA256 (`verify_webhook_signature`).
  Unsigned or mismatched payloads are rejected.
- **PII at rest.** Phone numbers are PII. Full message bodies live in
  `chat_history` (encrypted at rest with the same Fernet key as
  credentials); audit log captures direction, timestamps, and message_id
  only.

## Demo plan for TWS / Phillip Securities

The TWS automation brief lists 8 manual workflows; 5 end with WhatsApp.
End-to-end pilot path:

1. Enable WhatsApp adapter and complete Meta Business Manager setup
   (phone-number verification: 2–5 business days of waiting).
2. Wire the AIA flow (`roost/services/rpa_flows/library/aia.yaml`) to
   end with `whatsapp_send` after `download_one`.
3. Run end-to-end: agent receives "run AIA daily" via Telegram → flow
   logs in → OTP pause/resume → downloads PDF → `whatsapp_send` to a
   test client number with `source: last_download`.
4. Add `screenshot` step + `whatsapp_send` for the PMMF-rates weekly
   workflow (no auth, public page → screenshot → send).
5. Capture screen recording for the TWS pitch.

## Open questions for Yee Shen Hao (TWS) before pilot

- AIA OTP delivery — SMS or email? Email lets us fully automate; SMS
  keeps the OTP-via-Telegram pause/resume.
- Source of truth for client phone numbers — CRM API or spreadsheet?
  Determines whether the recipe pulls phones via `tools_contacts` or
  from a stored mapping.
- Singlife encrypted-email password rotation — fixed per client, or
  derived (last-4-NRIC, DoB)? Drives the credentials lookup pattern.
