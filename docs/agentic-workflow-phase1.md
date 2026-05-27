# Roost Agentic Workflow — Phase 1 Implementation Spec

**Status:** Build-ready brief for a Claude agent. Self-contained — does not require conversation context outside this document.

**Parent strategy:** [`agentic-workflow-strategy.md`](agentic-workflow-strategy.md) §8 Phase 1. Read §§1–6 of the strategy memo for *why*. This document is the *how*.

**Repo root assumed:** `/home/dev/projects/roost/`. All paths in this doc are repo-relative unless otherwise noted.

---

## 1. What Phase 1 is, in one paragraph

Roost already has a working chat surface (`/api/chat/message` → SSE stream → agent → tool calls → final response) with destructive-action gating via OTP. Phase 1 extends that into the first half of an *agentic workflow*: before the agent starts executing, it produces a **structured plan** — a list of steps and the tools each step will use — and the user sees that plan in the chat surface before tool calls begin. Then, as the agent executes, every tool invocation streams to the chat as a distinct event (not a coarse "Using tools: X, Y..." line), so the user can watch the work happen. Phase 1 does *not* add per-step approval gates, diff display, autonomy levels, or vertical-bundle skins — those are Phases 2–4.

The end-of-phase demo is: *"Type a request → see the plan appear → press 'go' (or auto-go for now) → watch each tool fire as a labelled card → see the final result."*

---

## 2. Non-goals for Phase 1 (explicit)

- ❌ Per-step approval gates beyond the existing Guardian/OTP flow. (Phase 2)
- ❌ Inline diff display for file edits. (Phase 2)
- ❌ Cost preview in tokens/dollars. (Phase 2)
- ❌ Autonomy-level controls (supervised / assisted / autonomous). (Phase 2)
- ❌ Vertical-bundle-specific demo seeds, vocab, or templates. (Phase 3)
- ❌ CLI surface (`roost agentic` command) or Telegram approval bridge. (Phase 4)
- ❌ Cross-session Workspace primitive. (Phase 5+)
- ❌ Replacing the existing `/chat` route. The new `/agentic` route is *added*; `/chat` stays untouched.

If a feature is not explicitly in §3, it is out of scope.

---

## 3. What Phase 1 adds — the delta

Five concrete additions:

1. **Planner step** — before the agent executes, a planner LLM call produces a structured JSON plan (steps + tools + rationale). New service: `roost/services/planner.py`.
2. **Per-tool SSE events** — agent runners are extended to emit `tool_called`, `tool_returned`, `tool_failed` events (in addition to the existing `progress`/`thinking`/`error`). New optional callback `on_tool_event` is added to the three agent runners.
3. **New SSE event types** — `plan`, `plan_step_started`, `plan_step_completed`, `tool_called`, `tool_returned`, `tool_failed`. Wire format in §4.
4. **`/agentic` route + template** — new FastAPI route (in `roost/web/api_agentic.py`), new Jinja template (`roost/web/templates/agentic.html`). Renders the new events.
5. **Settings exposure** — feature flag `AGENTIC_WORKFLOW_ENABLED`, separate planner-model env var `AGENTIC_PLANNER_MODEL` (default = same provider as `AGENT_PROVIDER`), surfaced on the settings page.

That's the whole list. Anything outside it is out of scope.

---

## 4. Event protocol — SSE wire format

The new `/api/agentic/message` endpoint emits these event types over SSE. Each event is a JSON object inside the `data:` line, terminated by `\n\n` per SSE convention.

### 4.1 Existing event types (kept, unchanged)

```jsonc
{"type": "thinking", "text": "Thinking (gemini)..."}
{"type": "progress", "text": "<free-form progress line>"}
{"type": "error", "text": "<error message>"}
{"type": "final", "text": "<final assistant response, markdown>"}
{"type": "held", "action_id": "...", "tool": "send_email", "description": "..."}
```

### 4.2 New event types (Phase 1)

