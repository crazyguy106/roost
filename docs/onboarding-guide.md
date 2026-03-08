# Remote Instance Onboarding Guide

Welcome to your personal Roost instance. This guide walks you through every step to get productive, from your first login to your daily workflow.

Created: 2026-02-21

---

## What You Get

Your VPS instance comes with a full personal productivity platform:

- **Web dashboard** at `https://yourname.example.com` -- tasks, calendar, contacts, projects, email triage, all in one place.
- **Telegram bot** -- a dedicated bot just for you. Manage tasks, triage email, check your calendar, and take notes from your phone.
- **Claude Code AI assistant** -- an AI assistant running on your VPS with access to all your tools. Ask it to draft emails, summarize meetings, manage tasks, and more.
- **Microsoft 365 integration** -- connects to your Outlook email, calendar, OneDrive, Teams, and SharePoint.
- **Meeting note capture** -- automatic ingestion of Otter.ai meeting transcripts (optional setup).

### What is NOT on remote instances

These features are only available on the dev VPS (your_user's instance) and will not work on yours:

- Google Workspace (Gmail, Google Drive, Slides, Sheets, Docs)
- Curriculum scanner and programme seeding
- Notion bidirectional sync

This is by design. Your instance uses Microsoft 365 as the primary integration for email, calendar, and files.

---

## Step 1: First Login (Web Dashboard)

1. Open your browser and go to `https://yourname.example.com` (replace `yourname` with your actual subdomain -- your_user will tell you what it is).
2. You will see a basic authentication prompt. Enter the **username** and **password** that your_user provided.
3. Once logged in, you will land on the dashboard. Take a moment to explore:
   - **Tasks** -- your personal task list with priorities, projects, and statuses.
   - **Calendar** -- your upcoming events (once Microsoft is connected).
   - **Contacts** -- people you work with, linked to projects and communications.
   - **Projects** -- organised by entity (company/organisation).

### Changing your password

Your web password is stored in the `.env` file on your VPS. To change it, ask your_user to update the `WEB_PASSWORD` value and restart the web service. There is no self-service password change in the web UI yet.

---

## Step 2: Connect Microsoft 365

This is the most important setup step. Once connected, your email, calendar, OneDrive, Teams, and SharePoint all become accessible through Roost.

### Steps

1. Make sure you are logged into the web dashboard first (Step 1 above).
2. Navigate to: `https://yourname.example.com/auth/microsoft`
3. You will be redirected to Microsoft's sign-in page. Sign in with your **Microsoft 365 account** (the one you use for Outlook, Teams, etc.).
4. Microsoft will show you a permissions consent screen. You are granting Roost access to:
   - Read your email
   - Send email on your behalf
   - Read and write your calendar
   - Access your OneDrive files
   - Read Teams messages and channels
   - Access SharePoint sites
5. Click **Accept**.
6. You will be redirected back to Roost with a success message: "Microsoft Graph authorized!"

### Verify it worked

- Go to the **Calendar** page on your web dashboard. You should see your Outlook calendar events.
- If you see "Calendar: not connected", the OAuth flow may not have completed. Try visiting `/auth/microsoft` again.

### Troubleshooting

- **"Something went wrong" during sign-in** -- Make sure you are signed into the web dashboard first. The Microsoft auth page requires you to be logged in.
- **Missing permissions** -- Some permissions (especially SharePoint and Teams channel messages) may require admin consent from your IT department. If you see 403 errors for specific features, ask your Microsoft 365 admin to grant consent for the Roost app.
- **Token expired** -- If Microsoft features stop working after a long period, re-visit `https://yourname.example.com/auth/microsoft` to re-authorize.

---

## Step 3: Connect Telegram Bot

Your Telegram bot is a personal assistant that lives in your pocket. It is already created and running on your VPS -- you just need to connect to it.

### Steps

1. your_user will give you your bot's username (e.g., `@yourname_roost_bot`).
2. Open **Telegram** on your phone or desktop.
3. Search for the bot by its username and open the chat.
4. Send `/start` to the bot.
5. The bot will respond with a welcome message if your Telegram user ID is in the allowed list.

### If the bot does not respond

Your Telegram user ID must be registered in the bot's allowed users list. If your_user has not set this up yet:

1. Send `/start` or `/help` to the bot anyway.
2. Tell your_user you tried -- he can check the bot logs to find your Telegram user ID and add it to the configuration.
3. Once added, the bot will need a quick restart (your_user handles this).

### Test commands

Once the bot is responding, try these:

| Command | What it does |
|---------|-------------|
| `/today` | Morning briefing -- today's calendar events + tasks |
| `/inbox` | Interactive email triage for your Outlook inbox |
| `/tasks` | List your active tasks |
| `/help` | Full list of all available commands |

---

## Step 4: Set Up Claude Code (Optional -- Power Users)

Claude Code is an AI coding assistant that runs directly on your VPS. It has access to all Roost tools and can help you manage tasks, draft emails, search files, and more.

### Browser-based access (recommended)

No SSH required. This is the easiest way to use Claude Code:

1. Go to `https://yourname.example.com/sessions` (log in with your web credentials).
2. Click **Create Session** -- give it a name and set the project directory (default: `/home/dev/projects/roost`).
3. Click **Connect** -- this opens a web terminal in your browser.
4. In the terminal, type `claude` and press Enter.
5. Claude Code will print a URL. Copy it and paste it into your browser to authenticate with your **Anthropic account**.
6. Once authenticated, you are in. Ask Claude anything.

### SSH access (alternative)

If you prefer a terminal:

1. SSH into your VPS: `ssh dev@yourname.example.com` (ask your_user for credentials).
2. Attach to the existing tmux session: `tmux attach -t ai-claude`
3. Type `claude` to start Claude Code.

### Things to try

- "What tasks do I have?"
- "Show me my calendar for today"
- "Search my email for messages from [person]"
- "Create a task to follow up with [person] about [topic]"
- "What's on my OneDrive?"

---

## Step 5: Set Up Meeting Capture (Optional)

If you use [Otter.ai](https://otter.ai) for meeting transcription, you can have meeting notes automatically flow into Roost.

### Option A: Zapier free tier (recommended)

1. Create a free [Zapier](https://zapier.com) account.
2. Set up a Zap: **Trigger** = new email from Otter.ai containing your transcript. **Action** = save to a Dropbox folder (or forward to a webhook).
3. Ask your_user to configure the Dropbox integration on your VPS so transcripts are picked up automatically.

### Option B: Webhook (requires Zapier Premium or custom setup)

1. Your VPS has a webhook endpoint at: `https://yourname.example.com/api/otter/ingest?token=YOUR_SECRET`
2. Ask your_user to configure the `OTTER_WEBHOOK_SECRET` in your `.env` file.
3. Set up Zapier (or another automation tool) to POST Otter transcripts to that URL.

Once configured, meeting notes appear automatically as notes tagged with "meeting" in your task system.

---

## Daily Workflow Quick Start

Here is a suggested daily workflow using Telegram commands. All of these also work through the web dashboard.

### Morning

| Command | Purpose |
|---------|---------|
| `/today` | See your calendar, overdue tasks, and what is in progress |
| `/inbox` | Triage your Outlook inbox -- browse, read, reply, archive |
| `/spoons` | Check your energy budget for the day (optional) |
| `/routine` | Go through your morning routine checklist (optional) |

### During the day

| Command | Purpose |
|---------|---------|
| `/tasks` | See all active tasks with filter buttons |
| `/add Buy coffee for the office` | Quick-add a new task |
| `/wip 42` | Mark task #42 as in-progress (what you are working on now) |
| `/note Met with Sarah, agreed on Q3 timeline` | Jot a quick note |
| `/cal` | Check today's calendar events |
| `/block` | Create a new calendar event (blocks time) |

### Wrapping up

| Command | Purpose |
|---------|---------|
| `/done 42` | Mark task #42 as complete |
| `/shutdown` | End-of-day ritual -- pauses all in-progress work |

### Resuming next day

| Command | Purpose |
|---------|---------|
| `/resumeday` | Pick up where you left off (restores paused tasks) |
| `/resume` | Show what you were working on |

---

## Troubleshooting

### Bot not responding

- The bot service may need a restart. Contact your_user.
- Check that your Telegram user ID is in the allowed list (ask your_user).

### Microsoft not connected / Calendar empty

- Visit `https://yourname.example.com/auth/microsoft` to re-authorize.
- Make sure you are logged into the web dashboard first (basic auth is required before the Microsoft OAuth page will load).
- If you recently changed your Microsoft password, you will need to re-authorize.

### "Not enabled" messages for Google features

This is expected. Remote instances do not have Google Workspace integration. Use the Microsoft 365 equivalents:
- Instead of Gmail: `/inbox` uses Outlook
- Instead of Google Calendar: `/cal` uses Microsoft Calendar
- Instead of Google Drive: use OneDrive via the web dashboard or Claude Code

### Cannot SSH into the VPS

- Ask your_user for your SSH credentials and the server IP address.
- SSH access uses key-based authentication. your_user will need to add your public key.

### Web dashboard not loading

- Try clearing your browser cache or using an incognito window.
- The URL must use `https://` (not `http://`).
- If the site is down, contact your_user -- the web service may need a restart.

---

## Getting Help

| Channel | When to use |
|---------|-------------|
| `/help` in Telegram | See all available bot commands and what they do |
| Ask Claude Code | "How do I..." questions about Roost features |
| Ask your_user | System administration, access issues, new features, bugs |

### Useful Telegram commands reference

| Category | Commands |
|----------|----------|
| **Tasks** | `/tasks`, `/add`, `/done`, `/show`, `/wip`, `/note`, `/notes` |
| **Triage** | `/today`, `/urgent`, `/resume`, `/daily`, `/lowenergy` |
| **Email** | `/inbox` (interactive Outlook triage) |
| **Calendar** | `/cal` (today's events), `/block` (create event) |
| **Wellbeing** | `/spoons`, `/routine`, `/shutdown`, `/resumeday` |
| **AI** | `/gem` (Gemini chat), `/doc` (document writing) |
| **Info** | `/help`, `/projects`, `/contacts`, `/entities` |

---

*This guide covers the essentials. As you use Roost, you will discover more features. The system is designed to adapt to how you work -- use what helps, ignore what does not.*
