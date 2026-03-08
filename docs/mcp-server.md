# Roost MCP Server

MCP (Model Context Protocol) server exposing roost as native Claude Code tools. Uses FastMCP 2.x with stdio transport. This is the 4th interface alongside CLI, Web, and Telegram Bot.

**285 tools across 44 modules** - tasks, contacts, projects, entities, calendar, Gmail, Drive, Slides, Sheets, Docs, Gemini (incl. image generation), Notion, SSH, Docker, Kubernetes, Microsoft 365 (email, calendar, OneDrive, Excel, Teams, SharePoint), OKR management, time tracking, productivity stats, document generation, presentations, scheduled emails, context bundles.

## Quick Start

The server is registered in `~/.mcp.json` and starts automatically when Claude Code needs it. After installation, restart Claude Code to discover the tools (`/mcp` to verify).

## Entry Point

```
roost-mcp
```

Installed to `~/.local/bin/roost-mcp` via `setup.py` console_scripts.

---

## Tools Reference (285 tools)

### Tasks — CRUD (4 tools)

| Tool | Description |
|------|-------------|
| `create_task` | Create a new task with title, priority, deadline, etc. |
| `list_tasks` | List tasks with filters (status, project, priority, deadline, energy) |
| `update_task` | Update any field on an existing task |
| `complete_task` | Mark a task as done |

### Tasks — Extended (22 tools)

| Tool | Description |
|------|-------------|
| `add_task_dependency` | Create a blocking dependency between two tasks |
| `remove_task_dependency` | Remove a dependency |
| `get_task_blockers` | Show what blocks a task and what it blocks |
| `list_subtasks` | List a task's children |
| `reorder_task` | Move a task to a new position |
| `recalculate_task_positions` | Re-number all task positions |
| `mark_task_wip` | Set task to in-progress with optional context note |
| `get_next_task` | Smart pick: highest urgency non-blocked task |
| `pick_random_task` | Random selection from eligible tasks |
| `set_task_last_worked` | Update the "last worked" timestamp |
| `get_task_progress` | Completion stats per project |
| `get_today_activity` | Activity log for today |
| `assign_task` | Assign a contact to a task with RACI role |
| `unassign_task` | Remove a task assignment |
| `list_task_assignments_mcp` | List all assignments for a task |
| `get_focus_tasks` | Get tasks focused for today |
| `set_focus` | Focus a task for today (or a specific date) |
| `clear_focus` | Remove focus from a task |
| `suggest_focus` | AI-suggested tasks to focus on today |
| `shelve_task` | Move task to someday/maybe list |
| `unshelve_task` | Restore a shelved task |
| `list_someday_tasks` | List all shelved/someday tasks |

### Activity Tracking (4 tools)

| Tool | Description |
|------|-------------|
| `set_active_task` | Set the currently active task for work linking |
| `get_active_task` | Check which task is currently active |
| `log_activity` | Log a significant action against the active task |
| `get_task_activity` | View activity history for a task |

### Wellbeing (13 tools)

| Tool | Description |
|------|-------------|
| `get_routine` | View daily routine items and completion status |
| `add_routine_item` | Add an item to the daily routine |
| `remove_routine_item` | Remove a routine item |
| `complete_routine_item` | Mark a routine item done |
| `uncomplete_routine_item` | Unmark a routine item |
| `get_spoon_status` | Check current energy budget (spoon theory) |
| `set_spoon_budget` | Set daily energy budget |
| `spend_spoons` | Deduct energy from budget |
| `reset_spoons` | Reset energy to full budget |
| `check_shutdown_status` | Check if shutdown protocol is active |
| `execute_shutdown` | End-of-day wind-down — pause tasks, defer deadlines |
| `execute_resume` | Resume from shutdown |
| `get_streak_status` | Check daily task completion streak |

### Contacts (13 tools)