```jsonc
// Emitted exactly once, after the planner call, before any tool fires.
{
  "type": "plan",
  "plan_id": "agt_a4f1c2",            // ULID/UUID, used to correlate later events
  "summary": "Draft three parent-comms variants and show them inline.",
  "steps": [
    {
      "step_id": 1,
      "description": "Read parent-comms template from Drive.",
      "tools": ["drive_search", "drive_download"],
      "rationale": "Need the canonical template before drafting."
    },
    {
      "step_id": 2,
      "description": "Draft three tone variants (formal, warm, brief).",
      "tools": ["gemini_generate"],
      "rationale": "Single LLM call with three outputs."
    },
    {
      "step_id": 3,
      "description": "Show variants inline in chat.",
      "tools": [],
      "rationale": "No external action — display only."
    }
  ],
  "estimated_tool_calls": 4,
  "estimated_duration_s": 25,
  "guardian_warnings": []              // populated when planner detects money-moving tools; full Guardian integration is Phase 2
}

// Emitted when execution of a step begins (best-effort — see §5.4).
{"type": "plan_step_started", "plan_id": "agt_a4f1c2", "step_id": 1}

// Emitted when a step is considered complete.
{"type": "plan_step_completed", "plan_id": "agt_a4f1c2", "step_id": 1, "status": "ok"}

// Emitted for every tool invocation.
{
  "type": "tool_called",
  "plan_id": "agt_a4f1c2",
  "step_id": 1,                        // null if execution diverges from plan
  "call_id": "tc_8f23",                // unique per call within run
  "tool": "drive_search",
  "args_preview": {"query": "parent comms template", "limit": 5}   // truncated; see §4.3
}

// Emitted when a tool returns successfully.
{
  "type": "tool_returned",
  "plan_id": "agt_a4f1c2",
  "call_id": "tc_8f23",
  "tool": "drive_search",
  "result_preview": "[5 files matched, top: 'Kingston parent-comms template v3.docx']",  // string, truncated to 500 chars
  "duration_ms": 412
}

// Emitted when a tool raises or returns an error envelope.
{
  "type": "tool_failed",
  "plan_id": "agt_a4f1c2",
  "call_id": "tc_8f23",
  "tool": "drive_search",
  "error": "Drive API rate limit (HTTP 429)",
  "duration_ms": 88
}
```

### 4.3 Truncation rules

- `args_preview` — JSON object, keys preserved, each string value truncated to 200 chars (longer values get `…[truncated, N chars]`).
- `result_preview` — single string (stringify if tool returned dict/list), truncated to 500 chars total.
- Binary / file-bytes tool results: emit `"result_preview": "<binary, N bytes>"`. Do not stream raw bytes over SSE.
- Secrets: tool arg keys matching `/secret|password|token|api_key|otp/i` get `"***"`. Apply this *before* truncation.

### 4.4 Event ordering guarantees

The implementation must guarantee:
1. `plan` is emitted exactly once, *before* any `tool_called` for the same `plan_id`.
2. Each `tool_called` for `call_id=X` is followed by exactly one of `tool_returned` or `tool_failed` for `call_id=X`, in the same SSE stream.
3. `final` is the last event of a successful run (after all `plan_step_completed`).
4. `error` or `held` may interrupt the stream at any point; subsequent events after `error` are not required.

---

## 5. Planner contract

### 5.1 Signature

```python
# roost/services/planner.py

from dataclasses import dataclass
from typing import Literal

@dataclass
class PlanStep:
    step_id: int
    description: str
    tools: list[str]
    rationale: str

@dataclass
class Plan:
    plan_id: str
    summary: str
    steps: list[PlanStep]
    estimated_tool_calls: int
    estimated_duration_s: int
    guardian_warnings: list[str]
    raw_text: str        # the planner's full text output, for audit/debugging
    model: str           # e.g. "gemini-2.5-flash"
    fallback_used: bool  # True if structured parse failed and a free-text plan was synthesised

async def generate_plan(
    user_message: str,
    tool_inventory: list[dict],       # [{"name": "...", "description": "...", "args": {...}}, ...]
    system_prompt: str,
    user_id: str,
    session_id: str,
    model: str | None = None,         # defaults to AGENTIC_PLANNER_MODEL env, else AGENT_PROVIDER's model
) -> Plan: ...
```

