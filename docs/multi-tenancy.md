# Roost Multi-Tenancy Design

**Status:** Approved
**Date:** 2026-02-24
**Author:** your_user + Claude

## Problem

Roost was built as a single-user system. On the dev VPS this works - your_user is the only user. But department VPSes (e.g. `user-vps`, `user-vps`) will have multiple people, each running their own Claude Code session. Today:

- All MCP processes share one set of MS Graph tokens - `send_email` sends from whoever authed last
- Tasks, contacts, notes have no owner - everyone sees everything
- Calendar cache is global - user A's events leak to user B
- `user_settings` (spoons, routines) are global singletons
- oauth_tokens keyed by `(provider, scope)` - only one account per provider

## Goal

Multiple users on one VPS, each with:
- Their own MS Graph tokens (email, calendar, OneDrive, Teams)
- Their own tasks, contacts, notes, routines, spoons
- Shared access to DeptTools data (pipeline, products, deals)
- No changes needed to how Claude Code connects

## Key Insight: MCP is Per-Process

Claude Code spawns a **separate** `roost-mcp` process per session (stdio transport). On a VPS with 3 users each running Claude Code, there are 3 independent MCP processes. Each process can receive different environment variables via `~/.mcp.json`:

```json
{
  "mcpServers": {
    "roost": {
      "command": "/home/dev/.local/bin/roost-mcp",
      "env": {
        "ROOST_USER": "ben@example.com"
      }
    }
  }
}
```

This means we don't need request-level user context. Process-level is enough.

## Architecture

```
Linux user: ben                    Linux user: sarah
~/.mcp.json                        ~/.mcp.json
  ROOST_USER=ben@example.com        ROOST_USER=sarah@example.com
       |                                  |
   roost-mcp (pid 1234)          roost-mcp (pid 5678)
   UserContext(user_id=2)            UserContext(user_id=3)
       |                                  |
       +----------------------------------+
       |
   data/roost.db (shared, WAL mode)
       |
   +---+---+---+---+
   |tasks  |oauth  |contacts| ...
   |uid=2  |uid=2  |uid=2   |
   |uid=3  |uid=3  |uid=3   |
```

## Design

### Phase 1: User Context Layer

**New file: `roost/user_context.py`**

```python
import os
from dataclasses import dataclass

@dataclass
class UserContext:
    user_id: int
    email: str
    name: str
    role: str  # owner, admin, member, viewer

# Module-level singleton - set once at MCP startup
_current_user: UserContext | None = None

def init_user_context() -> UserContext:
    """Resolve ROOST_USER env var to a UserContext.

    Called once at MCP server startup.
    Falls back to owner user if env var not set (backward compat).
    """
    global _current_user
    email = os.getenv("ROOST_USER", "")

    from roost.database import get_connection
    conn = get_connection()

    if email:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = LOWER(?)",
            (email.strip(),)
        ).fetchone()
    else:
        # Fallback: first owner user (backward compat for dev VPS)
        row = conn.execute(
            "SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1"
        ).fetchone()

    conn.close()

    if not row:
        raise RuntimeError(
            f"User not found: {email or '(no ROOST_USER set, no owner in DB)'}. "
            "Run the web UI to create users first."
        )

    _current_user = UserContext(
        user_id=row["id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
    )
    return _current_user

def get_current_user() -> UserContext:
    """Get the current user context. Raises if not initialized."""
    if _current_user is None:
        return init_user_context()
    return _current_user

def get_current_user_id() -> int:
    """Shorthand for get_current_user().user_id."""
    return get_current_user().user_id
```

**Changes to `mcp/server.py`:**

```python
from roost.user_context import init_user_context

def main():
    ctx = init_user_context()
    logger.info("MCP server starting for user: %s (id=%d)", ctx.email, ctx.user_id)
    mcp.run(transport="stdio")
```

### Phase 2: Token Isolation

**Schema migration (SCHEMA_V18):**