| Tool | Description |
|------|-------------|
| `search_contacts` | Find a contact by name (partial match) with affiliations |
| `list_contacts` | List all contacts, optionally filtered by entity |
| `get_contact` | Get a specific contact by ID with affiliations and identifiers |
| `create_contact` | Create a new contact (auto-creates email/phone identifiers) |
| `update_contact` | Update contact fields |
| `add_contact_to_entity` | Link a contact to an entity |
| `list_contact_entities` | List contact-entity affiliations |
| `remove_contact_from_entity` | Remove a contact-entity affiliation |
| `set_contact_identifier` | Add/update a contact identifier (email, phone, microsoft, telegram, linkedin, etc.) |
| `remove_contact_identifier` | Remove a contact identifier |
| `find_contact_by_identifier` | Find a contact by any identifier type and value |
| `harvest_email_contacts` | Scan Gmail for contacts, cross-reference existing DB, optionally create new contacts (dry_run first) |
| `import_google_contacts` | Import contacts from Google Contacts (People API) with dedup and identifier merging |

### Communications (5 tools)

| Tool | Description |
|------|-------------|
| `log_communication` | Log a call, meeting, or message to a contact's timeline |
| `get_contact_history` | View communication history for a contact |
| `sync_contact_emails` | Pull recent emails for a contact from Gmail |
| `sync_contact_meetings` | Pull recent meetings for a contact from Calendar |
| `delete_communication` | Remove a communication log entry |

### Projects (4 tools)

| Tool | Description |
|------|-------------|
| `list_projects` | List projects with filters (status, category, entity) |
| `get_project` | Get a project with children and team assignments |
| `create_project` | Create a new project |
| `update_project` | Update project fields |

### Entities (2 tools)

| Tool | Description |
|------|-------------|
| `list_entities` | List all entities (companies/organisations) |
| `get_entity` | Get an entity with its projects and people |

### Calendar — Read (2 tools)

| Tool | Description |
|------|-------------|
| `get_today_events` | Get all calendar events for today |
| `get_week_events` | Get calendar events for the next N days |

### Calendar — Write (5 tools)

| Tool | Description |
|------|-------------|
| `calendar_create_event` | Create a new event (timed or all-day) |
| `calendar_update_event` | Update event fields (summary, time, location, etc.) |
| `calendar_delete_event` | Delete an event |
| `calendar_list_calendars` | List all accessible calendars with IDs and roles |
| `calendar_search_events` | Search events by text query within a date range |

#### Calendar Examples

```
# Create a meeting
calendar_create_event(
    summary="Team standup",
    start="2026-02-15T09:00:00",
    end="2026-02-15T09:30:00",
    location="Zoom"
)

# Create an all-day event
calendar_create_event(
    summary="Project deadline",
    start="2026-02-20",
    end="2026-02-21",
    all_day=True
)

# Search for events
calendar_search_events(query="standup", days=14)
```

### Gmail (7 tools)

| Tool | Description |
|------|-------------|
| `search_emails` | Search Gmail using Gmail query syntax |
| `read_thread` | Read all messages in an email thread |
| `send_email` | Send an email (with threading and attachment support) |
| `list_labels` | List all Gmail labels |
| `list_attachments` | List attachments in an email thread |
| `download_attachments` | Download email attachments to local filesystem |
| `list_google_accounts` | List all connected Google accounts on this instance |

**CRITICAL: Draft-first workflow.** Never send an email directly — always present the draft to the user and wait for approval.

### Google Drive (4 tools)

| Tool | Description |
|------|-------------|
| `drive_list` | List files/folders in a Drive path (via rclone) |
| `drive_download` | Download file/folder from Drive to local filesystem |
| `drive_upload` | Upload local file/directory to Drive |
| `drive_search` | Recursively search Drive by filename pattern |

#### Drive Examples

```
drive_list("gdrive:YourOrg/Programmes/SCTP-AI-Security-Architecture/")

# Find all M5 documents
drive_search("M5", "gdrive:YourOrg/Programmes/SCTP-AI-Security-Architecture/")


# Upload edited documents back
```

### Google Slides (8 tools)

| Tool | Description |
|------|-------------|
| `pptx_list_placeholders` | List placeholders in a PowerPoint template |
| `pptx_replace_text` | Replace `{{placeholder}}` text in a .pptx file |
| `pptx_get_slide_notes` | Extract speaker notes from a .pptx |
| `pptx_duplicate_slide` | Duplicate a slide within a .pptx file |
| `gslides_list_placeholders` | List placeholders in a Google Slides presentation |
| `gslides_replace_text` | Replace text in a Google Slides presentation |
| `gslides_create_from_template` | Copy a Slides template and fill placeholders |
| `gslides_replace_image` | Replace an image in Google Slides by URL |

