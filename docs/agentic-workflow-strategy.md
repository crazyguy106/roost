# Roost Agentic Workflow — Product Strategy Memo

> **Status:** Strategy memo, not implementation spec
> **Author:** Ethan Seow (with Claude Code assistance)
> **Last updated:** 2026-05-20
> **Origin:** Kingston all-staff AI intro session, May 2026 — demoed Claude Code patterns as the closest available proxy for agentic workflow, because Roost in its current form doesn't yet show those patterns at the surface
> **Audience for this memo:** Ethan first; Roost contributors second; potentially Ben / Teik Guan / collaborators as we scope build effort
> **Decision sought:** Direction (lightweight vs. full Workspace primitive), surface lead (Web vs. CLI), and rough build sequence

---

## 1. Why this memo exists

In the May 2026 Kingston all-staff AI intro, the demo segments that landed hardest were the ones where AI **worked agentically alongside the user** in a visible, iterative loop — read a file, proposed a plan, executed it, then waited for redirection. Those segments were demonstrated using **Claude Code**, not Roost.

That's a problem worth fixing on purpose. The current arrangement says, in effect, *"Roost gives Claude Code a persistent workspace"* — Roost is the backend, Claude is the visible agent. That framing is true but strategically thin. It positions Roost as the plumbing under someone else's brand. Every training session, every demo, every conversation with a prospect routes the *attention* and the *emotional response* through Claude. Roost gets credit only when people read the architecture diagram afterwards.

What the Kingston session showed me — and what this memo argues — is that the **agentic surface itself** is now the differentiated product. The integrations are commoditising. Tool count is commoditising. What is not commoditising is *the way humans and AI work together in the loop*: how plans are shown, how approval gates appear, how files are touched, how interruptions land, how trust is built incrementally.

If Roost owns its own agentic surface, three things change:

1. **Demo-able by Ethan, brand-distinct.** The next Kingston session, the next insurance-agent session, the next SME pitch — Roost is the visible agent. Claude (or Gemini, or Codex) is the engine underneath, but the experience is Roost.
2. **Verticalisable.** The same agentic surface, three skins. Admin staff see "draft a parent email." Insurance agents see "run the AIA sweep." SME owners see "reconcile this week's Stripe payouts." Same engine, same loop, different bundle.
3. **Defendable as a category.** "Personal AI agent platform" is too generic to defend. "**The agentic surface for non-IT professionals**" is specific enough to position against, and aligned with C4AIL's Translator-capability pedagogy (Roost makes the substrate visible without making the user a developer).

The strategic intent of this memo is: **lay out what Roost's agentic workflow should be, what we already have toward it, what's missing, and what to build in what order** — without locking the team into UI specifics.

---

## 2. What "agentic workflow" actually means

Agentic workflow is a name for a *style of interaction*, not a feature toggle. The defining characteristic is that the human and the AI are working on the same thing at the same time, with a tight feedback loop, and the AI's intent is visible *before* its action.

For this memo I'm scoping agentic workflow to **three behaviour patterns** — the ones Ethan demoed at Kingston and identified as core. A fourth pattern (conversational mid-task redirect) is deliberately out of scope for now; see §9.

### Pattern 1 — Plan-then-execute with approval gate

The AI states what it's about to do, in plain language, **before doing it**. The human can approve, modify, or veto. Once approved, execution proceeds — but at each material step there's a chance to interrupt.

This is not the same as Roost's current Guardian gate. Guardian is a rules-engine that runs *at the moment of tool invocation* and either lets the tool through, warns, or blocks. It's correct and load-bearing, but it's an *exception path* — you only see it when something's wrong. In Plan-then-execute, the plan is the *default surface*, not the exception.

