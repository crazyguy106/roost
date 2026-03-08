# Gmail + Calendar Integration

**Date:** 2026-02-09
**Updated:** 2026-02-09 — added Google Calendar OAuth API integration (replaced ICS polling)

**Key files:** `roost/gmail/auto_label.py`, `roost/gmail/poller.py`, `roost/calendar_service.py`

---

## Overview

The Gmail integration now handles three responsibilities on each poll cycle (default every 5 minutes):

1. **Email-to-task capture** — emails with `[task]` or `[note]` subject prefix create items (pre-existing)
2. **Auto-labelling** — applies user labels to unlabelled inbox messages based on sender domain
3. **Action cycling** — manages `(To Reply)` ↔ `(Waiting for Reply)` state transitions automatically

---

## 1. Auto-Labelling

### How It Works

On each poll, `auto_label_recent()` scans recent inbox messages. If a message:
- Has no user label applied, AND
- Is from a domain in the rules table

...it applies the matching label automatically.

### Domain → Label Rules

| Sender Domain | Applied Label |
|---------------|---------------|
| `nexaguard.tech` | `Partnerships/#nexaguard` |
| `sginnovate.com` | `Partnerships/#sgiinovate` |
| `div0.sg` | `Partnerships/#div0` |
| `nus.edu.sg` | `Partnerships/#nus` |
| `singaporetech.edu.sg` | `Partnerships/#sit` |
| `sit.edu.sg` | `Partnerships/#sit` |
| `cisco.com` | `Partnerships/#cisco` |
| `mail-id.cisco.com` | `Partnerships/#cisco` |
| `infosec-city.com` | `Partnerships/#sincon` |
| `dwtc.com` | `Partnerships/#gisec` |
| `isaca.org` | `Partnerships/#isaca` |
| `smu.edu.sg` | `Partnerships/#smu` |
| `sutd.edu.sg` | `Partnerships/#sutd` |
| `sim.edu.sg` | `Partnerships/#sim` |

### Adding New Rules

Edit `DOMAIN_LABEL_RULES` in `roost/gmail/auto_label.py`:

```python
DOMAIN_LABEL_RULES: dict[str, str] = {
    "nexaguard.tech": "Partnerships/#nexaguard",
    # Add new rules here:
    "newdomain.com": "Category/Label",
}
```

The label must already exist in Gmail — the module will not create labels automatically.

---

## 2. Action Cycling: (To Reply) ↔ (Waiting for Reply)

### How It Works

`cycle_action_labels()` manages two transitions on each poll:

**Transition 1: You replied → move to Waiting**
- Scans all threads labelled `(To Reply)`
- If the last message in the thread is FROM `user@example.com` (configured via `GMAIL_SEND_FROM`)
- Removes `(To Reply)`, adds `(Waiting for Reply)`

**Transition 2: They replied → move back to Action**
- Scans all threads labelled `(Waiting for Reply)`
- If the last message in the thread is NOT from you
- Removes `(Waiting for Reply)`, adds `(To Reply)`

### The Cycle

```
You receive email → manually label (To Reply)
                         ↓
You send a reply → auto-moves to (Waiting for Reply)
                         ↓
They reply back → auto-moves to (To Reply)
                         ↓
You reply again → auto-moves to (Waiting for Reply)
                       ...
Thread resolved → you manually remove the label
```

### Key Design Decisions

- **Only the last message matters** — the system checks who sent the most recent message in the thread
- **Manual entry point** — you still manually apply `(To Reply)` to new threads that need action. The automation only handles the cycling after that.
- **Manual exit** — when a thread is fully resolved, you remove the label manually
- **Non-destructive** — if neither condition is met, the thread stays where it is

---

## 3. Existing Label System

### Action Labels (bracket style)

| Label | Purpose |
|-------|---------|
| `(To Reply)` | Threads requiring your action |
| `(Waiting for Reply)` | Ball in someone else's court (auto-managed) |
| `(Invoice)` | Financial documents requiring action |
| `(Need to Sign)` | Documents requiring signature |
| `(Meeting Request)` | Auto-labelled from Brella meeting notifications |

### Hierarchy Labels (slash-nested)

| Category | Examples |
|----------|----------|
| `your_user/` | Speaker, Conference, Event, Travel, Workshop, etc. |
| `Partnerships/#` | nexaguard, naio, gisec, nus, sim, sit, etc. |
| `Sales/#` | mindef, mobily |
| `Vendor/#` | crest, cvent, jalaj, fortii |
| `example/` | finance, Hexcore Labs, Practical Cyber |
| `Membership/` | isc2 |