### Google Sheets (5 tools)

| Tool | Description |
|------|-------------|
| `gsheets_read_range` | Read a cell range from a spreadsheet |
| `gsheets_write_range` | Write data to a cell range |
| `gsheets_append_rows` | Append rows to the end of a sheet |
| `gsheets_create_from_template` | Copy a Sheets template |
| `gsheets_get_metadata` | Get spreadsheet metadata (sheets, titles, sizes) |

### Google Docs (5 tools)

| Tool | Description |
|------|-------------|
| `gdocs_read_content` | Read document content as plain text |
| `gdocs_replace_text` | Find and replace text in a document |
| `gdocs_create_from_template` | Copy a Docs template and fill placeholders |
| `gdocs_append_text` | Append text to the end of a document |
| `gdocs_insert_image` | Insert an image at a position in the document |

### Document Generation (2 tools)

| Tool | Description |
|------|-------------|
| `convert_document` | Convert between formats using pandoc (MD→DOCX, PDF, HTML, etc.) |
| `convert_and_upload` | Convert a document and upload to Google Drive in one step |

#### Doc Generation Examples

```
# Convert markdown to Word
convert_document("/home/dev/projects/doc.md", "docx")

# Convert and upload to Drive in one step
convert_and_upload(
    "/home/dev/projects/programmes/ai-security/deliverables/M5-slides.md",
    "docx",
    "gdrive:YourOrg/Programmes/SCTP-AI-Security-Architecture/deliverables/"
)

# Convert to PDF
convert_document("/tmp/report.md", "pdf", "/tmp/report.pdf")
```

### Gemini AI Pipeline (10 tools)

| Tool | Description |
|------|-------------|
| `gemini_summarize` | Summarize text using Gemini |
| `gemini_generate` | Single-shot generation with Claude-assembled context and output_file |
| `gemini_image` | Generate images via Gemini or Imagen 4 models (5 models, reference images, multi-image) |
| `gemini_vision` | Analyze images using Gemini's vision capabilities |
| `gemini_process_document` | Process a document with custom instructions |
| `gemini_clean_text` | Clean and format text |
| `gemini_compare` | Compare two texts and highlight differences |
| `gemini_research` | Research a topic using Gemini's knowledge |
| `gemini_agent` | Autonomous multi-step agent with file tools and context management |
| `gemini_usage` | Check Gemini API usage stats |

### Notion (16 tools)

| Tool | Description |
|------|-------------|
| `notion_search` | Search pages and databases by title |
| `notion_get_page` | Retrieve a page's properties |
| `notion_create_page` | Create a new page (in database or as subpage) |
| `notion_update_page` | Update a page's properties |
| `notion_get_block_children` | Read content blocks of a page |
| `notion_append_blocks` | Append content blocks to a page |
| `notion_update_block` | Update a single block |
| `notion_delete_block` | Delete (archive) a block |
| `notion_query_database` | Query a database with filters and sorts |
| `notion_get_database` | Retrieve a database's schema |
| `notion_create_comment` | Add a comment to a page |
| `notion_list_comments` | List comments on a page/block |
| `notion_archive_page` | Archive (soft-delete) a page |
| `notion_restore_page` | Restore an archived page |
| `notion_create_database` | Create a new database as child of a page |
| `notion_duplicate_page` | Duplicate a page — copies properties + content blocks |

### Time Tracking (5 tools)

| Tool | Description |
|------|-------------|
| `start_timer` | Start a timer linked to a task (auto-stops any running timer) |
| `stop_timer` | Stop the currently running timer |
| `get_running_timer` | Check if a timer is running and elapsed time |
| `get_time_entries` | Get completed time entries (filterable by task/days) |
| `get_time_summary` | Time summary grouped by task over a period |

#### Time Tracking Examples

```
# Start tracking time on a task
start_timer(task_id=42, note="Working on API refactor")

# Check what's running
get_running_timer()

# Stop and record
stop_timer()

# Review time spent this week
get_time_summary(days=7)
```

### Productivity Stats (4 tools)

| Tool | Description |
|------|-------------|
| `get_completed_task_history` | History of completed tasks (filterable by days/project) |
| `get_daily_completion_counts` | Daily counts for trend analysis |
| `get_productivity_summary` | Comprehensive summary: completions, by priority/project, spoons, streaks, time |
| `get_weekly_review` | Weekly review: completed, in progress, blocked, overdue, upcoming deadlines |

