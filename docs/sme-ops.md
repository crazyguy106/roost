# SME Ops

Vertical bundle for small/medium businesses — operational glue connecting accounting, e-commerce, payments, CRM, and marketing systems. Peer to the Property-Agent Toolkit; same pattern (services + recipes + RPA flows + dashboard pages), different vertical.

> **Note:** Roost is domain-agnostic. SME Ops is one *bundled vertical* among others (Property-Agent for SG agents is the other shipping today). The platform itself doesn't assume a vertical.

## Status

| Phase | Status | Scope |
|---|---|---|
| 0 — Foundation | **Shipped** | Bundle skeleton, Zapier ingress + outbound bridge, `sme_ops_events` audit log, `/sme/sync-status` dashboard |
| 1A — Anchor adapters (read-only) | **Shipped** | Stripe, Shopify, Xero — read endpoints + signed webhooks (Stripe, Shopify) |
| 1B — Anchor adapters (write) | **Shipped** | Stripe refunds + payment links, Shopify fulfill/cancel, Xero OAuth2 + webhook + create_invoice, `/sme/orders` and `/sme/cashflow` pages |
| 2 — SG add-ons | Planned | HitPay, Lazada/Shopee RPA (Corppass-assisted), ACRA bizfile lookup |
| 3 — Other adapters | As-needed | QuickBooks, WooCommerce, Pipedrive, HubSpot, Mailchimp, SendGrid, Airtable |
| 4 — Recipe library | Planned | Order→invoice→SMS, abandoned cart, low stock, payment-failed flows |

## Phase 0 — what shipped

### Universal Zapier bridge

The fastest way to wire any of Zapier's 6,000+ apps into Roost before native adapters land.

**Inbound (Zapier → Roost):**
- Endpoint: `POST /api/zapier/inbound`
- Auth: `Authorization: Bearer <ZAPIER_INGRESS_TOKEN>`
- Body shape:
  ```json
  { "event": "order.created", "payload": { "id": 123, "total": 49.90, "customer": "..." } }
  ```
- Behaviour: persists envelope to `sme_ops_events`, fires SOP trigger `zapier_event` with `zapier_event_name` set so recipes can route on it. Fail-closed (404 if `SME_OPS_ENABLED=false`, 401 on bad token, 503 if no token configured).

**Outbound (Roost → Zapier):**
- `from roost.services.sme_ops.zapier import ZapierBridge`
- `ZapierBridge().send("invoice.paid", {"id": 99, "amount": 100.00})`
- POSTs `{event, payload}` to `ZAPIER_OUTBOUND_URL`. Returns `{ok, status}`. Never raises — outbound notifications must not break the caller.

### Audit log

`sme_ops_events` table (added in `SCHEMA_V28`):

| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| source | TEXT | `zapier`, later `xero`, `shopify`, … |
| event | TEXT | provider's event name |
| payload_json | TEXT | raw envelope |
| received_at | TEXT | UTC datetime |
| processed_at | TEXT | filled by recipe runner |
| status | TEXT | `received` / `processed` / `failed` |

### Dashboard

`/sme/sync-status` — shows Zapier configuration state, last received event, today's counts by status. Native adapters appear as additional cards as they ship.

## Configuration

See `env-templates/sme-ops.env` for the full block. Minimum to enable Phase 0:

```env
SME_OPS_ENABLED=true
ZAPIER_ENABLED=true
ZAPIER_INGRESS_TOKEN=<generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
```

`ZAPIER_OUTBOUND_URL` is optional — only needed if you want Roost to push events back to Zapier.

## Setup walkthrough — wire Shopify orders into Roost via Zapier

Until the native Shopify adapter ships, this is the recommended path.

1. **In Roost:** set `SME_OPS_ENABLED`, `ZAPIER_ENABLED`, and `ZAPIER_INGRESS_TOKEN` in `.env`. Restart the web service.
2. **In Zapier:** new Zap →
   - **Trigger:** Shopify → New Order
   - **Action:** Webhooks by Zapier → POST
     - URL: `https://<your-roost-host>/api/zapier/inbound`
     - Payload type: JSON
     - Data: `event = order.created`, `payload = { ...mapped Shopify fields... }`
     - Headers: `Authorization: Bearer <ZAPIER_INGRESS_TOKEN>`