### 5.2 Prompt template (planner)

The planner uses a dedicated prompt distinct from the executor's system prompt. The full template lives at `roost/services/planner_prompts.py` as `PLAN_SYSTEM_PROMPT` and `PLAN_USER_TEMPLATE`. Required content:

- Identifies itself as a planner, not an executor.
- Lists the tool inventory (names + one-line descriptions). For Phase 1 pass the *names + descriptions* only; do not include full JSON schemas (token budget).
- Instructs JSON-only output matching the schema in §5.3, wrapped in ` ```json … ``` ` fences.
- Caps `steps` at 8. Caps `estimated_tool_calls` at `MAX_TOOL_CALLS_PER_RUN` (currently 25 — read from existing config, do not hardcode).
- Requires that every tool name in `steps[].tools` exists in the inventory. Hallucinated tool names = parse-failure → fallback path (§5.5).
- For requests that do not need tools (greeting, factual question, opinion), the planner returns a single-step plan with `tools: []` and `description: "Respond conversationally."` — this is *valid*, not a failure.

### 5.3 Output JSON schema

```jsonc
{
  "summary": "string, ≤120 chars",
  "steps": [
    {
      "step_id": 1,                  // 1-indexed
      "description": "string, ≤200 chars",
      "tools": ["tool_name_1", "tool_name_2"],   // may be empty
      "rationale": "string, ≤200 chars"
    }
  ],
  "estimated_tool_calls": 4,
  "estimated_duration_s": 25
}
```

`plan_id`, `guardian_warnings`, `raw_text`, `model`, `fallback_used` are populated by `generate_plan()` post-hoc, not by the LLM.

### 5.4 Where step alignment is best-effort

The planner produces a plan; the executor (current `agent.run()`) does not consume the plan as a directive — it sees the same user message and may choose differently. Phase 1 does **not** modify the executor to follow the plan strictly. Therefore:

- The planner is *advisory* to the user, not *binding* on the executor.
- `plan_step_started` / `plan_step_completed` events are emitted by a best-effort mapper that watches the tool stream and tries to attribute each `tool_called` to a `step_id`. If a tool fires that no step claimed, emit the `tool_called` event with `"step_id": null`.
- Phase 2 will tighten this. Phase 1 lives with the soft alignment.

### 5.5 Fallback path

If the planner output cannot be parsed against §5.3 schema:
1. Log the raw output to `roost/data/planner_failures.log` with timestamp + user message hash.
2. Synthesise a one-step fallback plan: `summary = "Plan generation failed — proceeding with direct execution."`, single step `{step_id: 1, description: user_message[:120], tools: [], rationale: "Fallback plan; tool selection deferred to executor."}`, `fallback_used = True`.
3. Emit the `plan` event with `fallback_used: true` so the UI can label it.
4. Continue with execution as normal.

Do **not** retry the planner. Two LLM calls of cost for a plan we already failed to parse is wrong tradeoff for Phase 1.

### 5.6 Planner model selection

```
AGENTIC_PLANNER_MODEL env var (precedence order):
  1. If set explicitly, use it (e.g., "gemini-2.5-flash", "claude-haiku-4-5").
  2. If unset, use the executor model from AGENT_PROVIDER's config.
```

Recommended defaults — but do not hardcode; load from env:
- For Gemini-default Roost: planner = `gemini-2.5-flash` (cheap, fast, structured-output capable).
- For Claude users: planner = `claude-haiku-4-5-20251001`.
- For OpenAI users: planner = `gpt-4o-mini`.

Add to `roost/config.py`:
```python
AGENTIC_PLANNER_MODEL = os.getenv("AGENTIC_PLANNER_MODEL", "")  # empty = use executor model
AGENTIC_WORKFLOW_ENABLED = os.getenv("AGENTIC_WORKFLOW_ENABLED", "false").lower() == "true"
```

Mirror in `roost/config_service.py::FEATURE_FLAGS` so it shows on the settings page.

