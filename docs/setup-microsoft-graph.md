# Setting Up Microsoft Graph API for Roost

Microsoft Graph integration enables Outlook email, Calendar, OneDrive, Excel Online, Teams, and SharePoint access via 38 MCP tools (`ms_` prefix). This runs alongside the existing Google Workspace integration — both providers coexist.

Created: 2026-02-15

---

## Overview

| Component | Details |
|-----------|---------|
| **Auth library** | MSAL (Microsoft Authentication Library) — handles token acquisition, caching, silent refresh |
| **API** | Microsoft Graph REST API v1.0 via `requests` |
| **Token storage** | MSAL's `SerializableTokenCache` JSON blob in `oauth_tokens` table (`provider='microsoft'`, `scope='graph'`) |
| **Tools** | 4 email + 7 calendar + 4 OneDrive + 3 Excel + 16 Teams + 4 SharePoint = **38 MCP tools** |
| **Config vars** | `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID`, `MS_ENABLED` |

---

## Step 1: Register an App in Azure Portal

1. Go to https://portal.azure.com
2. Navigate to **Microsoft Entra ID** → **App registrations** → **+ New registration**
3. Fill in:
   - **Name**: `Roost`
   - **Supported account types**: Choose one:
     - "Accounts in this organizational directory only" (single-tenant, your org only)
     - "Accounts in any organizational directory" (multi-tenant, if Ben or others are in different tenants)
     - "Accounts in any organizational directory and personal Microsoft accounts" (broadest)
   - **Redirect URI**: Platform = **Web**, URI = `https://YOUR_DOMAIN/auth/microsoft/callback`
     - Replace `YOUR_DOMAIN` with your actual domain (e.g. `vps.example.com:8080`)
     - Must be HTTPS in production. For local testing, `http://localhost:8080/auth/microsoft/callback` works
4. Click **Register**
5. Note the **Application (client) ID** — this is `MS_CLIENT_ID`
6. Note the **Directory (tenant) ID** — this is `MS_TENANT_ID`
   - Use `common` if you selected multi-tenant above

## Step 2: Configure API Permissions (Scopes)

This is the critical step — these permissions determine what the app can access.

1. In your app registration, go to **API permissions** → **+ Add a permission**
2. Select **Microsoft Graph** → **Delegated permissions**
3. Add all **13 permissions** listed below:

### Full Scope List

| # | Permission | Category | What it enables | Tools |
|:-:|------------|----------|-----------------|-------|
| 1 | `Mail.Read` | Mail | Read email - search, list folders, read conversations | `ms_search_emails`, `ms_read_conversation`, `ms_list_folders` |
| 2 | `Mail.Send` | Mail | Send email on behalf of the user | `ms_send_email` |
| 3 | `Calendars.ReadWrite` | Calendars | Read, create, update, delete calendar events | `ms_get_today_events`, `ms_get_week_events`, `ms_calendar_*` |
| 4 | `Files.ReadWrite` | Files | Read/write files in user's OneDrive + Excel workbooks | `ms_onedrive_*`, `ms_excel_*` |
| 5 | `Sites.ReadWrite.All` | Sites | Read/write SharePoint sites and document libraries | `ms_sharepoint_*` |
| 6 | `Team.ReadBasic.All` | Teams | List teams the user has joined | `ms_teams_list_teams` |
| 7 | `Channel.ReadBasic.All` | Teams | List channels within a team | `ms_teams_list_channels` |
| 8 | `ChannelMessage.Read.All` | Teams | Read channel messages | `ms_teams_read_messages` |
| 9 | `ChannelMessage.Send` | Teams | Send messages to channels | `ms_teams_send_message`, `ms_teams_reply_channel_message` |
| 10 | `Chat.ReadWrite` | Teams | List chats, read/send chat messages, create chats, list members, reactions | `ms_teams_list_chats`, `ms_teams_read_chat`, `ms_teams_send_chat`, `ms_teams_create_chat`, `ms_teams_list_chat_members`, `ms_teams_add_reaction`, `ms_teams_remove_reaction` |
| 11 | `User.Read` | User | Sign in and read basic profile (required for auth) | *(auth flow)* |
| 12 | `User.ReadBasic.All` | User | Look up users by email in the tenant directory | `ms_teams_lookup_user`, `ms_teams_create_chat` |
| 13 | `offline_access` | OpenId | Refresh tokens for long-lived access | *(token refresh)* |

4. After adding all permissions, verify the list shows:

```
Microsoft Graph (13)
├── Calendars.ReadWrite         Delegated    Read and write user calendars
├── Channel.ReadBasic.All       Delegated    Read the names and descriptions of channels
├── ChannelMessage.Read.All     Delegated    Read user channel messages
├── ChannelMessage.Send         Delegated    Send channel messages
├── Chat.ReadWrite              Delegated    Read and write user chat messages
├── Files.ReadWrite             Delegated    Have full access to user files
├── Mail.Read                   Delegated    Read user mail
├── Mail.Send                   Delegated    Send mail as a user
├── offline_access              Delegated    Maintain access to data you have given it access to
├── Sites.ReadWrite.All         Delegated    Edit or delete items in all site collections
├── Team.ReadBasic.All          Delegated    Read the names and descriptions of teams
├── User.Read                   Delegated    Sign in and read user profile
└── User.ReadBasic.All          Delegated    Read all users' basic profiles
```

5. **Admin consent** — several of these scopes **require admin consent** in most tenants:
   - `Sites.ReadWrite.All` — almost always requires admin consent
   - `ChannelMessage.Read.All` — often requires admin consent
   - `Team.ReadBasic.All`, `Channel.ReadBasic.All` — may require admin consent
   - Click **Grant admin consent for [org]** if the Status shows "Not granted"
   - For personal Microsoft accounts, user consent is sufficient for most

### Permission Notes

- All permissions are **Delegated** (act on behalf of a signed-in user), not Application-level
- `Mail.Read` (not `Mail.ReadWrite`) — read-only mailbox access; sending uses the separate `Mail.Send`
- `Files.ReadWrite` (not `Files.ReadWrite.All`) — scoped to user's own OneDrive, not all org files
- `Sites.ReadWrite.All` — the `.All` is necessary because SharePoint sites are shared resources
- `Chat.ReadWrite` covers listing chats, reading messages, AND sending — one scope for all chat ops
- `offline_access` enables refresh tokens so the user doesn't need to re-authenticate every hour

## Step 3: Create a Client Secret

1. In your app registration, go to **Certificates & secrets** → **+ New client secret**
2. Description: `Roost production` (or similar)
3. Expiry: Choose your preference (6 months, 12 months, 24 months, or custom)
   - **Set a calendar reminder** to rotate before expiry
4. Click **Add**
5. **Copy the Value immediately** — it's only shown once. This is `MS_CLIENT_SECRET`
   - Do NOT copy the "Secret ID" — you need the "Value" column

## Step 4: Configure Roost

Edit `/home/dev/projects/roost/.env` and add:

```bash
# Microsoft Graph OAuth (Azure AD)
MS_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MS_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
MS_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MS_ENABLED=true
```

Replace with the actual values from Steps 1 and 3.

If multi-tenant, set `MS_TENANT_ID=common`.

Then restart the web server:

```bash
systemctl --user restart roost-web
```

## Step 5: Complete the OAuth Consent Flow