3. **In Roost recipes:** create a recipe with `trigger_type=event`, `trigger_config=zapier_event`. Inside the recipe, branch on `zapier_event_name == "order.created"`.

## Phase 1A — native anchor adapters (read-only)

All three adapters share the same shape: class instance constructed from env, `is_configured()` gate, fail-closed `{"error": "..."}` returns, normalized dicts (no provider wire-shape leakage).

### Stripe

- **Auth:** `STRIPE_API_KEY` (secret key)
- **Read tools:** `stripe_list_charges`, `stripe_list_subscriptions`, `stripe_list_customers`
- **Webhook:** `POST /api/stripe/webhook`. Verifies the `Stripe-Signature` header (HMAC-SHA256 with `STRIPE_WEBHOOK_SECRET`, 5-minute replay tolerance). On valid event, persists envelope to `sme_ops_events` and fires SOP trigger `stripe_event` with `stripe_event_name` in data.
- **Setup:** create a webhook endpoint in the Stripe Dashboard (Developers → Webhooks) pointing at `https://<your-host>/api/stripe/webhook`; copy the signing secret into `STRIPE_WEBHOOK_SECRET`.

### Shopify

- **Auth:** `SHOPIFY_STORE_DOMAIN` + `SHOPIFY_ACCESS_TOKEN` (custom-app admin API token)
- **Read tools:** `shopify_list_orders`, `shopify_list_products`, `shopify_list_customers`
- **Webhook:** `POST /api/shopify/webhook`. Verifies the `X-Shopify-Hmac-Sha256` header (base64 HMAC-SHA256 over raw body with `SHOPIFY_WEBHOOK_SECRET`). Topic comes via `X-Shopify-Topic`. Fires SOP trigger `shopify_event`.
- **Setup:** in Shopify admin → Settings → Notifications → Webhooks, register an endpoint. Copy the secret shown.

### Xero

- **Auth:** `XERO_PAT` + `XERO_TENANT_ID` (Personal Access Token route — sufficient for single-user instances; OAuth2 flow comes in Phase 1B)
- **Read tools:** `xero_list_invoices`, `xero_list_contacts`, `xero_list_bank_transactions`
- **No webhook in 1A** — Xero's webhook product is OAuth-gated; it lands with the OAuth2 flow in 1B.

## Phase 1B — writes, OAuth2, and cross-adapter pages

### Draft-first approval for money-moving writes

These three tools never execute when called by an AI agent. Instead they
go through Guardian (`services/guardian.py::guardian_gate`), which
persists a row in `guardian_drafts` with status `pending`. The draft only
runs after a human approves it.

- **Drafted:** `stripe_create_refund`, `shopify_cancel_order`, `xero_create_invoice` (when `status != "DRAFT"`).
- **Direct (no draft):** `stripe_create_payment_link`, `shopify_fulfill_order`, `xero_create_invoice` with `status="DRAFT"`.

When an agent calls a drafted tool it gets back:

```json
{ "ok": false, "status": "pending_approval", "draft_id": 17,
  "reason": "stripe_create_refund requires explicit human approval (money-moving write).",
  "preview": { "charge_id": "ch_1", "amount": 49.90, "reason": null } }
```

Surfaces:
- `GET /api/sme/drafts` — list pending drafts.
- `POST /api/sme/drafts/<id>/approve` — execute the underlying call. Returns `{status: "executed"|"failed", result: {...}}`.
- `POST /api/sme/drafts/<id>/reject` — discard with optional reason.
- `/sme/sync-status` — pending-drafts table at the top of the page with Approve / Reject buttons.

Drafts are immutable once decided. A second approve returns `{status: "not_pending"}`.

### Stripe writes

- `stripe_create_refund(charge_id, amount=None, reason=None)` — full or partial refund. **Drafted via Guardian.**
- `stripe_create_payment_link(amount, currency, description=None)` — returns a shareable Stripe-hosted URL. Direct.