---

## 6. Touchpoints — exact files

### 6.1 New files

| Path | Purpose | Approx. LoC |
|---|---|---|
| `roost/services/planner.py` | `generate_plan()` factory + Plan / PlanStep dataclasses | ~200 |
| `roost/services/planner_prompts.py` | Planner system prompt + user template | ~80 |
| `roost/services/agentic_events.py` | Event-emit helpers (`emit_plan`, `emit_tool_called`, …) + truncation/redaction utilities | ~150 |
| `roost/web/api_agentic.py` | FastAPI router for `/api/agentic/message`, `/api/agentic/confirm`, `/api/agentic/pending` | ~250 |
| `roost/web/templates/agentic.html` | Jinja template — chat shell, plan card, streaming tool list | ~250 |
| `roost/web/static/agentic.js` | Client-side SSE consumer + DOM updates for new event types | ~200 |
| `tests/test_planner.py` | Plan schema validation, fallback path | ~120 |
| `tests/test_agentic_events.py` | Event ordering, truncation, redaction | ~100 |
| `tests/test_api_agentic.py` | End-to-end smoke against `/api/agentic/message` with a mocked tool inventory | ~150 |

### 6.2 Modified files

| Path | What changes | Why |
|---|---|---|
| `roost/config.py` | Add `AGENTIC_WORKFLOW_ENABLED`, `AGENTIC_PLANNER_MODEL` env reads | Feature flag + planner model selection |
| `roost/config_service.py` | Add both flags to `FEATURE_FLAGS` with metadata | Settings-page visibility |
| `roost/adapters/__init__.py` | `create_agent()` gains optional `on_tool_event` arg; passed through to runners | Plumb event callback into agent factory |
| `roost/agents.py` | `ClaudeAgent.run()` and `OpenAIAgent.run()` emit `tool_called` / `tool_returned` / `tool_failed` via `on_tool_event` callback if provided. Existing `on_progress` behaviour unchanged. | Per-tool events |
| `roost/gemini_agent.py` | `GeminiAgent.run()` same change as above | Per-tool events |
| `roost/web/app.py` | Mount `api_agentic` router; mount `agentic.html` route at `GET /agentic` (gated by `AGENTIC_WORKFLOW_ENABLED`) | New surface |
| `roost/web/templates/base.html` (if exists) | Add sidebar/nav link to `/agentic` (gated by flag) | Discovery |
| `env-templates/dev.env` (or wherever env templates live — check repo) | Add `AGENTIC_WORKFLOW_ENABLED=false` and `AGENTIC_PLANNER_MODEL=` placeholders | Onboarding |
| `docs/agentic-workflow-strategy.md` | Add §8 Phase 1 cross-link to this spec | Discoverability |
| `README.md` | Add this spec to the Documentation section | Discoverability |

### 6.3 Files explicitly NOT touched

- `roost/web/api_chat.py` — left alone. The existing `/chat` surface continues to work as today.
- `roost/web/templates/chat.html` — left alone.
- Any bundle file under `roost/extras/*` — Phase 1 is core-only.
- Guardian (`roost/services/guardian.py`) — left alone. Existing OTP-on-destructive flow continues; Phase 2 expands it.

---

## 7. Implementation order

The agent should implement in this order. Each step ends with a runnable checkpoint.

### Step 1 — Config + flag scaffolding
- Add the two env vars to `roost/config.py`.
- Add entries to `roost/config_service.py::FEATURE_FLAGS`.
- Add to `env-templates/*.env`.
- **Checkpoint:** `python3 -m pytest -q` still passes. `python3 -c "from roost.config import AGENTIC_WORKFLOW_ENABLED, AGENTIC_PLANNER_MODEL"` works.

### Step 2 — Events module + truncation/redaction utilities
- Create `roost/services/agentic_events.py` with helper functions: `truncate_args(args)`, `truncate_result(result)`, `redact_secrets(args)`, and an `EventEmitter` class that holds an async queue and exposes `emit(event_type, payload)`.
- Write `tests/test_agentic_events.py` covering: secret redaction across common key names, truncation of long strings, binary handling, dict/list stringification.
- **Checkpoint:** `python3 -m pytest tests/test_agentic_events.py` passes.

