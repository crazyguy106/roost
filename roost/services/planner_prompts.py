"""Prompts for the Agentic Workflow planner (Phase 1).

The planner is a *separate* LLM call from the executor. It produces a
structured JSON plan only — no tool execution, no chained reasoning.

Cap reminders (enforced in roost.services.planner):
- ``steps`` ≤ 8
- ``estimated_tool_calls`` ≤ MAX_TOOL_CALLS_PER_RUN (read from gemini_agent)
- Tool names in ``steps[].tools`` must exist in the inventory
"""

PLAN_SYSTEM_PROMPT = """You are the Roost Planner.

Your job: given a user message and a list of available tools, output a
JSON plan describing how Roost should respond. You do NOT execute tools.
You produce a single JSON document and stop.

Rules:
1. Output ONLY a single fenced ```json``` block. No prose before or after.
2. The plan must conform exactly to this schema:
   {
     "summary": "string, ≤120 chars — one-sentence description of what we'll do",
     "steps": [
       {
         "step_id": 1,                          // 1-indexed integer
         "description": "string, ≤200 chars",
         "tools": ["tool_name", ...],           // may be [] for non-tool steps
         "rationale": "string, ≤200 chars"
       }
     ],
     "estimated_tool_calls": 0,                  // integer; sum across steps
     "estimated_duration_s": 0                   // integer; rough wall-clock seconds
   }
3. ``steps`` must have between 1 and 8 entries.
4. Every tool name in ``steps[].tools`` MUST exist in the tool inventory
   provided below. Do not invent tools.
5. For greetings, factual questions, or opinions where no tool is needed,
   return a single step with ``tools: []`` and
   ``description: "Respond conversationally."`` That is a VALID plan.
6. Prefer fewer, larger steps over many tiny ones. A step can use
   multiple tools when they form one logical unit.
7. ``estimated_tool_calls`` must equal the total count across all steps'
   ``tools`` arrays, capped at the per-run limit.
8. Be concise. The user will read this plan before approving execution.
"""


PLAN_USER_TEMPLATE = """## Available tools

{tool_inventory}

## User message

{user_message}

## Output

Produce the JSON plan now."""


def format_tool_inventory(tools: list[dict]) -> str:
    """Render the tool inventory as compact text for the planner prompt.

    Each ``tools`` entry is expected to have ``name`` and ``description``
    keys. Schemas/parameters are intentionally omitted — the planner only
    needs to pick *which* tools, not how to call them.
    """
    if not tools:
        return "(no tools available — respond conversationally)"
    lines = []
    for tool in tools:
        name = tool.get("name", "?")
        desc = (tool.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 140:
            desc = desc[:137] + "..."
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines)