### Shopify writes

- `shopify_fulfill_order(order_id, tracking_number=None, tracking_company=None, notify_customer=True)` — fulfils all line items on the order via the 2024-04 fulfillment-orders API. Direct.
- `shopify_cancel_order(order_id, reason="other", refund=False)` — Set `refund=True` to also issue a refund. **Drafted via Guardian.**

### Xero OAuth2

OAuth2 replaces the PAT for multi-tenant or production setups. PAT remains as a fallback when no OAuth tokens are stored.

**Setup:**
1. Register a "Web app" at https://developer.xero.com/app/manage. Set the redirect URI to `https://<your-host>/api/xero/oauth/callback` (or set `XERO_REDIRECT_URI` explicitly).
2. Drop `XERO_CLIENT_ID` and `XERO_CLIENT_SECRET` into `.env`. Restart.
3. Visit `https://<your-host>/api/xero/oauth/start` once. You'll be redirected to Xero, approve, and bounced back. The callback stores tokens per tenant in `xero_oauth_tokens` and auto-refreshes them on use.

Tokens last 30 minutes; refresh tokens last 60 days of inactivity. Roost rotates them within 60 seconds of expiry.

### Xero webhook

- **Endpoint:** `POST /api/xero/webhook`
- **Signature:** base64 HMAC-SHA256 over the raw body, header `x-xero-signature`, key from the Xero app's Webhooks tab → `XERO_WEBHOOK_KEY`.
- **Intent-to-receive:** Xero validates the endpoint with an empty body. We return 200 only when the signature is valid; 401 otherwise (Xero's required behaviour).
- Fires SOP trigger `xero_event` per envelope event with `xero_event_name`, `tenant_id`, `resource_id`, `resource_url`, and `event_type`.

### Xero writes

- `xero_create_invoice(contact_id, line_items, due_date=None, status="DRAFT", type_="ACCREC")` — DRAFT executes immediately (no notification, fully reversible). SUBMITTED / AUTHORISED are **drafted via Guardian** since they can lock the document and trigger customer notifications. `ACCREC` = sales (money in); `ACCPAY` = bills (money out). Each line item: `{description, quantity, unit_amount, account_code?}`.

### Cross-adapter pages

- **`/sme/orders`** — recent orders pulled live from Shopify + Stripe, normalized into one table (source / ref / customer / amount / status / fulfillment / created_at). Sorted most-recent first. Adapters that aren't configured are silently omitted.
- **`/sme/cashflow`** — money in / out / net per source. Pulls Xero invoices + bank transactions and Stripe charges; shows recent rows from each.

Both pages fail-soft: if a source returns an error, a small chip is shown but other sources still render.

## Code map

- **Services:** `roost/extras/sme_ops/services/{zapier,stripe,shopify,xero,xero_oauth}.py`
- **MCP tools:** `roost/extras/sme_ops/mcp/tools_{stripe,shopify,xero}.py` (registered via the bundle's `_register()` in `roost/extras/sme_ops/__init__.py`, not in core `roost/mcp/server.py`)
- **Web ingress:** `roost/web/api_{zapier,stripe,shopify,xero,sme_drafts}.py`
- **Pages:** `roost/web/pages.py` — `/sme/sync-status`, `/sme/orders`, `/sme/cashflow`
- **Templates:** `roost/web/templates/sme_ops/{sync_status,orders,cashflow}.html`
- **Schema:** `SCHEMA_V28` (events) + `SCHEMA_V29` (Xero OAuth tokens) + `SCHEMA_V30` (Guardian drafts) in `roost/database.py`
- **SOP triggers:** `zapier_event`, `stripe_event`, `shopify_event`, `xero_event` in `roost/services/sop_triggers.py::EVENT_TYPES`

## Tests

- `tests/test_zapier_ingress.py` — Zapier inbound (auth, gating, persistence, SOP dispatch)
- `tests/test_stripe.py` — service mocks (respx) + webhook signature verification + happy path
- `tests/test_shopify.py` — service mocks + HMAC webhook verification + happy path
- `tests/test_xero.py` — service mocks + normalization + limit truncation