### Step 3 — Planner service
- Create `roost/services/planner_prompts.py` with `PLAN_SYSTEM_PROMPT` and `PLAN_USER_TEMPLATE` (template fields: `{tool_inventory}`, `{user_message}`).
- Create `roost/services/planner.py` with `generate_plan()`. Implementation reuses the same provider abstraction as the executor — call `create_agent()` with a planner-only system prompt, no tools, request JSON. Parse the response. On parse failure, fallback per §5.5.
- Write `tests/test_planner.py` covering: valid JSON parse, missing field, hallucinated tool name (must trigger fallback), no-tools-needed plan, empty steps array (must trigger fallback).
- **Checkpoint:** `python3 -m pytest tests/test_planner.py` passes. Manual: `python3 -c "import asyncio; from roost.services.planner import generate_plan; print(asyncio.run(generate_plan('what time is it', [], '', 'u1', 's1')))"` returns a Plan object.

### Step 4 — Agent runner event hooks
- Add `on_tool_event: Callable | None = None` parameter to:
  - `roost/agents.py::ClaudeAgent.run()` and `OpenAIAgent.run()`
  - `roost/gemini_agent.py::GeminiAgent.run()`
- Before each tool invocation, if `on_tool_event` is provided, await `on_tool_event({"type": "tool_called", "tool": <name>, "args": <args>, "call_id": <generated>})`.
- After each tool returns, await `on_tool_event({"type": "tool_returned", "tool": <name>, "call_id": <same>, "result": <result>, "duration_ms": <measured>})`.
- On tool exception, await `on_tool_event({"type": "tool_failed", "tool": <name>, "call_id": <same>, "error": <str(e)>, "duration_ms": <measured>})` then re-raise (or handle per existing logic — preserve current behaviour).
- `call_id`: generate inside the runner. Use `f"tc_{secrets.token_hex(4)}"`.
- **Important:** the existing `on_progress` callback continues to work unchanged. `on_tool_event` is additive. Tests that rely on `on_progress` must still pass.
- Update `roost/adapters/__init__.py::create_agent()` to accept and pass through `on_tool_event` (but don't change `run_agent()` — that's for non-web adapters).
- **Checkpoint:** existing `python3 -m pytest -q` passes (no regression). New unit test in `tests/test_agentic_events.py` exercises the hook with a mocked agent.

### Step 5 — `/api/agentic/message` endpoint
- Create `roost/web/api_agentic.py`. Pattern after `api_chat.py` but with the new event protocol.
- Endpoint flow:
  1. Rate-limit check (reuse `_last_message` pattern from `api_chat.py`).
  2. Authenticate user via `_get_user(request)`.
  3. Check `AGENTIC_WORKFLOW_ENABLED`; 404 if false.
  4. Build tool inventory from the agent's tool registry (whichever subset of `TIER_WEB` applies).
  5. Call `generate_plan(user_message, tool_inventory, system_prompt, user_id, session_id)`.
  6. Emit `plan` event with the result.
  7. Create the agent via `create_agent(mode, session_id, system_prompt)`.
  8. Pass an `on_tool_event` callback that translates runner events → SSE events (mapping `step_id` best-effort via tool-name match against the plan).
  9. Run the agent.
  10. Emit `final` event with the agent's response.
- Reuse the existing `confirmation_callback` + held-actions pattern from `api_chat.py` for destructive tools. Held-action events are emitted as `held` (same as today).
- **Checkpoint:** `tests/test_api_agentic.py` posts a mocked user message, asserts event ordering (`plan` → `tool_called` → `tool_returned` → `final`), passes.