```sql
-- Add user_id to oauth_tokens
ALTER TABLE oauth_tokens ADD COLUMN user_id INTEGER REFERENCES users(id);

-- Backfill: assign existing tokens to owner user
UPDATE oauth_tokens SET user_id = (
    SELECT id FROM users WHERE role = 'owner' ORDER BY id LIMIT 1
) WHERE user_id IS NULL;

-- Drop old unique constraint, add new one
-- (SQLite doesn't support DROP CONSTRAINT, so we recreate the table)
CREATE TABLE oauth_tokens_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    access_token TEXT,
    refresh_token TEXT NOT NULL,
    token_type TEXT DEFAULT 'Bearer',
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, provider, scope)
);

INSERT INTO oauth_tokens_v2 (id, user_id, provider, scope, access_token,
    refresh_token, token_type, expires_at, created_at, updated_at)
SELECT id, user_id, provider, scope, access_token,
    refresh_token, token_type, expires_at, created_at, updated_at
FROM oauth_tokens;

DROP TABLE oauth_tokens;
ALTER TABLE oauth_tokens_v2 RENAME TO oauth_tokens;
CREATE INDEX idx_oauth_tokens_provider ON oauth_tokens(provider);
CREATE INDEX idx_oauth_tokens_user ON oauth_tokens(user_id);
```

**Changes to `microsoft/client.py`:**

```python
def get_stored_token_cache(user_id: int | None = None) -> str | None:
    """Retrieve the stored MSAL token cache JSON for a specific user."""
    if user_id is None:
        from roost.user_context import get_current_user_id
        user_id = get_current_user_id()

    # Per-user shared file: /tmp/msal_cache_{user_id}.json
    shared = _shared_cache_path(user_id)
    if shared and shared.exists():
        return shared.read_text().strip() or None

    from roost.database import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT refresh_token FROM oauth_tokens WHERE user_id = ? AND provider = ? AND scope = ?",
        (user_id, PROVIDER, SCOPE_KEY),
    ).fetchone()
    conn.close()
    return row["refresh_token"] if row else None

def store_token_cache(cache_json: str, user_id: int | None = None) -> None:
    if user_id is None:
        from roost.user_context import get_current_user_id
        user_id = get_current_user_id()
    # ... same logic but with user_id in queries
```

Same pattern for `gmail/client.py` - `get_stored_refresh_token(user_id)`, `store_refresh_token(token, user_id)`.

### Phase 3: Personal Data Isolation

Add `user_id` column to personal data tables. Shared data tables (entities, DeptTools) remain unscoped.

**Personal tables (add `user_id`):**

| Table | Reason |
|-------|--------|
| `tasks` | Each user's task list |
| `notes` | Personal notes |
| `contacts` | Personal CRM |
| `activity_log` | Per-user activity |
| `user_settings` | Per-user preferences |
| `routines` | Per-user daily routines |
| `claude_sessions` | Per-user Claude sessions |
| `scheduled_emails` | Per-user email queue |
| `servers` | Per-user SSH servers |

**Shared tables (no user_id needed):**

| Table | Reason |
|-------|--------|
| `entities` | Company-wide entity registry |
| `projects` | Can be shared across users |
| `project_members` | Already has user_id via join |
| `okr_*` | Organizational objectives |

**Migration approach:** Add `user_id` column with default NULL, backfill to owner, then make NOT NULL.

```sql
ALTER TABLE tasks ADD COLUMN user_id INTEGER REFERENCES users(id);
UPDATE tasks SET user_id = (SELECT id FROM users WHERE role = 'owner' ORDER BY id LIMIT 1)
    WHERE user_id IS NULL;
```

**Service layer changes:**

Every service function that touches personal data gets a `user_id` parameter:

```python
# Before
def list_tasks(status="", project=""):
    conn = get_connection()
    query = "SELECT * FROM tasks WHERE 1=1"
    ...

# After
def list_tasks(status="", project="", user_id=None):
    if user_id is None:
        from roost.user_context import get_current_user_id
        user_id = get_current_user_id()
    conn = get_connection()
    query = "SELECT * FROM tasks WHERE user_id = ?"
    params = [user_id]
    ...
```

