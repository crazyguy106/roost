# Roost User Guide

*For team members with a Roost instance. Last updated: 2026-02-23.*

---

## 1. What is Roost?

Roost is your personal productivity platform running on a dedicated server. It brings together task management, email triage, calendar, contacts, and an AI assistant -- all in one place. You interact with it through three interfaces: a **Web Dashboard** you can access from any browser (and install as a phone app), a **Telegram Bot** for quick actions on the go, and **Claude Code**, an AI assistant that can do almost anything in the system on your behalf. Everything stays in sync -- a task you create in Telegram shows up on the web dashboard and is visible to Claude Code.

---

## 2. Your Three Interfaces

### Web Dashboard

Your web dashboard lives at your personal URL:

- **Ben:** https://user.example.com
- **user:** https://user.example.com

Log in with the username and password you were given. Once logged in, you'll see:

- **Dashboard** -- An overview of your tasks, what's due today, your focus items, streak counter, and energy budget
- **Tasks** -- Your full task list with filters (status, priority, project)
- **Projects** -- Projects grouped by company/entity, with progress tracking
- **Calendar** -- Your calendar events and task deadlines in a monthly view
- **Contacts** -- People you work with, grouped by organisation

**Install it on your phone.** The web dashboard works as a mobile app (PWA). On your phone:

1. Open your URL in Safari (iPhone) or Chrome (Android)
2. Tap "Share" then "Add to Home Screen" (iPhone) or tap the three-dot menu then "Install app" (Android)
3. It now appears as an app on your home screen with its own icon

The mobile version is touch-optimised -- you can swipe tasks to mark them done, pull down to refresh, and use the bottom navigation tabs to move between sections. You can also create, edit, and delete contacts, entities, projects, and notes directly from your phone -- all CRUD operations work on mobile.

### Telegram Bot

Your Telegram bot is the fastest way to interact with Roost. It is already set up and linked to your Telegram account. Just open the chat with your bot and start sending commands.

Here are the commands you'll use most often:

**Task basics:**

- `/tasks` -- See your task list (with filter buttons)
- `/add Buy groceries` -- Quick-add a task
- `/done 3` -- Mark task #3 as complete
- `/show 5` -- See details for task #5, with action buttons
- `/note Remember to call Sarah` -- Jot down a quick note

**Daily planning:**

- `/today` -- Your morning overview: what's overdue, due today, and in progress
- `/urgent` -- Your most urgent tasks, ranked by priority and deadline
- `/cal` -- Today's calendar events
- `/inbox` -- Triage your email (view, reply, archive, AI-draft replies)

**Working on tasks:**

- `/wip 3` -- Mark task #3 as "in progress" (what you're working on now)
- `/wip 3 finishing the slide deck` -- Same, but with a context note so you remember where you left off
- `/next` -- Show just one task: the single most important thing to do right now
- `/pick` -- Can't decide? This picks a random task for you, weighted by urgency

**Energy management:**

- `/spoons` -- Check your energy budget for the day
- `/spoons 10` -- Set today's energy budget to 10 spoons
- `/lowenergy` -- Show only light, low-effort tasks (for when energy is low)
- `/shutdown` -- Pause all work for the day (defers deadlines, pauses tasks)

### Claude Code (AI Assistant)

Claude Code is an AI assistant that has full access to your Roost. It can manage tasks, draft emails, look up contacts, check your calendar, and much more. Think of it as a very capable personal assistant that understands natural language.

**How to start a session:**

1. Go to your web dashboard and click **Sessions** in the navigation
2. Click **Create Session** -- give it a name like "Daily work"
3. Click **Connect** -- this opens a terminal in your browser
4. Type `claude` and press Enter
5. The first time, it will show a URL to authenticate with your Anthropic account. Copy-paste that URL into your browser, log in, and you're connected.

**Example prompts you can use:**

- "Show me my tasks for today"
- "Draft an email to John about rescheduling our meeting to Thursday"
- "What meetings do I have this week?"
- "Create a task to review the Q1 report, high priority, due Friday"
- "Search my inbox for emails from Sarah"
- "What's on my calendar tomorrow?"
- "Mark task 5 as done"
- "Give me a summary of my week"

Claude Code understands context, so you can have a natural conversation. It will always show you email drafts before sending and ask for confirmation on important actions.