### Step 6 — Frontend template + JS
- Create `roost/web/templates/agentic.html`. Layout:
  - Top: page title "Agentic Workflow" (this is the heading users see; brand name TBD per strategy memo §12).
  - Top: text input (same pattern as `chat.html`) for the user message.
  - Middle: a *Plan card* that appears when a `plan` event arrives — shows summary, steps as a numbered list with tool chips, estimated duration. Visually distinct (bordered card).
  - Below the plan card: a *streaming tool list* — each `tool_called` event appends a row with tool name, args summary, status spinner; each `tool_returned`/`tool_failed` updates the row in place (status icon + result preview).
  - Bottom: final response area, rendered as markdown.
- Create `roost/web/static/agentic.js`. Uses the browser `EventSource` API to subscribe to the SSE endpoint. Switch on `event.type`, update the DOM accordingly. No framework dependency — vanilla JS in the spirit of the existing Roost frontend.
- Mount the route in `roost/web/app.py`:
  ```python
  @app.get("/agentic", response_class=HTMLResponse)
  async def agentic_page(request: Request):
      if not AGENTIC_WORKFLOW_ENABLED:
          raise HTTPException(404)
      # auth check pattern from existing pages
      return templates.TemplateResponse("agentic.html", {"request": request, "user": user})
  ```
- Add nav link in `base.html` (gated on flag).
- **Checkpoint:** manual smoke — set `AGENTIC_WORKFLOW_ENABLED=true`, restart, navigate to `/agentic`, submit "list my unread emails", see plan card render, see tool events stream, see final response.

### Step 7 — Strategy memo + README cross-refs
- Add a one-line link from `docs/agentic-workflow-strategy.md` §8 Phase 1 to this file.
- Add to `README.md` Documentation section.
- **Checkpoint:** `grep -r "agentic-workflow-phase1" docs/ README.md` returns both files.

### Step 8 — Acceptance tests (see §9)

---

## 8. Token & rate-limit cost shape

For sizing: one user message through `/api/agentic/message` produces:

- 1 planner LLM call: ~500 input tokens (tool inventory + user msg + system) + ~300 output tokens (plan JSON). At `gemini-2.5-flash` rates this is sub-cent.
- 1 executor LLM call cycle: same as today's `/chat` — unchanged.

Net: ~1 extra cheap LLM call per request. Acceptable.

If the planner fails parsing on >30% of requests in dogfood week, the prompt is the problem — iterate prompts, not retry logic.

---

## 9. Acceptance tests (the bar to call Phase 1 done)

All must pass before declaring Phase 1 shipped.

### 9.1 Automated (CI)

```bash
python3 -m pytest -q
```

Must pass with:
- All pre-existing tests still passing (no regression).
- New tests in `tests/test_planner.py`, `tests/test_agentic_events.py`, `tests/test_api_agentic.py` all green.

### 9.2 Manual smoke checklist

With `AGENTIC_WORKFLOW_ENABLED=true` and `AGENT_PROVIDER=gemini`:

1. **Cold load.** Navigate to `/agentic`. Page renders. Input is focused. No console errors.
2. **No-tools request.** Submit "what's your name?". Expect: `plan` event with single tool-less step, then `final`. No `tool_called` events. Total wall time < 5s.
3. **Single-tool request.** Submit "list my unread emails". Expect: `plan` event with 1–2 steps, 1 `tool_called` for `search_emails`, 1 `tool_returned`, then `final`. Tool row in UI shows the args preview and a short result summary.
4. **Multi-tool request.** Submit "what's on my calendar today and any urgent emails?". Expect: `plan` event with ≥2 steps, ≥2 tool calls (one for calendar, one for email), events stream in order, `final` arrives last.
5. **Held action.** Submit "send a test email to myself saying hi". Expect: `plan` event, then `tool_called` for `draft_email`, then `tool_returned`, then `held` event when `send_email` is gated. UI shows OTP prompt. (This proves Guardian/OTP path still works through the new surface.)
6. **Tool failure.** Temporarily break a tool (or pick one with a known failure mode). Expect: `tool_failed` event with error string, agent still emits `final` (with a graceful explanation).
7. **Planner fallback.** Force planner failure (e.g. point `AGENTIC_PLANNER_MODEL` at an invalid model name). Expect: `plan` event with `fallback_used: true`, single fallback step, agent still executes normally.
8. **Secret redaction.** Submit a request that would invoke a tool with arg names matching the secret regex. Inspect the SSE stream in browser devtools — values must be `***`, not the real values.
9. **Long-result truncation.** Submit a request that returns a large result (e.g. list a long Drive folder). `result_preview` in SSE must be ≤500 chars, with truncation suffix.
10. **No `/chat` regression.** Visit `/chat`, submit a message, confirm it still works exactly as before (same event types as pre-Phase-1).