---

## 4. Native Gmail Filters (scope now available)

Currently 6 native Gmail filters handle specific high-volume automated senders:

| From | Action |
|------|--------|
| `noreply@brella.io` | → `(Meeting Request)` |
| `noreply@wise.com` | → `example/#wise` |
| `jalaj.jain@jalaj719.com` | → `Vendor/#jalaj` |
| `UNCECOM5@uobgroup.com` | → `example/#finance` |
| `notifications-noreply@linkedin.com` | → `CATEGORY_SOCIAL` |
| `noreply@wise.com` (daily rate alert) | → `example/#wise`, skip inbox |

**Note:** The `gmail.settings.basic` OAuth scope is now authorised. Native filters can be created programmatically if needed, though the poller-based auto-labelling continues to handle domain rules effectively.

---

## 5. Google Calendar Integration (OAuth API)

### Overview

Calendar events are fetched via the Google Calendar API v3 using the same OAuth token as Gmail. This replaced the previous ICS polling approach (which required a separate `GOOGLE_CALENDAR_ICS_URL`).

### How It Works

`calendar_service.py` is the central hub. On each call to `fetch_calendar_events()`:

1. Discovers all calendars via `calendarList.list()`
2. Queries `events.list()` on each calendar (next 30 days)
3. Merges all events, sorted by start time
4. Caches results for 15 minutes

### Calendars Discovered

All calendars the user has access to are queried automatically:
- Primary calendar (`user@example.com`)
- Shared/subscribed calendars (e.g. "your_user's Gig Schedule")
- Holiday calendars (e.g. "Holidays in Singapore")
- Transferred/secondary calendars (e.g. `user@example.com`)

### Downstream Consumers

All of these read from `calendar_service.py` — no changes needed:

| Consumer | Function Used | Where |
|----------|--------------|-------|
| Telegram `/cal` | `get_week_events()` | `bot/handlers/triage.py` |
| Telegram `/today` | `get_merged_today()` | `bot/handlers/triage.py` |
| Morning digest email | `get_today_events()` | `gmail/service.py`, `bot/scheduler.py` |
| Web calendar page | `get_week_events()` | `web/pages.py` |
| Web API `/api/calendar/today` | `get_merged_today()` | `web/api.py` |
| Web API `/api/calendar/week` | `get_week_events()` | `web/api.py` |
| ICS export | `export_tasks_to_ics()` | `web/api.py`, `bot/handlers/triage.py` |

### Calendar Write Operations

Write operations (create/update/delete events from tasks) are handled separately in `gmail/calendar_write.py`.

### Required OAuth Scope

```
https://www.googleapis.com/auth/calendar
```

This grants full calendar access (read + write). Included in the `GMAIL_SCOPES` list alongside Gmail scopes. Authorized via `/auth/gmail`.

---

## 6. Architecture

### Files

| File | Responsibility |
|------|---------------|
| `roost/gmail/auto_label.py` | Auto-labelling rules + action cycling logic |
| `roost/gmail/poller.py` | Orchestrator — runs all three jobs on each poll |
| `roost/gmail/client.py` | OAuth credential management, service builders |
| `roost/gmail/service.py` | Email sending (digest, notifications) |
| `roost/gmail/subscriber.py` | Event bus integration for email notifications |
| `roost/gmail/auth.py` | OAuth consent flow web routes |
| `roost/gmail/calendar_write.py` | Google Calendar event write operations |
| `roost/calendar_service.py` | Calendar event reading — central hub for all consumers |

### Poll Cycle Flow

```
Scheduler (every GMAIL_POLL_INTERVAL seconds, default 300)
    └→ poll_inbox()
        ├→ auto_label_recent()    — label unlabelled messages by domain
        ├→ cycle_action_labels()  — cycle (To Reply) ↔ (Waiting for Reply)
        └→ [task]/[note] capture  — create tasks/notes from email subjects
```

### Configuration

```env
GMAIL_ENABLED=true
GMAIL_SEND_FROM=user@example.com    # Used to identify "your" messages
GMAIL_POLL_INTERVAL=300               # Seconds between polls
# GOOGLE_CALENDAR_ICS_URL is no longer needed — calendar reads via OAuth API
```

### OAuth Scopes

```
gmail.modify             — Read/write email, labels
gmail.settings.basic     — Manage filters
calendar                 — Full calendar access (read + write)
```

Authorize all scopes at `/auth/gmail`. The `include_granted_scopes=true` parameter ensures existing grants are preserved across re-auth flows.