---

## 3. Daily Workflow

Here's what a typical day looks like using Roost.

### Morning: Orient yourself

Start your day by getting the lay of the land:

1. **Check `/today`** in Telegram -- this shows you what's overdue, what's due today, and what you were working on yesterday. Or ask Claude Code: "Give me my morning briefing."

2. **Review your calendar** with `/cal` -- see what meetings and events are coming up today.

3. **Triage your inbox** with `/inbox` -- this walks you through your unread emails one at a time. For each email, you can:
   - View the full thread
   - Reply (type your own reply or use AI to draft one)
   - Archive it (marks as read)
   - Skip to the next one

4. **Set your energy level** -- if you're having a low-energy day, use `/spoons 8` to set a lower budget, or `/lowenergy` to only see light tasks.

5. **Pick your focus** -- use `/daily` to see suggested focus tasks, or `/next` to get just one thing to start with.

### During the day: Stay on track

- **Mark what you're working on** with `/wip 3 writing the proposal` -- this sets your current task and adds a context note.
- **Capture thoughts quickly** with `/note Call the client about the demo` -- jot it down before you forget.
- **Use Claude Code for complex tasks** -- "Draft a reply to the last email from Ben about the project timeline" or "Create three subtasks for task #7."
- **Check `/urgent`** if you lose track of what matters most.

### End of day: Wind down

- **Review what you did** -- ask Claude Code "What did I accomplish today?" or use `/done today` to see your activity log.
- **Shut down** with `/shutdown` -- this pauses all in-progress tasks and defers today's deadlines to tomorrow. No guilt about unfinished work.
- **Next morning,** use `/resumeday` to pick up where you left off.

---

## 4. Key Features

### Task Management

Tasks are the backbone of Roost. Each task has:

- **Title** -- what needs to be done
- **Status** -- `todo` (not started), `in_progress` (working on it), `done` (finished), or `blocked` (stuck on something)
- **Priority** -- `low`, `medium`, `high`, or `urgent`
- **Deadline** -- optional due date
- **Project** -- group tasks under a project
- **Effort** -- `light`, `moderate`, or `heavy` (used for energy budgeting)

**Create tasks** from anywhere:
- Telegram: `/add Review the contract -p high`
- Web: Click the + button on the tasks page
- Claude Code: "Create a task to prepare the presentation, high priority, due next Monday"

**Track progress** with statuses. Move tasks through `todo` -> `in_progress` -> `done`. Use `/wip` to mark something as in-progress, and `/done` to complete it.

**Organise with projects.** Tasks can belong to projects, and projects can belong to companies/entities. Use `/projects` to see everything grouped together.

**Break down big tasks.** If a task feels overwhelming, use `/break 5` in Telegram to break task #5 into smaller subtasks. The bot enters capture mode -- just type each subtask one per message, then `/done` when finished.

### Email Triage

Your Roost instance connects to your Microsoft 365 (Outlook) account. Once connected, you can manage email directly from Telegram or Claude Code.

**Interactive triage in Telegram:**

1. Type `/inbox` -- the bot shows your most recent unread emails
2. Tap an email to view it
3. Choose an action:
   - **Reply** -- type your own reply or tap "AI Draft" to have an AI-generated reply suggested
   - **Archive** -- marks the email as read
   - **Skip** -- move to the next email
4. Keep going until your inbox is clear

**Using Claude Code for email:**

- "Search my inbox for emails from John about the budget"
- "Draft a reply to the latest email from Sarah, thanking her for the proposal and confirming the meeting on Thursday"

Important: Claude Code will always show you the draft before sending. You must approve it first.

**Connect your Microsoft account** (one-time setup):

1. Log in to your web dashboard
2. Click the Microsoft sign-in link (or go to `/auth/microsoft`)
3. Sign in with your Microsoft 365 account and grant permissions
4. Done -- email, calendar, and OneDrive are now connected

### Calendar

Your calendar syncs from Microsoft 365 once connected.

- `/cal` -- Today's events with times and locations
- `/block Meeting with John 2pm-3pm` -- Create a new calendar event

In Claude Code, ask things like:
- "What meetings do I have this week?"
- "Create a meeting called 'Team sync' on Thursday at 10am for 30 minutes"
- "Move my 2pm meeting to 3pm"

### AI Assistant (Claude Code)