### 9.3 Plan-quality eval (gating Phase 2)

Run 10 representative requests across the three target verticals (admin / agent / SME — pull from strategy memo §7). Have Ethan rate each plan **"would approve" / "would modify" / "would reject"**. Phase 2 may not start until ≥80% are "would approve" *or* a documented prompt-iteration cycle has been completed.

If <80% pass, the planner prompt needs revision — not the surface, not the protocol. The strategy hinges on plan quality being good enough to be worth showing.

---

## 10. Open questions for the implementing agent

These can be decided during build. Document the decision in commit messages or a follow-up note.

1. **Plan persistence.** Should plans be stored in the DB for audit? Recommendation: yes — add a lightweight `agentic_plans` table (plan_id PK, user_id, session_id, summary, steps_json, created_at, fallback_used, model). Insert on plan generation. Phase 2 will read from it for the audit trail.
2. **Tool inventory source.** Where does `/api/agentic/message` get the tool list? Recommendation: introspect the agent instance's `_tool_schemas` (or equivalent) — same source the LLM sees. Don't maintain a parallel list.
3. **Step-to-tool mapping accuracy.** What heuristic maps a `tool_called` event to a `step_id`? Recommendation: first match against `steps[].tools` — if exactly one step claims this tool name and the step hasn't completed, assign that `step_id`. Otherwise `null`. Don't over-engineer.
4. **Session continuity.** Does `/agentic` reuse the same `session_id` format as `/chat` (`web:{user_id}:agent`)? Recommendation: different — use `web:{user_id}:agentic` — so chat history doesn't bleed across surfaces during Phase 1 dogfood.
5. **HTMX vs vanilla JS.** Strategy memo §12 leans extend-existing. Recommendation: vanilla JS + `EventSource` for the SSE consumer. HTMX SSE extension is fine if it's already in use elsewhere — check the existing chat template first.

---

## 11. Risk register specific to Phase 1

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Plan-quality is bad enough that the surface annoys users | Medium | High | §9.3 gate before Phase 2; iterate the prompt, not the protocol |
| Per-tool event emission breaks the existing agent runners | Low | High | `on_tool_event` is additive; existing `on_progress` is untouched; full pytest run is the gate |
| Step-to-tool mapping is so wrong that the UI confuses users | Medium | Medium | Accept "step_id: null" as a normal case; UI must render it gracefully (show under "ad-hoc" section, not error) |
| Secret redaction misses a key name | Low | Critical | Regex is permissive (`secret\|password\|token\|api_key\|otp`); add a redaction unit test per tool that handles credentials |
| Planner LLM cost balloons (long tool inventory) | Medium | Low | Inventory passes names + 1-line descriptions only, not full JSON schemas. If still too long, truncate inventory to top-K by relevance in a later phase |
| Two SSE streams (chat + agentic) confuse the user | Low | Low | Phase 1 keeps both live; nav surfacing makes the distinction. Strategy memo §12 will eventually consolidate naming |

---

## 12. What Phase 2 will need from Phase 1 (forward-compat hooks)

Don't build any of this in Phase 1. Just make sure Phase 1 doesn't paint Phase 2 into a corner.

- **Approval gates.** Phase 2 needs to pause execution before a flagged step. Phase 1's event stream is one-way (server → client). For Phase 2, the existing `/api/chat/confirm` pattern will be re-used at `/api/agentic/confirm`. Make sure the held-action flow already works (covered by acceptance test §9.2 #5).
- **Diff display.** Phase 2 adds diffs for file-writing tools. Phase 1's `tool_returned.result_preview` is a string; Phase 2 will add an optional `diff` field. Build the event-emit helper in a way that allows extra fields without schema break.
- **Autonomy controls.** Phase 2 adds supervised/assisted/autonomous. Phase 1 effectively runs in "assisted" mode (plan shown, no per-step gate). Don't bake "assisted" assumptions into hard-coded behaviour — leave room for a runtime config knob.