### SSH & SCP (7 tools)

| Tool | Description |
|------|-------------|
| `register_server` | Register a remote server for management |
| `list_servers` | List registered servers |
| `update_server` | Update server configuration |
| `remove_server` | Delete a server registration |
| `ssh_exec` | Execute a command on a remote server |
| `scp_upload` | Upload a file to a remote server |
| `scp_download` | Download a file from a remote server |

**Security:** SSH and SCP output is automatically sanitized to mask secrets (API keys, tokens, passwords, private keys). See [Secrets Masking](#secrets-masking) below.

### Docker (6 tools)

| Tool | Description |
|------|-------------|
| `docker_ps` | List containers on a remote server |
| `docker_logs` | View container logs |
| `docker_pull` | Pull a Docker image |
| `docker_compose_up` | Start services with docker-compose |
| `docker_compose_down` | Stop services with docker-compose |
| `docker_compose_ps` | List docker-compose services |

### Kubernetes (5 tools)

| Tool | Description |
|------|-------------|
| `kubectl_get` | Get resources (pods, services, deployments, etc.) |
| `kubectl_describe` | Describe a specific resource |
| `kubectl_logs` | View pod logs |
| `kubectl_apply` | Apply a manifest (file or inline YAML) |
| `kubectl_delete` | Delete a resource |

**Security:** All kubectl output is automatically sanitized. See [Secrets Masking](#secrets-masking).

### Microsoft Email (4 tools)

| Tool | Description |
|------|-------------|
| `ms_search_emails` | Search Outlook emails (KQL syntax) |
| `ms_read_conversation` | Read all messages in an email conversation |
| `ms_send_email` | Send an email via Outlook |
| `ms_list_folders` | List Outlook mail folders |

### Microsoft Calendar (7 tools)

| Tool | Description |
|------|-------------|
| `ms_get_today_events` | Get today's calendar events |
| `ms_get_week_events` | Get events for the next N days |
| `ms_calendar_create_event` | Create a calendar event |
| `ms_calendar_update_event` | Update an event |
| `ms_calendar_delete_event` | Delete an event |
| `ms_calendar_list_calendars` | List all calendars |
| `ms_calendar_search_events` | Search events by text query |

### Microsoft OneDrive (4 tools)

| Tool | Description |
|------|-------------|
| `ms_onedrive_list` | List files/folders |
| `ms_onedrive_download` | Download a file |
| `ms_onedrive_upload` | Upload a file (<4MB simple upload) |
| `ms_onedrive_search` | Search for files |

### Microsoft Excel (3 tools)

| Tool | Description |
|------|-------------|
| `ms_excel_list_worksheets` | List worksheets in a workbook |
| `ms_excel_read_range` | Read a cell range |
| `ms_excel_write_range` | Write to a cell range |

### Microsoft Teams (16 tools)

| Tool | Description |
|------|-------------|
| `ms_teams_list_teams` | List joined teams |
| `ms_teams_list_channels` | List channels in a team |
| `ms_teams_read_messages` | Read channel messages |
| `ms_teams_send_message` | Send a channel message |
| `ms_teams_reply_channel_message` | Reply to a channel message (threaded) |
| `ms_teams_list_chats` | List recent chats |
| `ms_teams_read_chat` | Read chat messages |
| `ms_teams_send_chat` | Send a chat message |
| `ms_teams_list_chat_members` | List chat members (auto-links to contacts) |
| `ms_teams_create_chat` | Create 1:1 or group chat by email |
| `ms_teams_lookup_user` | Resolve email to Azure AD user ID |
| `ms_teams_add_reaction` | React to a message (like, heart, laugh, angry, sad, surprised) |
| `ms_teams_remove_reaction` | Remove a reaction |
| `ms_teams_download_images` | Download images + files from chat messages |
| `ms_teams_list_channel_files` | List files in a channel's SharePoint folder |
| `ms_teams_download_channel_file` | Download a file from a channel's SharePoint folder |

### Microsoft SharePoint (4 tools)

| Tool | Description |
|------|-------------|
| `ms_sharepoint_list_sites` | List SharePoint sites |
| `ms_sharepoint_list_files` | List files in a document library |
| `ms_sharepoint_download` | Download a file |
| `ms_sharepoint_upload` | Upload a file |


| Tool | Description |
|------|-------------|

### OKR Management (12 tools)

| Tool | Description |
|------|-------------|
| `create_okr_cycle` | Create an OKR cycle (quarterly, etc.) |
| `list_okr_cycles` | List OKR cycles |
| `update_okr_cycle` | Update cycle details |
| `create_okr_objective` | Create an objective |
| `update_okr_objective` | Update an objective |
| `create_okr_key_result` | Create a key result |
| `update_okr_key_result` | Update a key result |
| `link_task_to_kr` | Link a task to a key result |
| `unlink_task_from_kr` | Unlink a task from a key result |
| `get_okr_dashboard` | Dashboard view of OKR progress |
| `okr_checkin` | Record a check-in against a key result |
| `get_okr_scorecard` | Scorecard summary of all OKRs |


| Tool | Description |
|------|-------------|

### Presentations & Meeting Notes (3 tools)

| Tool | Description |
|------|-------------|
| `generate_presentation` | Generate a slide deck from a prompt |
| `structure_meeting_notes` | Structure raw meeting notes into sections |
| `transcribe_audio` | Transcribe an audio file using faster-whisper |

### Scheduled Email (3 tools)

| Tool | Description |
|------|-------------|
| `schedule_email` | Schedule an email for future delivery |
| `list_scheduled_emails` | List pending scheduled emails |
| `cancel_scheduled_email` | Cancel a scheduled email |

### Context Bundles (3 tools)

| Tool | Description |
|------|-------------|
| `morning_briefing` | Today's calendar + in-progress tasks + overdue + energy budget + awaiting replies |
| `project_pulse` | Tasks by status + upcoming events + recent emails for a project |
| `prep_for` | Contact details + communication history + related tasks + recent emails |

### Web Scraping (2 tools)

| Tool | Description |
|------|-------------|
| `scrape_url` | Scrape a web page and return clean text content |
| `scrape_js` | Scrape a JavaScript-rendered page using headless Chrome |

### Claude Sessions (4 tools)

| Tool | Description |
|------|-------------|
| `list_claude_sessions` | List Claude Code sessions |
| `create_claude_session` | Create a new session |
| `connect_claude_session` | Attach to a session |
| `close_claude_session` | Close a session |

### Telegram (2 tools)

| Tool | Description |
|------|-------------|
| `telegram_send_message` | Send a message via Telegram bot |
| `telegram_get_bot_info` | Get bot info and status |

### Text Cleanup (1 tool)

| Tool | Description |
|------|-------------|
| `fix_dashes` | Replace em-dashes and en-dashes with hyphens in files |

### Charts (15 tools)

Birth chart calculations across 4 systems: Human Design, BaZi (Four Pillars), Western (tropical), and Vedic (sidereal/Lahiri). Validated against SharpAstrology .NET reference implementation. Includes database-wide query modes for relationship analysis across all stored profiles.

| Tool | Description |
|------|-------------|
| `calculate_human_design` | HD chart - type, profile, authority, definition, incarnation cross, variables, centers, channels, gates |
| `calculate_bazi_chart` | BaZi Four Pillars - day master, ten gods, hidden stems, DM strength, elements, luck pillars |
| `calculate_western_chart` | Western tropical - planet positions, houses, ascendant, aspects, element/modality balance |
| `calculate_vedic_chart` | Vedic sidereal (Lahiri) - graha positions, moon nakshatra, Vimshottari Dasha with Bhuktis |
| `calculate_full_chart` | All 4 systems combined in one call |
| `calculate_chart_timing` | Current timing - BaZi luck/annual/monthly pillars + HD transit gates |
| `calculate_chart_composite` | Relationship analysis - electromagnetic channels, element compatibility |
| `chart_db_lookup` | Look up stored chart data by name from YAML database |
| `chart_db_list` | List all people in the chart database |
| `chart_db_fill` | Who fills a person's missing HD centers (natal + electromagnetic) |
| `chart_db_emfit` | Electromagnetic connection ranking across all people |
| `chart_db_element_fit` | Who adds missing Five Elements + minimal completions |
| `chart_db_compat` | Profile line harmonic compatibility ranking |
| `chart_db_missing` | Missing centers in a composite group |
| `chart_auto_add` | Calculate charts + add/update person in YAML database |

---

## Secrets Masking

SSH and Kubernetes tool output is automatically passed through a regex-based sanitizer (`mcp/sanitize.py`) that redacts:

| Pattern | Example |
|---------|---------|
| AWS access keys | `AKIAIOSFODNN7...` → `AKIA***REDACTED***` |
| AWS secret keys | `aws_secret_access_key = wJal...` → `aws_secret_access_key = ***REDACTED***` |
| Bearer tokens | `Bearer eyJhbG...` → `Bearer ***REDACTED***` |
| API keys/secrets | `api_key=sk-123...` → `api_key=***REDACTED***` |
| Private key blocks | `-----BEGIN RSA PRIVATE KEY-----...` → `***PRIVATE KEY REDACTED***` |
| Passwords in URLs | `postgres://user:pass@host` → `postgres://user:***REDACTED***@host` |
| GitHub PATs | `ghp_ABCDEF...` → `ghp_***REDACTED***` |
| Slack tokens | `xoxb-123456...` → `xoxb-***REDACTED***` |
| K8s secret data | `password: dGhpc2lzY...` → `password: ***REDACTED***` |
| Generic secrets | `secret_key = ...`, `auth_token: ...` → redacted |

This prevents accidental credential leaks into the LLM context window.

---

## Architecture

```
roost/mcp/
├── server.py                  # FastMCP app + module registration
├── gmail_helpers.py           # Gmail API wrappers
├── ms_graph_helpers.py        # Microsoft Graph API helpers
├── normalize.py               # Gemini Flash Lite input normalizer
├── sanitize.py                # Secrets masking for SSH/K8s output
├── tools_tasks.py             # Task CRUD (4)
├── tools_tasks_extended.py    # Dependencies, subtasks, reorder, focus, shelve (22)
├── tools_activity.py          # Active task + activity logging (4)
├── tools_wellbeing.py         # Routines, spoons, shutdown/resume, streaks (13)
├── tools_contacts.py          # Contact lookup, identifiers, harvest, import (13)
├── tools_comms.py             # Communication timeline (5)
├── tools_projects.py          # Project CRUD (4)
├── tools_entities.py          # Entity queries (2)
├── tools_notes.py             # Notes CRUD (4)
├── tools_okr.py               # OKR cycles, objectives, key results (12)
├── tools_calendar.py          # Google Calendar read (2)
├── tools_calendar_write.py    # Google Calendar CRUD (5)
├── tools_gmail.py             # Gmail search/read/send/attachments/accounts (7)
├── tools_scheduled_emails.py  # Scheduled email send (3)
├── tools_drive.py             # Google Drive via rclone (4)
├── tools_slides.py            # PowerPoint + Google Slides (8)
├── tools_sheets.py            # Google Sheets (5)
├── tools_docs.py              # Google Docs (5)
├── tools_gemini.py            # Gemini AI pipeline (10)
├── tools_notion.py            # Notion pages/databases/blocks (16)
├── tools_docgen.py            # Document generation - pandoc (2)
├── tools_meeting_notes.py     # Meeting notes, presentations, transcription (3)
├── tools_time.py              # Time tracking timers (5)
├── tools_stats.py             # Productivity stats/analytics (4)
├── tools_ssh.py               # SSH/SCP remote management (7)
├── tools_docker.py            # Docker container management (6)
├── tools_k8s.py               # Kubernetes resources (5)
├── tools_cloudflare.py        # Cloudflare DNS management (6)
├── tools_improvmx.py          # ImprovMX email forwarding (7)
├── tools_namecheap.py         # Namecheap domain management (9)
├── tools_ms_email.py          # Microsoft Outlook email (4)
├── tools_ms_calendar.py       # Microsoft Calendar (7)
├── tools_ms_onedrive.py       # Microsoft OneDrive (4)
├── tools_ms_excel.py          # Microsoft Excel Online (3)
├── tools_ms_teams.py          # Microsoft Teams (16)
├── tools_ms_sharepoint.py     # Microsoft SharePoint (4)
├── tools_bundles.py           # Context bundles - assembled views (3)
├── tools_scrape.py            # Web scraping with Chrome automation (2)
├── tools_sessions.py          # Claude Code session management (4)
├── tools_telegram.py          # Telegram messaging (2)
└── tools_text_cleanup.py      # Text cleanup utilities (1)
```

### Service Layer

MCP tools are thin wrappers around service modules:

| Service | MCP Module | Purpose |
|---------|-----------|---------|
| `task_service.py` | `tools_tasks`, `tools_tasks_extended` | All task CRUD and extensions |
| `calendar_service.py` | `tools_calendar` | Calendar read (cached 15 min) |
| Gmail OAuth API | `tools_calendar_write` | Calendar CRUD (direct API) |
| `gmail/service.py` | `tools_gmail` | Email search/read/send |
| `slides_service.py` | `tools_slides` | PowerPoint + Google Slides |
| `sheets_service.py` | `tools_sheets` | Google Sheets |
| `docs_service.py` | `tools_docs` | Google Docs |
| `docgen_service.py` | `tools_docgen` | Pandoc conversion + rclone upload |
| `time_service.py` | `tools_time` | Timer start/stop, time entries |
| `stats_service.py` | `tools_stats` | Productivity analytics |
| `ssh_service.py` | `tools_ssh`, `tools_docker`, `tools_k8s` | Remote command execution |
| `notion/client.py` | `tools_notion` | Notion API via rate-limited client |
| `gemini_agent.py` | `tools_gemini` | Multi-LLM pipeline |

### Design Patterns

- **Deferred imports** — service modules imported inside function bodies (fast startup)
- **Source tracking** — all service calls use `source="mcp"` for event bus
- **Error dicts** — return `{"error": "..."}` instead of raising exceptions
- **Body truncation** — email bodies capped at 10K chars per message
- **Secrets masking** — SSH/K8s output sanitized before returning to LLM
- **Drive timeout** — rclone operations: 60s default, 300s for downloads/uploads, 120s for search

---

## OAuth Scopes

```
gmail.modify           — Read/write email, labels
gmail.settings.basic   — Manage filters
calendar               — Full calendar access (read + write)
presentations          — Read/write Google Slides
spreadsheets           — Read/write Google Sheets
documents              — Read/write Google Docs
```

Auth URL uses `include_granted_scopes=true` to accumulate scopes across re-auth. If new scopes aren't showing, revoke the token first (`POST https://oauth2.googleapis.com/revoke`) then re-authorize.

---

## MCP Config (~/.mcp.json)

```json
{
  "mcpServers": {
    "roost": {
      "command": "/home/dev/.local/bin/roost-mcp",
      "args": []
    }
  }
}
```

---

## Dependencies

- `fastmcp<3` (FastMCP 2.x)
- `python-pptx>=1.0.0` (PowerPoint manipulation)
- `pandoc` (system binary — document conversion)
- `rclone` (system binary — Google Drive)
- All existing roost dependencies (SQLite, Google APIs, notion-client, etc.)

---

## Adding a New Tool

1. Choose or create the right `tools_*.py` file
2. Import `mcp` from `roost.mcp.server`
3. Add `@mcp.tool()` decorated function with typed parameters and docstring
4. Use deferred imports inside the function body
5. Return plain dicts (not Pydantic models)
6. Wrap in try/except, return `{"error": str(e)}` on failure
7. If new module: add `from roost.mcp import tools_xxx` to `server.py`
8. Reinstall: `pip install --break-system-packages -e .`
9. Verify: `python3 -c "from roost.mcp.server import mcp; print(len(mcp._tool_manager._tools))"`

---

## Changelog

| Date | Tools | Change |
|------|:-----:|--------|
| 2026-02-09 | 21 | Initial release — tasks, contacts, projects, entities, calendar, Gmail, Drive |
| 2026-02-12 | 67 | Added SSH, Docker, K8s, Notion, Gemini |
| 2026-02-13 | 113 | Added tasks_extended, activity, wellbeing, comms, Slides, Sheets, Docs |
| 2026-02-13 | 130 | Added calendar CRUD, time tracking, stats, Notion archive/restore/create_database |
| 2026-02-13 | 133 | Added doc generation, Notion duplicate, secrets masking |
| 2026-02-23 | 210 | Added gemini_generate, gemini_image (Gemini + Imagen 4), create_project, update_project |