**MCP tool changes:** Minimal - tools call service functions which auto-resolve user_id from context.

```python
# No change needed in most tools
@mcp.tool()
def list_tasks(status: str = "", project: str = "") -> dict:
    from roost import task_service
    # task_service.list_tasks() internally calls get_current_user_id()
    tasks = task_service.list_tasks(status=status, project=project)
    return {"tasks": [t.model_dump() for t in tasks]}
```

### Phase 4: Per-User Settings and Wellbeing

**`user_settings` schema change:**

```sql
-- Currently: key TEXT PRIMARY KEY
-- Change to: (user_id, key) composite
ALTER TABLE user_settings ADD COLUMN user_id INTEGER REFERENCES users(id);
-- Backfill + recreate with new constraint
```

**Routines:** Add `user_id` to `routines` table. Each user gets their own morning/evening routines.

**Spoons:** Already stored in `user_settings` - just needs the user_id scoping.

**Streaks:** Calculated from `activity_log` which will be user-scoped.

### Phase 5: Web Auth Integration

The web app already has per-user sessions (`request.session["user_id"]`). Tie this to the same `users` table:

- OAuth callback creates/updates user via `upsert_user_from_oauth()`
- Stores MS Graph tokens with `user_id` from session
- Web API endpoints use `request.session["user_id"]` for data filtering

**No new web auth needed.** The existing Google OAuth / basic auth flow already works. MS Graph OAuth callback just needs to tag tokens with user_id.

### Phase 6: User Provisioning

**How users get created:**

1. **Web UI login** - `upsert_user_from_oauth()` creates user on first login
2. **Admin CLI** - `roost user add --email ben@example.com --name "Ben Yeo" --role member`
3. **Telegram** - bot can create users from authorized telegram IDs

**How users get their MCP config:**

1. Admin creates Linux user account (if separate users)
2. Installs Claude Code under their home dir
3. Creates `~/.mcp.json` with `ROOST_USER` env var
4. User visits web UI to complete MS Graph OAuth (tokens stored per-user)

Or for shared Linux account with multiple Claude Code sessions:
1. Each tmux session sets `ROOST_USER` env var before launching Claude Code
2. `~/.mcp.json` reads from env (Claude Code supports `${env:ROOST_USER}` syntax - to verify)

## Implementation Order

### Sprint 1: Foundation (non-breaking) -- DONE 2026-02-24
1. Created `user_context.py` with `UserContext`, `init_user_context()`, `get_current_user_id()`
2. Modified `mcp/server.py` to call `init_user_context()` at startup
3. Added `user_id` column to `oauth_tokens` (rebuild with UNIQUE(user_id, provider, scope))
4. Updated `microsoft/client.py` - all functions accept user_id, auto-resolve from context
5. Updated `gmail/client.py` same pattern
6. Updated `microsoft/auth.py` and `gmail/auth.py` - OAuth callbacks tag tokens with user_id
7. **Verified:** Owner user auto-resolved, tokens scoped per-user, MCP starts cleanly (249 tools)

### Sprint 2: Personal Data Scoping -- DONE 2026-02-24
8. Added `user_id` column to tasks, notes, activity_log, projects (backfilled to owner)
9. Rebuilt `user_settings` with (user_id, key) composite PK
10. Updated `services/tasks.py` - create_task, list_tasks, recalculate_positions, reorder_task, get_focus_tasks, clear_focus, log_activity, get_today_activity all scoped by user_id
11. Updated `services/notes.py` - create_note, list_notes scoped by user_id
12. Updated `services/settings.py` - get/set/delete_setting scoped by user_id
13. Added `user_id` field to Task, Note, Project Pydantic models
14. **Verified:** User 2 sees all existing data, user 1 sees zero tasks/notes/settings. MCP tools need zero changes (auto-resolve from context).