---

## Appendix A — Synthetic end-to-end transcript

User request: *"What's on my calendar today and draft a one-line summary for my morning standup."*

SSE stream (timestamps illustrative):

```
t=0.0    data: {"type":"thinking","text":"Thinking (gemini)..."}
t=1.2    data: {"type":"plan","plan_id":"agt_b91e3a","summary":"Fetch today's calendar then draft a standup line.","steps":[
                  {"step_id":1,"description":"Get today's calendar events.","tools":["get_today_events"],"rationale":"Source data for the summary."},
                  {"step_id":2,"description":"Draft one-line standup summary from events.","tools":["gemini_generate"],"rationale":"Convert raw event list into a one-line summary."}
                ],"estimated_tool_calls":2,"estimated_duration_s":8,"guardian_warnings":[],"fallback_used":false}
t=1.3    data: {"type":"plan_step_started","plan_id":"agt_b91e3a","step_id":1}
t=1.4    data: {"type":"tool_called","plan_id":"agt_b91e3a","step_id":1,"call_id":"tc_3f81","tool":"get_today_events","args_preview":{}}
t=2.1    data: {"type":"tool_returned","plan_id":"agt_b91e3a","call_id":"tc_3f81","tool":"get_today_events","result_preview":"[3 events: 09:30 standup, 11:00 client call, 14:00 review]","duration_ms":712}
t=2.2    data: {"type":"plan_step_completed","plan_id":"agt_b91e3a","step_id":1,"status":"ok"}
t=2.3    data: {"type":"plan_step_started","plan_id":"agt_b91e3a","step_id":2}
t=2.4    data: {"type":"tool_called","plan_id":"agt_b91e3a","step_id":2,"call_id":"tc_5c02","tool":"gemini_generate","args_preview":{"prompt":"Write a one-line standup summary for these events: ..."}}
t=4.0    data: {"type":"tool_returned","plan_id":"agt_b91e3a","call_id":"tc_5c02","tool":"gemini_generate","result_preview":"Today: standup at 09:30, client call at 11:00, internal review at 14:00.","duration_ms":1604}
t=4.1    data: {"type":"plan_step_completed","plan_id":"agt_b91e3a","step_id":2,"status":"ok"}
t=4.2    data: {"type":"final","text":"Here's your standup line:\n\n> Today: standup at 09:30, client call at 11:00, internal review at 14:00."}
```

---

## Appendix B — Existing Roost code references

For the implementing agent's quick orientation. Read these before writing code:

- **Existing chat endpoint:** `roost/web/api_chat.py:49–207` — the pattern to copy for `api_agentic.py`.
- **Held-action / OTP flow:** `roost/web/api_chat.py:93–117, 208–283`.
- **Agent factory:** `roost/adapters/__init__.py:94–155`.
- **Gemini executor:** `roost/gemini_agent.py:1136–1300` — tool-call loop is around line 1245–1295.
- **Claude executor:** `roost/agents.py:330–410`.
- **OpenAI executor:** `roost/agents.py:449–520`.
- **Tool-call cap:** `MAX_TOOL_CALLS_PER_RUN` — search the codebase for the canonical definition (do not duplicate the constant).
- **Config + feature flags:** `roost/config.py` (env reads), `roost/config_service.py` (settings-page metadata).
- **System prompt builder:** `roost/context.py::build_agent_context()`.

---

## Doc control

- **Author:** Ethan Seow + Claude
- **Status at write time:** Draft v1 — awaiting first build pass
- **Updates:** revise this doc in place as build reality diverges. Don't keep a parallel "what we actually built" doc.
- **Sister docs:** `agentic-workflow-strategy.md` (the *why*), `agentic-workflow-phase2.md` (TBC, written after Phase 1 demo).