Operational shape: when the user makes a non-trivial request, the AI replies with a numbered plan (files to touch, tools to call, side effects expected, things it's uncertain about), and waits. The user types "go", "edit step 3", "skip step 5", or "stop." Only after explicit approval does the AI begin.

Why it matters: **this is intellectual/accountability labour made structural**. The AI does the intellectual labour of forming the plan; the human does the accountability labour of approving it. The handoff is visible, the audit trail is automatic, and the user learns the AI's reasoning shape over time.

### Pattern 2 — Live file read / edit / write

The AI works *on actual files* — opens them, shows their contents, edits in place, displays a diff before applying, persists the change. The user watches it happen and can intervene.

This is the pattern Roost is furthest from. Today Roost has rich service integrations (Drive, OneDrive, Gmail) and rich state in SQLite — but it does not have the *file workspace* concept that Claude Code has. There is no "current project directory" that both human and AI can see, scroll, edit, diff.

Closing this gap is the most consequential design decision in this memo. Two interpretations:
- **Lightweight:** stay service-based. "File ops" means uploading to Drive, downloading from Drive, editing via Google Docs API, showing the diff in the chat surface. No local-file workspace concept added.
- **Full:** introduce a **Workspace** primitive — a persistent named scope (folder, virtual or real) where files live, tools operate, and state accumulates. The user has a "this is what we're working on right now" container. Closer to Claude Code's project model.

§4 picks between these.

### Pattern 3 — Tool chaining in real time

The AI uses multiple tools in sequence to accomplish a task, and the user **sees each tool call as it happens** — not as a final report after the fact. "I'm calling `drive_search` to find the policy doc … found 3 candidates, choosing the most recent … now calling `gdocs_read_content` … now drafting the summary."

Roost has the tools (270+ MCP tools). What it lacks is the **streaming display surface**. Today the Web UI is more transactional ("submit a request, see a response"); Telegram is async by design; the MCP server is invisible to the user (it's only visible to whichever AI is consuming it).

This pattern is mostly UX and streaming infrastructure, not new capability.

### What agentic workflow is NOT (in this memo's scope)

- Not a fully autonomous mode — that's covered by Roost's existing autonomy levels (supervised/assisted/autonomous).
- Not multi-agent orchestration — that's a different design question.
- Not voice or multi-modal — out of scope for now.
- Not chat-as-everything — the existing Roost web surfaces (`/sme/sync-status`, `/rpa` viewer, kanban board) stay; agentic workflow is an *added* lane, not a replacement.

---

## 3. Capability audit — what Roost already has

The good news is that Roost has accumulated most of the *substrate* for agentic workflow. The gap is at the *surface*. Working through it:

| Capability needed for agentic workflow | Roost has? | Notes |
|---|---|---|
| Wide tool inventory the AI can compose from | ✅ 270+ MCP tools across core + 6 bundles | This is the biggest single asset. Claude Code calls Roost tools every day from Ethan's session. |
| Pre-flight safety check | ✅ Guardian (block / warn / allow) | Today this is a rules-engine on invocation. For agentic workflow we need it surfaced as part of the plan, not just at execution. |
| Reversibility of writes | ✅ Checkpoints + `/rollback` | One-click undo for write actions. Excellent fit for agentic workflow; needs surface-level "undo last 3 steps" affordance. |
| Money-moving approval queue | ✅ Guardian draft queue | `/sme/sync-status` shows pending. Pattern is already there for "AI proposed, human approves, then commit." Generalise this from "money-moving" to "any non-trivial write" in agentic workflow. |
| Cross-channel context | ✅ 7-day cross-channel memory | Context persists across Telegram / Web / MCP. Good. |
| Reusable patterns / skills | ✅ Self-improving Skills (extracts patterns from successful runs) | Pattern is right; needs to be surfaced *during* agentic workflow ("I'm using the skill `aia-portal-sweep` for this — want to see it first?") |
| Cost limits + budgets | ✅ Per-run and daily token cost limits | Already enforced. Surface to the user inline so they see "this plan will cost ~3¢ in tokens." |
| Autonomy levels | ✅ supervised / assisted / autonomous | Maps onto agentic workflows naturally: supervised = explicit approval per step; assisted = approval per plan; autonomous = approval per session. |
| Event triggers / recipes | ✅ SOP triggers | Important for "this session became a recipe" — promotes one-off work to repeatable. |
| Background sub-agents | ✅ MAX_BACKGROUND_RUNS + timeout | Useful for "go research X in the background while we work on Y" patterns. Surface needs to show what's running. |
| **Visible plan surface** | ❌ Not at the surface | The Guardian gate is the closest analogue, but it's exception-not-default. **New surface needed.** |
| **Workspace primitive** | ❌ Not present | Roost has services and state, no notion of "files-and-state-scoped-to-this-task." **Design decision in §4.** |
| **Streaming tool execution display** | ❌ Web is transactional | Tool calls happen invisibly. **New surface needed.** |
| **In-conversation diff display** | ❌ Not present | When a file is changed, the chat doesn't show before/after. **New surface needed.** |
| **First-class file ops in the chat** | Partial — via service tools | You can ask Roost to upload/download/edit via tools, but the *file* isn't the unit of interaction; the *tool call* is. |
| **Conversation-level memory of "the current thing"** | Partial — cross-channel memory | 7-day persistence is right; what's missing is a "current task" scope that bundles files + tool history + plan + decisions. |

**Honest read:** Roost has roughly **70-80% of the substrate** for a credible agentic workflow. The missing 20-30% is concentrated in **surface design and streaming UX**, not in capability. This is good news — the build effort is bounded, and most of the risk is in design choices, not engineering work.

It also means we should resist the temptation to add more capabilities. The bottleneck is **how visible and composable the existing capabilities are**, not whether more exist.

---

## 4. Two scope options, ranked

### Option A — Lightweight: chat-surface agentic loop, no Workspace primitive

**What it is:** A new dedicated chat surface (web route `/agentic` or similar) where the user converses with the Roost agent, sees streaming tool calls, sees plan-before-execute, sees diffs inline, and can approve / interrupt. Files live where they already live (Drive, OneDrive, the user's local filesystem accessed via service tools). No new "workspace folder" concept is introduced.

**What it buys:**
- Earliest demoable surface. Probably 4-6 weeks of focused work.
- Reuses existing services and tools entirely.
- Verticalisable from day 1 — same chat surface, different bundle context loaded.
- Low risk to existing Roost flows (it's additive).
- The Kingston demo problem solved by the next major session.

**What it lacks:**
- No persistent "we're working on X" container. Each conversation is its own thread; cross-conversation continuity relies on the existing memory layer (which is by user, not by task).
- Files aren't first-class. They're things the AI fetches and uploads via tools. The metaphor stays "service-mediated," not "workspace-native."
- Less differentiated from existing chat-with-AI products. The win is in the streaming + plan + tools, but the *file metaphor* doesn't pop.

### Option B — Full: introduce a Workspace primitive

**What it is:** Add a new abstraction — **Workspace** — to Roost. A Workspace is a named, persistent scope that bundles together: a set of files (could be local-mounted, could be Drive-backed), a conversation history, a tool-call log, a plan history, a state pocket. The user creates a Workspace ("Kingston-attendance-policy-update") and works in it across sessions. The AI knows what's in scope.

**What it buys:**
- The right metaphor. Matches how Ethan thinks ("I'm working on the TM-QSN second submission" → that's a workspace). Matches how Claude Code feels (project directory + memory).
- Cross-conversation continuity is native, not bolted on.
- Verticalisable in a richer way — admin workspaces vs. agent workspaces vs. SME workspaces could have *different shapes*, not just different bundles loaded.
- A nameable, demoable object. "Open the workspace, see what we did last time, pick up where we left off."

**What it costs:**
- Substantial design work. What is a Workspace, structurally? Is it a directory? A virtual scope? A database record with attached files? How do existing services (Drive, Gmail, RPA flows) target a Workspace? Does it have permissions?
- Migration risk for existing Roost users who've built habits around the current flat structure.
- Longer build. Probably 12-16 weeks once design is locked.
- New thing to maintain. New thing to teach in onboarding.

### Recommendation

**Ship Option A first. Earn Option B.**

The lightweight chat-surface agentic loop is shippable in a quarter. It solves the Kingston demo problem (and the equivalent next-session problems for insurance / SME). It gathers real usage data on what people actually do with the surface.

Then, *informed by what we see*, build the Workspace primitive as a Phase 2. By that point we'll know whether the "we're working on X" container is actually the right shape, or whether something simpler (named conversations? pinned threads?) does the job.

This is the same pattern Roost has used successfully before: ship the substrate, observe usage, design the surface from evidence rather than guess. The risk of inverting it — building the Workspace primitive first — is that we lock in a shape we then have to undo.

> **Where I'm willing to be talked out of this:** if you believe the Workspace primitive is the *demo differentiator* (i.e., people don't get the pitch without it), then we should bite the bullet and design it first. Lightweight chat-with-streaming is easier to ship but also easier to dismiss as "yet another ChatGPT-style window." I lean toward A; flag if you see it the other way.

---

## 5. Design principles for agentic workflow

These are the opinions that should shape every decision downstream. State them, defend them, hold them.

### 5.1 The plan appears *before* the action, not as a summary after

This is the single most important principle and the one most likely to get compromised under build pressure. Showing the plan first means the AI must structure its own intent into legible steps *before* it has run anything. That's slightly slower, slightly more constrained, and absolutely the whole point. Without this, "agentic workflow" is just "AI does stuff, hopefully you catch the bad ones."

Operationally: when the user submits a non-trivial request, the agent **must** emit a structured plan (numbered steps, tools-to-call, files-to-touch, side-effects-expected, uncertainties) and **must** wait for approval before invoking the first tool that touches user-visible state.

What counts as "non-trivial" is configurable via autonomy levels. Supervised: every multi-step request gets a plan. Assisted: only requests that touch external state get a plan. Autonomous: only money-moving / irreversible requests get a plan.

### 5.2 Tool calls stream, not batch

Every tool invocation is shown to the user as it happens, in the chat surface, with arguments and (truncated) results. This is not optional. It's what makes the surface *feel* like agentic workflow rather than "submit-and-wait."

Operationally: the chat surface is an event stream. Tool calls emit events. The frontend renders them inline. There's a small affordance to collapse / expand the streaming detail so power users can see everything and casual users can see summaries.

### 5.3 Approval gates are visible, not invisible

When the agent needs approval (per autonomy level, per Guardian rule, per draft-queue policy), the gate is **the chat's next message** — not a notification, not a popup, not an email. The user sees "I need approval to do X before continuing" inline, with the existing context above it. They reply yes / no / modify in the same surface.

This collapses the current "draft queue at `/sme/sync-status`" pattern into the agentic flow for in-session approvals, while keeping the draft queue as the persistent surface for out-of-session approvals (Telegram replies, async).

### 5.4 Intellectual labour visible; accountability labour gated

This is the C4AIL pedagogy translated into product behaviour. The AI does intellectual labour (research, drafting, computing, summarising) and **shows its working** — what it read, what it concluded, why. The human does accountability labour (approving, signing, deciding) and the system **records the decision** — who approved, when, on what version of the plan, with what visible context.

Operationally: every approval recorded with timestamp + user + plan version + chat context. Every decision survivable in the sense that if asked "why did you do that?" the user can pull up the exact moment they said yes.

### 5.5 Brand-distinct from Claude

We are not building a Claude Code clone. We are building Roost's agentic surface. Two specific anti-patterns to avoid:

- **Don't copy Claude's command palette.** Roost has its own conventions (Telegram slash commands, MCP tool names) — agentic workflow should extend those, not parallel Claude's `/plan`, `/explain`, etc.
- **Don't lift Claude's voice or visual style.** No "I'll do that for you" assistant-y phrasings. Roost's voice should be neutral-operational, like a competent ops partner, not a friendly assistant.

The pitch should never be "it's like Claude Code." The pitch is "it's the agentic surface for your work, with your data, in your verticals, on your VPS."

### 5.6 The surface should fail visibly, not silently

When a tool fails, when a plan can't be completed, when an external service is down — the surface says so, in plain language, *in the chat*. No silent retries that look like working. No errors hidden in logs. The user is the loop, and the loop must close.

Operationally: every tool exception is rendered as a chat event ("Drive returned 503 — retrying once … retried, succeeded" or "Drive returned 503 twice — stopping. Want me to keep trying?"). Errors are not failures to communicate; they're communication events.

### 5.7 Stay under three vocabulary terms for the user

The user should never have to know more than three Roost-specific terms to use agentic workflow. Beyond that, we're teaching software, not running agentic workflow.

Provisional three: **Plan** (what I'm about to do), **Approve** (yes go), **Rollback** (undo the last thing). Everything else is implementation detail and can be invisible until needed.

---

## 6. Surface design — per interface

Roost has four interfaces. They are not all suited to agentic workflow. Honest assessment:

### Web — lead interface for agentic workflow

**Why lead:** Streaming UX, rich diff display, multi-pane layout (chat + file panel + plan panel), keyboard-driven for power users. This is where Plan-Approve-Execute, Live file ops, and Tool chaining all land naturally.

**What changes:** New route, probably `/agentic` or `/work`. New event-stream backend (WebSocket or Server-Sent Events) feeding the chat. Possibly a Vue / lit / minimal-React component for the streaming display — but lean toward extending the existing Jinja+HTMX/SSE pattern Roost uses elsewhere if possible (don't introduce a frontend framework unless the cost is justified).

**Existing surfaces it doesn't replace:** `/sme/sync-status` (draft queue), `/rpa` viewer (run inspector), kanban board (`/tasks/board`), settings. Agentic workflow is *added*, not in place of.

### CLI — power-user surface

**Why included:** Some users (Ethan, Ben, technical SME owners) will want agentic workflow in a terminal. The CLI is also the natural surface for "run a recipe in agentic workflow" or "open the workspace from the command line."

**What changes:** A `roost agentic` command that opens an interactive REPL. Same event stream as web, rendered to terminal. Plan + approval + tool calls in ANSI-formatted output. Likely Phase 2 — Web first, CLI follows.

### Telegram — defer

**Why defer:** Telegram is inherently async and narrow. Streaming tool calls don't render well; multi-pane plan + approval doesn't fit. Telegram's existing role — daily summaries, mobile triage, approval-via-reply for the draft queue — is right for what it is. Don't shoehorn agentic workflow in.

**What stays:** Telegram remains the *mobile approval* channel for in-session gates that need user approval when they're not at their laptop. Example: user starts an agentic session on web, leaves the laptop, the agent hits an approval gate, Telegram pings with "Approve step 4? `/yes` / `/no` / `/show`."

### MCP — unchanged, but underpins everything

**Why:** The MCP server doesn't have a user-facing surface; it provides tools to whichever AI is consuming Roost. Agentic workflow uses these tools internally — there's no change to MCP itself.

**What might change:** If we want to expose agentic-workflow-aware behaviour to *other* MCP consumers (i.e., let Claude Code know "this tool wants a plan before it runs"), that's an extension to the MCP tool descriptors. Not urgent — Phase 2 or later.

### Surface lead: Web is primary. CLI is secondary. Telegram is async-approval-only. MCP is unchanged.

---

## 7. Vertical mapping — same engine, three skins

The reason "All three audiences — generic agentic surface" is the right answer is that Roost's existing architecture (core + bundle pattern) already separates concerns this way. Agentic workflow plugs in at the core; bundles configure what's available in each vertical.

What changes per vertical is **defaults + bundle visibility + demo flows**, not the engine.

### Admin staff (Kingston-style)

- **Workspace defaults:** integrate Drive / OneDrive folders, point at a few common doc templates, no broker portals visible.
- **Default tools loaded:** Gmail, Drive, Docs, Sheets, Slides, Gemini summarisation, Gemini research.
- **Demo flow (next Kingston session):**
  1. User asks: "Draft the parent update email for Mei Ling's three late arrivals this week."
  2. Agentic workflow plan appears: "I'll (a) check our parent-comms template in Drive, (b) draft three variants in different tones, (c) show them inline, (d) wait for your pick. ~20 seconds, no external sends."
  3. User: "go."
  4. Tool calls stream: `drive_search` → `gdocs_read_content` → `gemini_generate` ×3 → output.
  5. User picks variant 2, edits one line, clicks "Send via Gmail."
  6. Guardian gate appears inline: "Sending to mr.tan@example.com — confirm?" User confirms. Sent.
- **Why this works:** Maps directly onto the survey's top pain points (drafting, email follow-ups, info retrieval). Each step is visibly assisted but the human signs every output.

### Insurance / property agents (existing Roost vertical)

- **Workspace defaults:** broker portal access, Telegram-OTP bridge enabled, CDD/DNC tools available, agency intranet bookmarked.
- **Default tools loaded:** RPA flows for AIA / Great Eastern / Prudential / Singlife, AI CDR for inbox, IRAS calc, PDPC DNC, CDD screening, Gmail/Drive.
- **Demo flow (next agent session):**
  1. User: "Daily client follow-up sweep — pull the AIA statements that need replying, prioritise by deadline."
  2. Plan: "(a) run AIA portal sweep RPA flow (you'll need to do the OTP, I'll wait), (b) parse the downloaded statements via AI CDR, (c) cross-reference with the contacts CRM for deadline urgency, (d) draft replies for top 3, (e) leave the rest as tasks. Estimated 4 minutes including OTP."
  3. User: "go."
  4. RPA browser opens in sidecar, user does OTP, returns. AI CDR triages. Drafts appear.
  5. User reviews drafts inline. Approves two, edits one, defers two more to tasks.
- **Why this works:** Hits the M4 (AIA portal sweep) + M5 (AI CDR inbox) curriculum modules directly. The demo is *the course outcome*, not a contrived example.

### SME owners / operators

- **Workspace defaults:** Stripe / Shopify / Xero connected (via existing SME Ops bundle), Zapier bridge active, daily summary configured.
- **Default tools loaded:** Stripe, Shopify, Xero, Zapier, lead pipeline, CRM, Gmail/Outlook, daily summary.
- **Demo flow:**
  1. User: "Weekly cash-flow review — pull this week's Stripe payouts, this week's Shopify orders, this week's Xero invoices. Flag anything unusual."
  2. Plan: "(a) Stripe `list_payouts` last 7 days, (b) Shopify `list_orders` last 7 days, (c) Xero `list_invoices` last 7 days, (d) Gemini compare to last 4 weeks' baseline, (e) summary report. No writes. ~30 seconds."
  3. User: "go."
  4. Tool calls stream. Three data pulls in parallel. Comparison runs. Report drafted.
  5. Output: "Stripe payouts up 12% on baseline. Shopify orders normal. Xero has 3 invoices over 30 days — want me to draft chase emails?" User: "yes, draft chases." → enters a new agentic loop for the chase emails. Guardian queues each chase as a money-affecting (well, money-asking) draft.
- **Why this works:** SME owners' pain is operational visibility. Agentic workflow gives them one place to ask "how are we doing?" with the data accountability built in.

### Cross-vertical observation

Notice the common shape: **user asks a verb-shaped question → plan appears → user approves → tools stream → output → optional next loop**. The engine is identical. The bundles configure which tools are loaded, which templates are defaults, which demo seeds exist. The user experience reads as native-to-their-domain because the *tools and language* are native-to-their-domain; the *interaction shape* is universal.

This is the substrate/surface insight at the product level: **the substrate is the loop; the surface is the bundle**.

---

## 8. Build sequence

This is order-of-operations for Option A (lightweight). Each phase ends with something demoable.

### Phase 1 — Plumbing (3-4 weeks)

- Event-stream backend on the web surface (WebSocket or SSE — pick whichever fits Roost's existing async patterns)
- Streaming tool-call event emitter — every MCP tool invocation emits `tool_called` / `tool_returned` / `tool_failed` events
- Chat surface shell at `/agentic` — basic message stream, no plan or approval yet
- Plan-generator agent layer — wraps the existing MCP-consuming agent so it emits a structured plan before executing
- Decision: which AI engine drives the agent layer? (Gemini default, Claude / Codex configurable — matches existing Roost engine model)

**Demo at end of Phase 1:** "Look at this — Roost can now show you what it's about to do, then do it, with the tools visibly running." Internal demo only; not yet differentiated enough for public.

> **Implementation spec:** [`agentic-workflow-phase1.md`](agentic-workflow-phase1.md) — build-ready brief covering event protocol, planner contract, touchpoint file list, and acceptance tests. Hand this to a Claude agent in `roost/` to drive the build.

### Phase 2 — Approval + diffs (3-4 weeks)

- Inline approval gates — the chat renders "approve / modify / cancel" UI when the plan requests it or Guardian flags it
- Diff display for file edits — when a tool returns a modified file, the chat shows the diff inline
- Cost preview — plan includes estimated cost in tokens / dollars before approval
- Approval audit trail — every approval recorded with full context for later inspection
- Autonomy-level integration — supervised vs. assisted vs. autonomous controls plan/approval granularity

**Demo at end of Phase 2:** This is the first publicly demoable version. The Kingston-style demo runs. The insurance-agent demo runs. The SME demo runs. Different bundles, same surface.

### Phase 3 — Vertical bundle integration (2-3 weeks)

- Per-bundle defaults — what tools are loaded, what templates are seeded, what example workspaces exist
- Bundle-specific demo flows — pre-canned "show me what this looks like for an admin / agent / SME" demo seeds
- Bundle-specific language tweaks — the surface uses bundle-appropriate vocabulary (e.g., "client" for property agent, "student" for admin, "customer" for SME)

**Demo at end of Phase 3:** Roost agentic workflow is now visibly verticalised. Onboarding loads the right bundle based on user profile.

### Phase 4 — CLI agentic workflow + Telegram approval bridge (2-3 weeks)

- `roost agentic` CLI command — terminal REPL with same event stream
- Telegram approval bridge — in-session approval gates can ping Telegram, user can `/yes` `/no` `/show` from mobile

**Demo at end of Phase 4:** Agentic workflow is multi-surface. Power users can drive from the CLI. Mobile users can approve gates remotely.

### Phase 5+ — Workspace primitive (Option B), if earned (12-16 weeks)

Triggered only if Phase 2-4 usage shows clear demand for a "we're working on X across sessions" container. Design from observed usage patterns.

### Effort totals (rough)

- **Phase 1-4 (Option A complete):** ~10-14 weeks of focused work
- **Phase 5 (Option B add-on):** ~12-16 weeks
- **Total to "full agentic platform":** ~6-8 months from a standing start

This is a realistic single-engineer pace; faster with 2-3 contributors if they can be onboarded.

---

## 9. What we deliberately skip (and why)

### Conversational redirect mid-task

The Kingston session deliberately didn't lean on this pattern, and this memo follows suit. Why skipped:

- **Design difficulty:** mid-task redirect requires the agent to gracefully roll back partial work, re-plan from a new context, and not lose the user's intent across the pivot. That's *agent-design* hard, not UX hard.
- **Quality risk:** done poorly, it feels like the agent "lost the plot" — worse than no support for it at all.
- **Lower demo value:** for a single-session demo, Plan-Approve-Execute already does the work. Mid-task redirect is a longer-arc feature.

**When to revisit:** after Phase 2 ships and we have real usage. Mid-task redirect is the natural Phase 5/6 / Workspace-era feature.

### Multi-agent orchestration

Roost already has background agents (`MAX_BACKGROUND_RUNS`). That's enough for agentic workflow 1.0. Anything fancier — agent-to-agent delegation, swarm patterns, planner/executor split — is out of scope.

### Voice / multi-modal input

Out of scope for the same reason. Type-and-read is enough surface to validate the loop.

### Local sandboxed execution

Claude Code can run code in a session-scoped sandbox. Roost might want this eventually for verticals like SME (e.g., "run this analysis on the Stripe export"). Out of scope for agentic workflow 1.0; consider after Phase 4.

### Full IDE-like editing of code files

Roost's audience is non-IT-background users. The agentic surface should not become a code editor by accident. If users want to edit code, they have Claude Code / Cursor / their IDE. Roost's file-editing affordances should be document-shaped, not code-shaped.

---

## 10. Risks and tradeoffs

### 10.1 The opportunity cost is real

A quarter of focused work on agentic workflow is a quarter not spent on: more verticals, deeper SME bundle, RPA flow library expansion, the property-agent CDD work, the Kingston curriculum production. Whether agentic workflow earns its quarter depends on whether the *strategic position* (Roost as the demoable agentic surface) is more valuable than incremental vertical depth.

**My read:** yes, because the verticals plateau at "more integrations" without a brandable surface. Surface is what makes the verticals *legible as a product* instead of as a toolkit.

### 10.2 Demo-vs-product tension

Agentic workflow is *very* demoable. That's a strength and a trap. Demoable features get built to the demo, not to operational reality. Watch for:
- Designing the plan-display to look good in a 5-minute demo and feel cluttered in a 30-minute working session
- Optimising for "first impression" tool-call streaming and missing "after the 200th tool call this week" usability
- Building the bundle skins to be screenshot-ready and not actually used by real agents/admins/SMEs after the demo

Mitigation: every phase ships to at least one real user (Ethan-as-power-user counts) and we look at actual usage data before declaring the phase done.

### 10.3 Brand collision with Claude

Roost's positioning today partly leans on "we give Claude Code a home." Agentic workflow mildly contradicts that — we're saying Roost has its own visible agentic frontend. Risk: confusion about whether Roost is competing with or hosting Claude.

The honest framing — and the one to use externally — is:

> Roost is the agentic surface for your work. The intelligence underneath can be Claude, Gemini, or Codex — your choice, all swappable. Roost is the loop; the engine is configurable.

This positions Roost as **layer-above-the-model**, which is also the right defensive position when model capabilities and prices keep shifting.

### 10.4 Cannibalising existing Roost flows

The existing Web surfaces (`/sme/sync-status`, `/rpa`, kanban) all work today. Agentic workflow is additive, but users who like the existing flows might feel pushed toward agentic workflow by enthusiasm or marketing. Risk: we degrade the things that work in pursuit of the new shiny.

Mitigation: explicit principle that agentic workflow is *added*, not *replacing*. The existing surfaces remain first-class. Agentic workflow is opt-in via `/agentic`, not the default landing.

### 10.5 The Workspace primitive is debt we're choosing to take on later

Option A defers the Workspace primitive. That's the right call, but it's also a real debt — when we want cross-session continuity for non-trivial work, we'll feel the gap. The 7-day cross-channel memory is a partial substitute but doesn't have the *named-scope* affordance.

Mitigation: in Phase 2 design, make sure the chat-message + tool-history schema is **forward-compatible** with bolting on a Workspace concept later. Don't paint into a corner where every conversation is its own unrelated thread with no extension point.

### 10.6 Agent quality bottleneck

Agentic workflow amplifies the agent's quality. A poorly-planning AI produces bad plans visibly. A good-planning AI is a useful collaborator visibly. If the underlying engine (default Gemini, configurable Claude/Codex) plans badly, no UX dressing fixes it.

**My read:** Gemini Pro 3 / Claude 4 / GPT-5 generation are good enough. Validate during Phase 1 with the actual planning prompt — if Gemini doesn't produce good plans against Roost's tool inventory, we either invest in better planning prompts, switch default engine, or hold the rollout.

### 10.7 Approval fatigue

If every step needs approval, users will start rubber-stamping or disabling gates. Approval has to be calibrated: enough for accountability, sparse enough for flow. The autonomy levels are the right calibration knob — but the *defaults* matter. If we default to "supervised" (approve everything), people will turn it off. If we default to "autonomous" (approve only money-moving), people will be surprised by side effects.

**My recommendation:** default to "assisted" — approve at plan level, not per step. Move to supervised only when entering high-stakes verticals (insurance compliance work, money-moving ops).

---

## 11. Connection to C4AIL pedagogy — the pitch underneath

This is the part of the memo that distinguishes the strategy from "build a chat UI." If we get this right, Roost's agentic workflow *is* the C4AIL framework instantiated as software.

### Intellectual labour / accountability labour, made structural

C4AIL teaches that AI does intellectual labour (research, drafting, analysis, computation) and humans retain accountability labour (decisions, sign-offs, defending the outcome). The framework names the split as the structural division of labour that survives AI getting better.

Agentic workflow **instantiates this in product behaviour**:
- The plan-display is intellectual labour proposed
- The approval gate is accountability labour preserved
- The audit trail is the record that the accountability labour happened
- The streaming tool calls are the intellectual labour visible (so the user can verify it was done right)

A user who works in agentic workflow for a week internalises the labour split without being lectured. The product *teaches* the framework by being shaped like it.

### The Translator capability

C4AIL's Translator capability is the Minimum Viable Literacy a leader needs to commission, interrogate, and challenge AI work without becoming a developer. The Translator can read a plan, push back on a step, ask "why are you doing it that way," and decide whether to proceed.

Agentic workflow **is the Translator's workbench**. The plan surface, the visible tool stream, the diff display, the approval gate — these are all designed to be legible to a non-developer who can nonetheless judge whether the work is right. We're not asking the Translator to read code. We're asking them to read a plan in plain English and decide.

This is the pitch I would use externally when explaining what Roost is:

> Roost is the workbench for the Translator. It makes AI's intellectual labour visible enough that a non-developer can supervise it, and it preserves the accountability decisions for the human in the seat. The result is faster work that the human can defend afterwards — what we call Decision Survivability.

### 98/2 principle, embodied

The 98/2 principle says 98% of work is deterministic; 2% is AI-edge. Agentic workflow is *exactly* the surface where the 2% happens. The deterministic 98% — the daily summary, the recurring recipe, the cron job, the form validation — keeps running through Roost's existing surfaces. Agentic workflow is the **deliberate, visible AI lane** for the work that requires judgement.

This framing also tells us what *not* to put in agentic workflow: deterministic operations. If the user is doing something the same way every time, that's a recipe or a cron, not an agentic session.

### The product is the curriculum

Every Roost training programme — `personal-ai-agent`, `insurance-agent-roost`, `property-agent-roost`, `sme-ops`, and the implied Kingston-style admin training — currently teaches AI literacy via a *separate curriculum layer* (slides, labs, reading packs). Agentic workflow lets the product itself teach.

A user who opens agentic workflow, sees a plan, approves it, watches it run, and rolls back when something looks wrong — has just been taught the C4AIL framework experientially. The training programmes become *certification + nuance*, not *first contact*.

This is, frankly, the most defensible long-term position Roost can take. The framework + the product + the training together form a coherent stack that no pure-tool competitor (Zapier, Make, n8n) and no pure-AI competitor (Claude, Gemini, ChatGPT) can match individually.

---

## 12. Decisions needed + prototypes to test

### Decisions needed before Phase 1 starts

| Decision | Options | Recommendation | Decider |
|---|---|---|---|
| Lightweight (Option A) or Workspace primitive (Option B) first? | A first / B first | **A first**, earn B in Phase 5+ (§4) | Ethan |
| Lead surface for agentic workflow? | Web / CLI / Telegram | **Web** lead, CLI Phase 4, Telegram async-approval only (§6) | Ethan |
| Default agent engine for the planner? | Gemini / Claude / Codex / configurable per-user | **Gemini default** (matches existing Roost), Claude/Codex configurable, validate planning quality in Phase 1 (§10.6) | Ethan + technical |
| Default autonomy level for agentic workflow? | supervised / assisted / autonomous | **Assisted** (approve at plan level) (§10.7) | Ethan |
| Web frontend approach? | Extend Jinja+HTMX+SSE / introduce lightweight JS framework | Lean extend-existing; only adopt framework if streaming-display cost forces it | Engineering |

> **On vocabulary.** This memo uses *agentic workflow* throughout as the descriptive term for the capability — it's accurate, industry-standard, and matches how the rest of the field talks about Plan-Approve-Execute loops with streaming tools. Whether the surface ships under a separate product/brand name in the UI is a marketing decision, not a strategy one, and is deliberately out of scope here.

### Prototypes to test before committing

Before Phase 1 fully greenlit, build two cheap probes (1-2 days each):

1. **Plan-quality probe.** Take 10 representative user requests from each vertical (30 total). Feed each to a planner prompt against Roost's MCP tool inventory using Gemini / Claude / Codex. Hand-rate the plans for: completeness, correct tool selection, no fabricated tools, plain-language legibility. **Goes / no-goes Phase 1.** If plan quality is poor across all engines, the strategy needs revision before build.

2. **Streaming-UX probe.** Hack together a minimal `/agentic` route that streams existing MCP tool calls to a chat-like surface, no plan or approval. Show it to 3 users (Ethan + 2 others). Ask: does the streaming visibility *change how you feel about the agent*? If yes, build effort is validated. If no, we may be solving the wrong surface problem.

### Validation gates between phases

- **Phase 1 → Phase 2:** Internal demo shows plan-then-execute reliably across 5 representative tasks per vertical. Plans are rated "would approve" by Ethan ≥80% of the time.
- **Phase 2 → Phase 3:** External demo (Kingston session, agent session, or SME pitch) gets positive enough response to commit to vertical bundle integration. Define "positive enough" as: at least one prospect requests follow-up engagement specifically citing agentic workflow.
- **Phase 3 → Phase 4:** Bundle-specific demos hold up under live workshop use, not just demo theatre. A real Kingston admin user opens agentic workflow unprompted within a week of the workshop.
- **Phase 4 → Phase 5 (Workspace primitive):** Sustained usage data shows users want cross-session continuity for non-trivial work. Define threshold concretely once we have Phase 3 data.

---

## Appendix A — One-paragraph elevator pitch (draft)

> **Roost is the agentic surface for non-developer professionals.** Where today's AI tools either make you a developer (Claude Code, Cursor) or hide the work entirely (ChatGPT, M365 Copilot), Roost shows you the AI's plan before it acts, lets you approve or interrupt at every meaningful step, streams every tool call so you can verify the work, and preserves the audit trail so you can defend the outcome later. The intelligence underneath is configurable — Claude, Gemini, or Codex, your choice. The loop, the verticals, and the operational accountability are Roost. **Built for the Translator: the leader who needs to commission, interrogate, and challenge AI without becoming an engineer.**

---

## Appendix B — Open questions for follow-up sessions

Things this memo intentionally does not answer:

1. Pricing model — is agentic workflow a paid feature, a tier differentiator, or a free add to existing Roost installs?
2. Cloud vs self-host posture — does agentic workflow change the deployment story? (Probably not, but the streaming infrastructure adds a wrinkle.)
3. Multi-tenancy — does agentic workflow preserve the multi-tenant isolation Roost already enforces? (Should, but worth checking against the streaming backend choice.)
4. Compliance — for regulated verticals (insurance, healthcare in future), does the approval audit trail satisfy specific frameworks? (Probably not without explicit work; flag for vertical-specific design.)
5. Onboarding — how does a brand-new user discover and learn agentic workflow? Connects to the `roost-onboard` wizard and the "the product is the curriculum" principle in §11.
6. Failure / fallback — what happens when the agent engine is unavailable (Gemini API down, Claude rate-limited)? Does the agentic surface degrade gracefully?

---

## Document control

- **Replaces:** Nothing — first memo on this topic.
- **Supersedes when:** First implementation spec for Phase 1 is written. This memo then becomes historical context.
- **Related Roost docs:** `platform-overview.md` (Roost architecture), `roost-lite.md` (deployment shape), `rpa.md` / `rpa-authoring.md` (one of the existing surfaces the agentic surface will reuse), `sme-ops.md` (existing draft-queue pattern the agentic surface generalises).
- **Related C4AIL docs:** `c4ail-academy/docs/whitepaper-2.md` §1.6 (substrate/surface), `c4ail-academy/docs/philosophical-framework.md` (Translator capability), `c4ail-academy/marketing/paper-8-the-guildhall.md` (Studio reference for naming).
- **Related programme decks:** `programmes/kingston-staff-enablement/notes/01-intro-1hr-speaker-notes.md` (the session that triggered this memo), `programmes/insurance-agent-roost/CLAUDE.md` (one of the vertical targets).