### Sprint 3: Wellbeing + Routines -- DONE 2026-02-24
12. Rebuilt `routines` table with UNIQUE(user_id, name) composite constraint
13. Added user_id to scheduled_emails, servers, claude_sessions (ALTER + backfill)
14. Updated `services/wellbeing.py` — get_routine, add_routine_item scoped by user_id
15. Updated execute_shutdown/execute_resume — task queries scoped by user_id
16. Updated `services/scheduled_emails.py` — schedule_email, list_scheduled, cancel_scheduled scoped
17. Spoons/streaks already work via user-scoped get_setting/set_setting (Sprint 2)

### Sprint 4: Web + Bot Integration -- DONE 2026-02-24
18. Updated `triage.py` — get_today_tasks(), recalculate_all_urgency_scores() scoped by user_id
19. Updated `calendar_service.py` — per-user in-memory cache dict
20. Updated `web/api.py` — threaded user_id from session to create_task, create_note, list_notes, create_project, focus, shutdown/resume, today endpoints
21. Updated `services/projects.py` — create_project accepts user_id, sets owner
22. Updated `services/tasks.py` — suggest_focus accepts user_id
23. Added `resolve_bot_user_id()` helper to `bot/handlers/common.py` — maps telegram_id → user_id with caching

### Sprint 5: Provisioning + Deployment -- DONE 2026-02-24
24. Created `scripts/manage_users.py` — CLI for add/list/remove/set-role with MCP config hints
25. Updated this document with implementation status

## Backward Compatibility

**Critical:** The dev VPS (your_user's instance) must continue working without any config changes.

- If `ROOST_USER` not set: falls back to owner user (first user with role='owner')
- If `user_id` column is NULL: backfill migration assigns to owner
- All service functions default `user_id=None` which resolves to current user context
- Single-user instances behave identically to today

## DeptTools Interaction

DeptTools has its own database (SQLAlchemy) and its own auth model. Multi-tenancy for DeptTools is a separate concern:

- DeptTools data (pipeline, products, deals) is already entity-scoped (by `department` column)
- DeptTools web auth is separate (basic auth or MS Graph)
- DeptTools MCP tools are separate from Roost MCP tools

The key integration point: `FilesService` (OneDrive uploads). Currently uses the single MS Graph token. After multi-tenancy, DeptTools should use a **service account** token (not any individual user's token) for shared operations like uploading generated files to a shared OneDrive folder.

## Decisions (2026-02-24)

1. **Per-user Linux accounts.** Each new team member gets a Linux user account. Their `~/.mcp.json` is provisioned with `ROOST_USER`. Supports `${USER}` expansion so config can be templated.

2. **Projects have owner + members.** Add `user_id` (owner) to `projects` table. The existing `project_members` table handles sharing. Tasks are strictly personal (user_id, no sharing). Projects without an owner are treated as "organizational" (visible to all).

3. **Calendar cache partitioning.** Change module-level dict in `calendar_service.py` to `{user_id: {events, fetched_at}}`. Simple fix.

4. **Gemini API key sharing.** Shared resource (single API key in `.env`). All users on the VPS share the same Gemini quota. Fine for now.

5. **MCP env var support - CONFIRMED.** Claude Code's `~/.mcp.json` supports `env` field and `${VAR}` expansion. Each user's config:
   ```json
   {
     "mcpServers": {
       "roost": {
         "command": "/usr/local/bin/roost-mcp",
         "env": { "ROOST_USER": "${USER}@example.com" }
       }
     }
   }
   ```

## Effort Estimate

- Sprint 1 (Foundation): ~3 hours
- Sprint 2 (Data scoping): ~4 hours
- Sprint 3 (Wellbeing): ~2 hours
- Sprint 4 (Web + Bot): ~3 hours
- Sprint 5 (Provisioning): ~2 hours
- Total: ~14 hours of implementation + testing