1. Open `https://YOUR_DOMAIN/auth/microsoft` in a browser
2. Sign in with the Microsoft account you want to connect
3. Review and accept the permissions (you'll see all 12 listed)
4. You'll be redirected back with a success message: "Microsoft Graph authorized!"
5. Verify the token was stored:

```bash
sqlite3 /home/dev/projects/roost/data/roost.db \
  "SELECT provider, scope, length(refresh_token) FROM oauth_tokens WHERE provider='microsoft'"
```

Expected output: `microsoft|graph|<some number>` (the number is the JSON cache size)

## Step 6: Verify MCP Tools

```bash
# Check Microsoft is available
python3 -c "from roost.microsoft import is_microsoft_available; print(is_microsoft_available())"
# Should print: True

# Test via Claude Code MCP tools
# ms_search_emails(query="test")
# ms_get_today_events()
# ms_onedrive_list(path="/")
# ms_excel_list_worksheets(file_path="/Documents/budget.xlsx")
# ms_teams_list_teams()
# ms_sharepoint_list_sites()
```

---

## Architecture

```
roost/microsoft/
├── __init__.py    # MS_SCOPES (13 scopes), is_microsoft_available(), get_graph_session()
├── client.py      # MSAL token cache (DB-backed), get_access_token(), build_graph_session()
└── auth.py        # FastAPI OAuth routes (/auth/microsoft, /auth/microsoft/callback)

roost/mcp/
├── ms_graph_helpers.py      # Graph API request/retry/pagination + all business functions
├── tools_ms_email.py        # 4 tools: search, read conversation, send, list folders
├── tools_ms_calendar.py     # 7 tools: today, week, create, update, delete, list, search
├── tools_ms_onedrive.py     # 4 tools: list, download, upload (auto simple/resumable), search
├── tools_ms_excel.py        # 3 tools: list worksheets, read range, write range
├── tools_ms_teams.py        # 16 tools: teams/channels/chats, replies, members, reactions, files, images
└── tools_ms_sharepoint.py   # 4 tools: list sites, list files, download, upload
```

### Token Storage

MSAL manages the full token lifecycle internally. We store its `SerializableTokenCache` (a JSON blob containing access tokens, refresh tokens, and ID tokens) in the existing `oauth_tokens` table:

| Column | Value |
|--------|-------|
| `provider` | `microsoft` |
| `scope` | `graph` |
| `refresh_token` | The entire MSAL cache JSON (reusing the column — no schema change) |

On every `get_access_token()` call, MSAL silently refreshes if needed. If the cache changed, it's written back to DB.

### Request Handling

- **Rate limiting**: 429 responses are retried with exponential backoff (respects `Retry-After` header), up to 3 retries
- **Pagination**: `@odata.nextLink` is followed automatically up to a configurable max items
- **Body truncation**: Email and Teams message bodies capped at 10K characters
- **Error handling**: All tools return `{"error": str(e)}` on failure (same pattern as all other MCP tools)

---

## MCP Tools Reference (38 tools)

### Email (4 tools)

| Tool | Signature | Description |
|------|-----------|-------------|
| `ms_search_emails` | `(query, max_results=10)` | Search Outlook using KQL syntax |
| `ms_read_conversation` | `(conversation_id)` | Read all messages in a conversation thread |
| `ms_send_email` | `(to, subject, body, cc, bcc, reply_to_id, attachment_paths)` | Send email (draft-first workflow enforced) |
| `ms_list_folders` | `()` | List mail folders with unread/total counts |

### Calendar (7 tools)

| Tool | Signature | Description |
|------|-----------|-------------|
| `ms_get_today_events` | `()` | Get all events for today |
| `ms_get_week_events` | `(days=7)` | Get events for next N days |
| `ms_calendar_create_event` | `(summary, start, end, ...)` | Create a new event |
| `ms_calendar_update_event` | `(event_id, ...)` | Update an existing event (partial update) |
| `ms_calendar_delete_event` | `(event_id)` | Delete an event |
| `ms_calendar_list_calendars` | `()` | List all accessible calendars |
| `ms_calendar_search_events` | `(query, days=30)` | Search events by text (local filter) |

### OneDrive (4 tools)

| Tool | Signature | Description |
|------|-----------|-------------|
| `ms_onedrive_list` | `(path="/", max_results=100)` | List files/folders at a path |
| `ms_onedrive_download` | `(remote_path, local_dir)` | Download a file to local filesystem |
| `ms_onedrive_upload` | `(local_path, remote_path)` | Upload a file (auto-selects simple <4MB or resumable up to 250MB) |
| `ms_onedrive_search` | `(query)` | Search files by name/content |

### Excel (3 tools)

| Tool | Signature | Description |
|------|-----------|-------------|
| `ms_excel_list_worksheets` | `(file_path)` | List worksheets in a .xlsx file on OneDrive |
| `ms_excel_read_range` | `(file_path, worksheet, cell_range)` | Read cells (returns 2D values array + formulas) |
| `ms_excel_write_range` | `(file_path, worksheet, cell_range, values)` | Write cells (2D values array) |

### Teams (16 tools)

| Tool | Signature | Description |
|------|-----------|-------------|
| `ms_teams_list_teams` | `()` | List teams the user has joined |
| `ms_teams_list_channels` | `(team_id)` | List channels in a team |
| `ms_teams_read_messages` | `(team_id, channel_id, max_results=20)` | Read recent channel messages |
| `ms_teams_send_message` | `(team_id, channel_id, content)` | Send a channel message (outbound guard) |
| `ms_teams_reply_channel_message` | `(team_id, channel_id, message_id, content)` | Reply to a channel message (threaded reply) |
| `ms_teams_list_chats` | `(max_results=20)` | List 1:1 and group chats |
| `ms_teams_read_chat` | `(chat_id, max_results=20)` | Read recent chat messages |
| `ms_teams_send_chat` | `(chat_id, content)` | Send a chat message (outbound guard) |
| `ms_teams_list_chat_members` | `(chat_id)` | List chat members (auto-links to contacts) |
| `ms_teams_create_chat` | `(member_emails, topic, message)` | Create 1:1 or group chat by email |
| `ms_teams_lookup_user` | `(email)` | Resolve email to Azure AD user ID (auto-links to contacts) |
| `ms_teams_add_reaction` | `(chat_id, message_id, reaction)` | React to a message (like, heart, laugh, angry, sad, surprised) |
| `ms_teams_remove_reaction` | `(chat_id, message_id, reaction)` | Remove a reaction |
| `ms_teams_download_images` | `(chat_id, local_dir, max_messages)` | Download images + file attachments from chat (inline, hosted, SharePoint/OneDrive) |
| `ms_teams_list_channel_files` | `(team_id, channel_id, path, max_results)` | List files in a channel's SharePoint folder |
| `ms_teams_download_channel_file` | `(drive_id, item_id, local_dir)` | Download a file from a channel's SharePoint folder |

### SharePoint (4 tools)

| Tool | Signature | Description |
|------|-----------|-------------|
| `ms_sharepoint_list_sites` | `(query="")` | List followed sites, or search all sites |
| `ms_sharepoint_list_files` | `(site_id, path="/", max_results=100)` | List files in a site's document library |
| `ms_sharepoint_download` | `(site_id, file_path, local_dir)` | Download from SharePoint |
| `ms_sharepoint_upload` | `(site_id, local_path, remote_path)` | Upload to SharePoint (<4MB) |

---

## Security

### Outbound Guard

These tools are gated by the PreToolUse hook (`~/.claude/hooks/outbound-guard.sh`):

| Tool | Confirmation shown |
|------|--------------------|
| `ms_send_email` | "Sending Outlook email to X - 'Subject'" |
| `ms_teams_send_message` | "Sending Teams channel message (team=... chan=...)" |
| `ms_teams_send_chat` | "Sending Teams chat message (chat=...)" |
| `ms_teams_reply_channel_message` | "Replying to Teams channel message" |
| `ms_teams_create_chat` | "Creating Teams chat with ..." |

Matcher regex in `~/.claude/settings.local.json` covers all three.

### Draft-First Workflow

Same rule as Gmail: **never send via any outbound tool without presenting the content to the user first.** This applies to emails, Teams channel messages, and chat messages — no exceptions.

### Scope Notes

- `Mail.Read` (not `Mail.ReadWrite`) — read-only mailbox
- `Files.ReadWrite` (not `Files.ReadWrite.All`) — user's own OneDrive only
- `Sites.ReadWrite.All` — `.All` required because SharePoint is shared
- `Chat.ReadWrite` — one scope for list + read + send chat ops
- All delegated (not application) — acts on behalf of signed-in user only

---

## Known Limitations

| Limitation | Detail | Future Fix |
|-----------|--------|------------|
| Upload size | OneDrive: auto simple/resumable up to 250MB. SharePoint: simple upload <4MB | Add resumable SharePoint upload |
| Calendar search | Fetches then filters locally | Graph `calendarView` doesn't support `$search` |
| Email search delay | Graph `$search` requires indexed mailbox | Very new messages may not appear immediately |
| Excel formulas | Can read formulas but writing formulas untested | Test with `=SUM()` etc. |
| Teams message format | Content sent as HTML | Plain text auto-wrapped |
| No Word/PPT editing | Graph has no inline document editing API | Download → edit with python-docx/pptx → re-upload |

---

## Troubleshooting

### "No Microsoft accounts in token cache"

The OAuth flow hasn't been completed. Visit `/auth/microsoft` in a browser.

### "Token refresh failed"

The refresh token may have expired or been revoked. Re-authorize at `/auth/microsoft`.

### 403 Forbidden on specific operations

The required permission hasn't been granted. Check API permissions in Azure Portal:

| Operation | Required scope |
|-----------|---------------|
| Email | `Mail.Read` + `Mail.Send` |
| Calendar | `Calendars.ReadWrite` |
| OneDrive / Excel | `Files.ReadWrite` |
| SharePoint | `Sites.ReadWrite.All` |
| Teams — list teams/channels | `Team.ReadBasic.All` + `Channel.ReadBasic.All` |
| Teams — read channel messages | `ChannelMessage.Read.All` |
| Teams — send channel messages | `ChannelMessage.Send` |
| Teams - chats | `Chat.ReadWrite` |
| Teams - user lookup / create chat | `User.ReadBasic.All` |

If admin consent is required, an org admin must click "Grant admin consent" in Azure Portal.

### Client secret expired

Azure AD client secrets have an expiry. Check in Azure Portal → Certificates & secrets. Create a new secret and update `MS_CLIENT_SECRET` in `.env`.

### Multi-tenant vs single-tenant

If using `MS_TENANT_ID=common` (multi-tenant) and getting errors, ensure "Accounts in any organizational directory" was selected during app registration. For single-tenant, use the actual tenant ID from Azure Portal.