Claude Code is your most powerful interface with 210 tools. It can do everything the web and Telegram can do, plus:

- **Research topics** -- "Research the latest trends in AI governance and summarise the key points"
- **Draft documents** -- "Write a one-page summary of our project status"
- **Complex task management** -- "Move all high-priority tasks from Project A to Project B"
- **Prepare for meetings** -- "Prep me for my meeting with Sarah. Show her contact info, recent emails, and any tasks related to her"
- **Weekly reviews** -- "Give me a productivity summary for this week"

The AI understands context and can chain actions together. For example: "Check my calendar for tomorrow, then draft an email to the team summarising what's scheduled."

### Energy Management

Roost includes features designed for sustainable productivity, especially on days when energy is low.

**Spoon Budget** -- Based on "spoon theory," this gives you a daily energy budget. Each task costs spoons based on its effort level (light = 1, moderate = 2, heavy = 4). Set your budget with `/spoons 12` and the system tracks what you've spent. When spoons run low, it suggests lighter tasks.

**Low Energy Mode** -- Type `/lowenergy` to see only light tasks. Perfect for days when you can still be productive but need to take it easy.

**Shutdown Protocol** -- Type `/shutdown` when you need to stop for the day. It pauses everything in progress, defers today's deadlines to tomorrow, and suppresses notifications. No guilt, no loose ends. Resume the next day with `/resumeday`.

**Streaks** -- Roost tracks consecutive days where you complete at least one task. It's a gentle motivator -- you'll see your streak on the dashboard and get milestone messages at 3, 7, 14, 30, and 100 days.

**Daily Routines** -- Set up morning and evening checklists with `/routine morning` or `/routine evening`. Add items like "Check calendar" or "Set tomorrow's focus." Items reset each day so you always have a fresh checklist.

### Settings & Integrations

The settings page at `/settings` (or `/m/settings` on mobile) lets you manage your Roost instance:

**Integrations** -- Connect and test your AI providers, Telegram bot, Google Workspace, Microsoft 365, and Notion. Each integration shows its connection status. Click "Test" to verify a credential works before relying on it.

**Feature Flags** -- Toggle features on and off without restarting. Useful for disabling integrations you don't use (reduces the attack surface and simplifies the interface).

**Personality** -- Customise how your AI agent sounds. Write a short description like "Direct and concise, no emojis" or "Warm and encouraging, use bullet points." This text is injected into the agent's system prompt.

To access settings, click "Settings" in the sidebar (desktop) or tap the gear icon (mobile). Credential management requires admin access.

---

## 5. Tips and Tricks

### Top 10 Bot Commands

| Command | What it does |
|---------|-------------|
| `/today` | Morning overview: overdue tasks, due today, in progress |
| `/add TITLE` | Quick-add a task in one message |
| `/done ID` | Mark a task as complete |
| `/wip ID note` | Mark a task as in-progress with an optional context note |
| `/inbox` | Interactive email triage (view, reply, AI draft, archive) |
| `/cal` | Today's calendar events at a glance |
| `/next` | Show the single most important task right now |
| `/urgent` | Top tasks ranked by urgency score |
| `/note TEXT` | Capture a quick thought or reminder |
| `/spoons` | Check your energy budget for the day |

### Useful habits

- **Start every day with `/today`** -- it takes 30 seconds and sets you up for the day.
- **Use `/wip` when you start something** -- when you come back later, `/resume` shows you exactly what you were doing and any context notes you left.
- **Capture everything with `/note`** -- don't trust your memory. Quick notes can be turned into tasks later.
- **Use `/shutdown` guilt-free** -- it's designed for this. Bad days happen. The system defers your deadlines and pauses your work so nothing falls through the cracks.
- **Let Claude Code handle complex things** -- drafting emails, preparing for meetings, reorganising tasks. That's what it's there for.
- **Install the mobile app** -- having Roost on your phone home screen makes it much easier to check tasks and capture notes throughout the day.

### Getting help

- Type `/help` in Telegram to see all available commands
- Ask Claude Code "What can you do?" for a summary of capabilities
- If something isn't working, reach out to your_user -- he manages all the VPS instances and can troubleshoot remotely

---

*Roost is actively developed. New features are added regularly and pushed to your instance automatically. If you have ideas or feedback, let your_user know.*
