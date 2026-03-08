"""MCP tools for Gemini API integration.

Leverages Gemini's large context window (1-2M tokens) for bulk document
processing, text cleanup, summarisation, and comparison tasks that benefit
from high throughput at lower cost. Claude orchestrates; Gemini processes.

Uses the google-genai SDK (google-genai >= 1.0).
"""

import logging
import sqlite3
import os
from datetime import datetime, timezone

from roost.mcp.server import mcp

logger = logging.getLogger("roost.mcp.tools_gemini")

# Default model — configurable via GEMINI_MODEL env var
_DEFAULT_MAX_OUTPUT = 8192

# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------
_USAGE_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "gemini_usage.db",
)

# Per-million-token pricing (USD)
_PRICING = {
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
}


def _init_usage_db():
    """Create the usage table if it doesn't exist."""
    os.makedirs(os.path.dirname(_USAGE_DB), exist_ok=True)
    conn = sqlite3.connect(_USAGE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            thinking_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0.0
        )
    """)
    conn.commit()
    conn.close()


def _log_usage(tool_name: str, model: str, usage_metadata) -> dict:
    """Extract token counts from response usage_metadata and log to DB.

    Returns a dict with the usage info for inclusion in tool responses.
    """
    input_tokens = getattr(usage_metadata, "prompt_token_count", 0) or 0
    output_tokens = getattr(usage_metadata, "candidates_token_count", 0) or 0
    thinking_tokens = getattr(usage_metadata, "thoughts_token_count", 0) or 0
    total_tokens = getattr(usage_metadata, "total_token_count", 0) or 0

    # Calculate cost
    prices = _PRICING.get(model, {"input": 0.50, "output": 3.00})
    cost = (
        (input_tokens / 1_000_000) * prices["input"]
        + ((output_tokens + thinking_tokens) / 1_000_000) * prices["output"]
    )

    try:
        _init_usage_db()
        conn = sqlite3.connect(_USAGE_DB)
        conn.execute(
            "INSERT INTO usage (timestamp, tool_name, model, input_tokens, "
            "output_tokens, thinking_tokens, total_tokens, estimated_cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                tool_name, model, input_tokens, output_tokens,
                thinking_tokens, total_tokens, round(cost, 6),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to log usage: %s", e)

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(cost, 6),
    }


def _get_client():
    """Build a Gemini client using stored API key."""
    from google import genai
    from roost.config import GEMINI_API_KEY

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not configured. Set it in .env or environment.")

    return genai.Client(api_key=GEMINI_API_KEY)


def _get_model() -> str:
    """Get the configured Gemini model name."""
    from roost.config import GEMINI_MODEL
    return GEMINI_MODEL or "gemini-3-flash-preview"


def _generate(prompt: str, max_output_tokens: int = _DEFAULT_MAX_OUTPUT,
               tool_name: str = "generate") -> str:
    """Common helper to generate text from Gemini."""
    client = _get_client()
    model = _get_model()
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "max_output_tokens": max_output_tokens,
            "temperature": 0.1,
        },
    )
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        _log_usage(tool_name, model, response.usage_metadata)
    return response.text


@mcp.tool()
def gemini_generate(
    prompt: str,
    context: str = "",
    system_instruction: str = "",
    temperature: float = 0.4,
    max_output_tokens: int = 32768,
    output_file: str = "",
    thinking: bool = False,
    thinking_budget: int = 8192,
) -> dict:
    """Single-shot Gemini generation with full control over context and output.

    Unlike gemini_agent (multi-turn with file tools), this is a focused one-shot
    call where Claude pre-assembles all context. Ideal for curriculum deliverable
    generation: Claude reads module plans, extracts relevant sections, and passes
    them as context — Gemini generates the content in one pass.

    Args:
        prompt: The generation task (e.g. "Generate a slide deck for LU1...").
        context: Reference material to prepend (e.g. module plan excerpt, K&A list).
            Sent as part of user content, separated from prompt by ---.
        system_instruction: Role/tone instruction using Gemini's native system
            instruction field (e.g. "You are a curriculum developer...").
        temperature: Generation temperature (0.0-2.0). Slides: 0.6, Labs: 0.25,
            Assessments: 0.4, Reading Packs: 0.6.
        max_output_tokens: Maximum output tokens (default 32768).
        output_file: If set, writes output to this file path and returns metadata
            + 500-char preview (saves Claude's context window). Creates parent dirs.
        thinking: Enable Gemini's native thinking mode (default False).
        thinking_budget: Token budget for thinking when enabled (default 8192).
    """
    try:
        from google.genai import types

        client = _get_client()
        model = _get_model()

        # Build user content: context (if any) + prompt
        if context:
            user_content = f"{context}\n\n---\n\n{prompt}"
        else:
            user_content = prompt

        # Build config
        config_kwargs = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }

        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if thinking:
            try:
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=thinking_budget,
                )
            except Exception:
                logger.warning("ThinkingConfig not supported — ignoring")

        config = types.GenerateContentConfig(**config_kwargs)

        response = client.models.generate_content(
            model=model,
            contents=user_content,
            config=config,
        )

        # Track usage
        usage = {}
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = _log_usage("gemini_generate", model, response.usage_metadata)

        result_text = response.text

        # Write to file if requested
        if output_file:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(result_text)
            return {
                "model": model,
                "output_file": output_file,
                "output_size": len(result_text),
                "preview": result_text[:500],
                "usage": usage,
            }

        return {
            "model": model,
            "result": result_text,
            "output_length": len(result_text),
            "usage": usage,
        }

    except Exception as e:
        logger.exception("gemini_generate failed")
        return {"error": str(e)}


@mcp.tool()
def gemini_summarize(text: str, prompt: str = "Summarize this text concisely.") -> dict:
    """Summarize or analyze large text using Gemini's large context window.

    Ideal for long email threads, large documents, or multi-file content
    that benefits from Gemini's 1M+ token context.

    Args:
        text: The text content to process (can be very large).
        prompt: Instructions for how to process the text.
    """
    try:
        full_prompt = f"{prompt}\n\n---\n\n{text}"
        result = _generate(full_prompt, tool_name="gemini_summarize")
        return {
            "model": _get_model(),
            "input_length": len(text),
            "result": result,
        }
    except Exception as e:
        logger.exception("gemini_summarize failed")
        return {"error": str(e)}


@mcp.tool()
def gemini_process_document(file_path: str, prompt: str) -> dict:
    """Read a local file and process it with Gemini.

    Supports text files (.txt, .md, .csv, .json, .py, etc.).
    For PDFs, convert to text first using pdftotext.

    Args:
        file_path: Absolute path to the local file to process.
        prompt: Instructions for what to do with the file content.
    """
    try:
        import os
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}

        size = os.path.getsize(file_path)
        if size > 10_000_000:  # 10MB guard
            return {"error": f"File too large ({size} bytes). Max 10MB for text processing."}

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        full_prompt = f"{prompt}\n\nFile: {os.path.basename(file_path)}\n\n---\n\n{content}"
        result = _generate(full_prompt, max_output_tokens=_DEFAULT_MAX_OUTPUT,
                           tool_name="gemini_process_document")

        return {
            "model": _get_model(),
            "file_path": file_path,
            "file_size": size,
            "input_length": len(content),
            "result": result,
        }
    except Exception as e:
        logger.exception("gemini_process_document failed")
        return {"error": str(e)}


@mcp.tool()
def gemini_clean_text(text: str, instructions: str = "") -> dict:
    """Clean or fix text using Gemini — OCR correction, formatting, deduplication.

    Good for: fixing PDF extraction artifacts, normalising formatting,
    removing headers/footers, fixing character encoding issues.

    Args:
        text: The text to clean.
        instructions: Specific cleaning instructions (e.g. "Fix OCR ligature
            errors where 3=ti, 1=ti in headings, ?=tt").
    """
    try:
        default_instructions = (
            "Clean and fix this text. Preserve all meaningful content. "
            "Fix any obvious OCR errors, encoding artifacts, or formatting issues. "
            "Remove page numbers, headers, footers, and repeated copyright lines. "
            "Output the cleaned text only, no commentary."
        )
        prompt = (
            f"{instructions or default_instructions}\n\n"
            f"---\n\n{text}"
        )
        result = _generate(prompt, max_output_tokens=max(len(text) // 2, _DEFAULT_MAX_OUTPUT),
                           tool_name="gemini_clean_text")

        return {
            "model": _get_model(),
            "input_length": len(text),
            "output_length": len(result),
            "result": result,
        }
    except Exception as e:
        logger.exception("gemini_clean_text failed")
        return {"error": str(e)}


@mcp.tool()
def gemini_compare(text_a: str, text_b: str, prompt: str = "") -> dict:
    """Compare two texts using Gemini — diff, merge analysis, cross-reference.

    Good for: comparing document versions, finding differences between
    drafts, cross-referencing question banks, identifying gaps.

    Args:
        text_a: First text (e.g. original version).
        text_b: Second text (e.g. edited version).
        prompt: Specific comparison instructions (e.g. "Identify all
            questions in Text B that modify or replace questions in Text A").
    """
    try:
        default_prompt = (
            "Compare these two texts. Identify what was added, removed, "
            "and modified. Present the differences clearly."
        )
        full_prompt = (
            f"{prompt or default_prompt}\n\n"
            f"=== TEXT A ===\n\n{text_a}\n\n"
            f"=== TEXT B ===\n\n{text_b}"
        )
        result = _generate(full_prompt, tool_name="gemini_compare")

        return {
            "model": _get_model(),
            "text_a_length": len(text_a),
            "text_b_length": len(text_b),
            "result": result,
        }
    except Exception as e:
        logger.exception("gemini_compare failed")
        return {"error": str(e)}


@mcp.tool()
def gemini_research(query: str, context: str = "") -> dict:
    """Research a topic using Gemini with Google Search grounding.

    Leverages Gemini's native integration with Google Search for
    up-to-date information retrieval. Good for first-pass research,
    gathering reference materials, and fact-checking.

    Args:
        query: The research question or topic.
        context: Optional context to focus the research
            (e.g. "For a non-technical cybersecurity certification audience").
    """
    try:
        from google import genai
        from google.genai import types
        from roost.config import GEMINI_API_KEY

        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = query
        if context:
            prompt = f"{context}\n\n{query}"

        model = _get_model()
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
                max_output_tokens=_DEFAULT_MAX_OUTPUT,
            ),
        )

        if hasattr(response, "usage_metadata") and response.usage_metadata:
            _log_usage("gemini_research", model, response.usage_metadata)

        return {
            "model": model,
            "query": query,
            "result": response.text,
        }
    except Exception as e:
        logger.exception("gemini_research failed")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Gemini Agent — multi-step tool-use loop
# ---------------------------------------------------------------------------

# Context budget: Gemini 2.5 Flash has 1M tokens (~4M chars).
# We use conservative thresholds to avoid degraded output quality.
_CONTEXT_BUDGET_CHARS = 3_000_000   # ~750K tokens
_CONTEXT_WARN_PCT = 0.70            # warn agent at 70%
_CONTEXT_COMPACT_PCT = 0.85         # truncate file reads at 85%

# Research mode: tighter thresholds — agent externalises findings to disk
_RESEARCH_WARN_PCT = 0.60
_RESEARCH_COMPACT_PCT = 0.75

_DOCUMENT_SYSTEM_PROMPT = (
    "# ROLE\n"
    "You are an autonomous document processing agent.\n\n"
    "# ENVIRONMENT\n"
    "Working directory: {working_dir}\n\n"
    "# TOOLS\n"
    "You have 5 tools:\n"
    "- read_file: Read a file's contents\n"
    "- list_files: List directory contents with optional glob\n"
    "- search_text: Search for text patterns across files\n"
    "- write_file: Create a new file or fully replace an existing one\n"
    "- edit_file: Surgical find-and-replace in an existing file\n\n"
    "# RULES\n"
    "1. ALWAYS read source materials BEFORE generating content.\n"
    "2. ALWAYS use write_file to save deliverables. "
    "NEVER return large content (>200 words) as text — write it to a file.\n"
    "3. Use edit_file for changes to existing files. "
    "Use write_file ONLY for new files.\n"
    "4. Do NOT invent facts, citations, statistics, or references.\n"
    "5. For curriculum content: match the tone and structure of "
    "existing materials. Preserve numbering and formatting.\n"
    "6. If you receive a CONTEXT WARNING: stop reading new files, "
    "work with what you have, prioritise writing output.\n\n"
    "# WORKFLOW\n"
    "Phase 1 — GATHER: Read all required source files.\n"
    "Phase 2 — PLAN: State your approach in 2-3 sentences.\n"
    "Phase 3 — EXECUTE: Produce deliverables using write_file/edit_file.\n"
    "Phase 4 — VERIFY: Re-read output files to confirm correctness.\n"
    "Phase 5 — SUMMARISE: Report what was created, file paths, and sizes.\n"
)

# Research phases: (name, cumulative_pct, goal)
# Boundaries computed as round(cum_pct * max_turns)
_RESEARCH_PHASES = [
    ("FRAME", 0.13,
     "Restate the research question. Write a research plan to the findings "
     "file: what sub-questions need answering? What sources to check? "
     "What would a good answer look like?"),
    ("GATHER", 0.47,
     "Read sources and extract findings to the findings file. "
     "One source at a time. Record: source, key claim, evidence quality, "
     "relevance."),
    ("STRUCTURE", 0.60,
     "Read findings file. Identify distinct themes or requirements. "
     "Write numbered section headings to the output file."),
    ("DEVELOP", 0.87,
     "Deep-dive one requirement per turn. Add evidence, analysis, and "
     "recommendations under each heading in the output file."),
    ("COLLATE", 1.00,
     "Read the output file. Add introduction, executive summary, "
     "confidence levels, and gaps. Finalize."),
]

_RESEARCH_SYSTEM_PROMPT = (
    "# ROLE\n"
    "You are a research agent. Find, evaluate, and synthesize information.\n\n"
    "# ENVIRONMENT\n"
    "Working directory: {working_dir}\n\n"
    "# TOOLS\n"
    "You have 5 file tools (read_file, list_files, search_text, write_file, "
    "edit_file).\n"
    "{search_note}\n\n"
    "# RULES\n"
    "1. Extract key findings as you go — do NOT accumulate raw content "
    "in memory.\n"
    "2. After reading a source, IMMEDIATELY append findings to "
    "{findings_file}.\n"
    "3. Each finding: source (file/URL), key claim, evidence quality "
    "(strong/moderate/weak), relevance.\n"
    "4. Do NOT re-read files you have already processed. Your findings "
    "file IS your memory.\n"
    "5. Do NOT invent facts, citations, statistics, or references.\n"
    "6. Follow the PHASE status line you receive each turn. It tells you "
    "which phase you should be in, how many turns you have left in that "
    "phase, and what to achieve.\n\n"
    "# WORKFLOW\n"
    "Phase 1 — FRAME: Restate the research question. Write a research "
    "plan to {findings_file}: sub-questions, sources to check, what a "
    "good answer looks like.\n"
    "Phase 2 — GATHER: Read sources, extract findings, append to "
    "{findings_file}. One source at a time.\n"
    "Phase 3 — STRUCTURE: Read {findings_file}. Identify distinct themes "
    "or requirements. Write numbered section headings to {output_file}.\n"
    "Phase 4 — DEVELOP: One requirement per turn. Deep-dive each heading "
    "in {output_file} — add evidence, analysis, recommendations.\n"
    "Phase 5 — COLLATE: Read {output_file}. Add introduction, summary, "
    "confidence levels, gaps. Finalize.\n"
)

_AGENT_TOOL_DECLARATIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the text content.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "list_files",
        "description": "List files and directories at a path. Optionally filter by glob pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Absolute directory path to list",
                },
                "pattern": {
                    "type": "string",
                    "description": "Optional glob pattern to filter (e.g. '*.md')",
                },
            },
            "required": ["directory"],
        },
    },
    {
        "name": "search_text",
        "description": (
            "Search for a text pattern in files within a directory. "
            "Returns matching lines with file paths and line numbers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text pattern to search for (case-insensitive)",
                },
                "directory": {
                    "type": "string",
                    "description": "Directory to search in",
                },
                "file_glob": {
                    "type": "string",
                    "description": "Optional file pattern (e.g. '*.md')",
                },
            },
            "required": ["pattern", "directory"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates or overwrites the entire file.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Edit a file by replacing a specific string with new text. "
            "More precise than write_file — use this for surgical edits "
            "to existing files. The old_string must match exactly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find in the file",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false, first only)",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
]


def _estimate_context_chars(contents: list) -> int:
    """Estimate total chars in the conversation history."""
    total = 0
    for content in contents:
        for part in content.parts:
            if hasattr(part, "text") and part.text:
                total += len(part.text)
            if hasattr(part, "function_call") and part.function_call:
                total += len(str(part.function_call.args or {}))
            if hasattr(part, "function_response") and part.function_response:
                total += len(str(part.function_response.response or {}))
    return total


def _compact_old_results(contents: list) -> list:
    """Replace large function responses in older turns with summaries.

    Keeps the first message (system+task) and last 4 messages intact.
    Truncates any function_response content > 10K chars in older messages.
    Returns a new list — does not mutate the original.
    """
    from google.genai import types

    if len(contents) <= 5:
        return contents

    protected = 4  # keep last N messages intact
    compacted = []
    for i, content in enumerate(contents):
        if i == 0 or i >= len(contents) - protected:
            compacted.append(content)
            continue

        # Check if this content has large function responses
        needs_compact = False
        for part in content.parts:
            if hasattr(part, "function_response") and part.function_response:
                resp_str = str(part.function_response.response or {})
                if len(resp_str) > 10_000:
                    needs_compact = True
                    break

        if not needs_compact:
            compacted.append(content)
            continue

        # Rebuild with truncated responses
        new_parts = []
        for part in content.parts:
            if hasattr(part, "function_response") and part.function_response:
                resp_str = str(part.function_response.response or {})
                if len(resp_str) > 10_000:
                    new_parts.append(
                        types.Part.from_function_response(
                            name=part.function_response.name,
                            response={
                                "note": f"[Compacted — original was {len(resp_str)} chars]",
                                "preview": resp_str[:500],
                            },
                        )
                    )
                else:
                    new_parts.append(part)
            else:
                new_parts.append(part)

        compacted.append(types.Content(role=content.role, parts=new_parts))

    return compacted


def _execute_agent_tool(name: str, args: dict, max_read_chars: int = 200_000) -> dict:
    """Execute an agent tool and return the result as a dict.

    Args:
        name: Tool name to execute.
        args: Tool arguments.
        max_read_chars: Max chars for file reads (reduced when context is tight).
    """
    import os
    import glob as glob_module
    import subprocess

    try:
        if name == "read_file":
            file_path = args["file_path"]
            if not os.path.exists(file_path):
                return {"error": f"File not found: {file_path}"}
            size = os.path.getsize(file_path)
            if size > 5_000_000:
                return {"error": f"File too large ({size} bytes). Max 5MB."}
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if len(content) > max_read_chars:
                content = (
                    content[:max_read_chars]
                    + f"\n\n... [truncated at {max_read_chars // 1000}K chars]"
                )
            return {"content": content, "size": size}

        elif name == "list_files":
            directory = args["directory"]
            pattern = args.get("pattern", "*")
            if not os.path.isdir(directory):
                return {"error": f"Directory not found: {directory}"}
            full_pattern = os.path.join(directory, pattern)
            matches = sorted(glob_module.glob(full_pattern))
            entries = []
            for m in matches[:200]:
                if os.path.exists(m):
                    etype = "dir" if os.path.isdir(m) else "file"
                    esize = os.path.getsize(m) if os.path.isfile(m) else 0
                    entries.append({"path": m, "type": etype, "size": esize})
            return {"entries": entries, "count": len(entries)}

        elif name == "search_text":
            pattern = args["pattern"]
            directory = args["directory"]
            file_glob = args.get("file_glob", "")
            if not os.path.isdir(directory):
                return {"error": f"Directory not found: {directory}"}
            cmd = ["grep", "-r", "-i", "-n", pattern, directory]
            if file_glob:
                cmd = ["grep", "-r", "-i", "-n", "--include", file_glob,
                       pattern, directory]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            output = result.stdout
            if len(output) > 100_000:
                output = output[:100_000] + "\n... [truncated]"
            lines = output.strip().split("\n") if output.strip() else []
            return {"matches": lines[:200], "count": len(lines)}

        elif name == "write_file":
            file_path = args["file_path"]
            content = args["content"]
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"status": "success", "file_path": file_path, "size": len(content)}

        elif name == "edit_file":
            file_path = args["file_path"]
            old_string = args["old_string"]
            new_string = args["new_string"]
            replace_all = args.get("replace_all", False)

            if not os.path.exists(file_path):
                return {"error": f"File not found: {file_path}"}

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            count = content.count(old_string)
            if count == 0:
                return {"error": "old_string not found in file"}
            if count > 1 and not replace_all:
                return {
                    "error": (
                        f"old_string found {count} times. "
                        "Set replace_all=true or provide more context."
                    )
                }

            if replace_all:
                new_content = content.replace(old_string, new_string)
                replacements = count
            else:
                new_content = content.replace(old_string, new_string, 1)
                replacements = 1

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return {
                "status": "success",
                "file_path": file_path,
                "replacements": replacements,
            }

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gemini_agent(
    task: str,
    working_dir: str = "/tmp",
    max_turns: int = 0,
    temperature: float = 0.1,
    max_output_tokens: int = 65536,
    enable_search: bool | None = None,
    thinking: bool = True,
    mode: str = "document",
    output_file: str = "",
) -> dict:
    """Run a multi-step Gemini agent with file access tools.

    The agent can read files, list directories, search text, write files,
    and make surgical edits. It runs in an autonomous loop with context
    management — compacting old results when context fills up.

    Two modes:
    - **document** (default): Bulk document processing, curriculum creation,
      multi-file review. System prompt follows GATHER/PLAN/EXECUTE/VERIFY/SUMMARISE
      workflow. Default 15 turns, search off.
    - **research**: Find, evaluate, and synthesize information. Agent externalises
      findings to a scratch file on disk instead of accumulating raw content in
      conversation history. Tighter context thresholds (warn 60%, compact 75%).
      Default 15 turns, search on. Writes synthesis to output_file.

    NOTE: For curriculum deliverable generation, prefer `gemini_generate` with
    Claude-assembled context for better quality and token efficiency.

    Args:
        task: Detailed task description. Be specific about what files to read,
            what to produce, and where to write output.
        working_dir: Base directory context for the agent (informational).
        max_turns: Maximum tool-use rounds before stopping. 0 = use mode
            default (15 for both document and research).
        temperature: Generation temperature (default 0.1 — lower = more precise).
        max_output_tokens: Max tokens per response (default 65536).
        enable_search: Enable Google Search grounding for web research.
            Default: True for research mode, False for document mode.
            Explicit True/False always overrides the mode default.
        thinking: Enable Gemini's native thinking mode for planning (default True).
        mode: Agent mode — "document" (default) or "research".
        output_file: Output file path. Research mode defaults to
            {working_dir}/research-output.md if not set.
    """
    try:
        from google.genai import types

        client = _get_client()
        model = _get_model()

        # Mode-specific defaults
        if mode == "research":
            if max_turns == 0:
                max_turns = 15
            if enable_search is None:
                enable_search = True  # research defaults to web search on
            findings_file = os.path.join(working_dir, "research-findings.md")
            if not output_file:
                output_file = os.path.join(working_dir, "research-output.md")
            os.makedirs(working_dir, exist_ok=True)
            warn_pct = _RESEARCH_WARN_PCT
            compact_pct = _RESEARCH_COMPACT_PCT
        else:
            if max_turns == 0:
                max_turns = 15
            if enable_search is None:
                enable_search = False
            findings_file = ""
            warn_pct = _CONTEXT_WARN_PCT
            compact_pct = _CONTEXT_COMPACT_PCT

        # Web grounding pre-step (research mode only)
        # Runs a separate search-only call before the agent loop, saves
        # results to a file. Avoids combining search + function calling
        # in the same request (unsupported by some models).
        grounding_file = ""
        if mode == "research" and enable_search:
            grounding_file = os.path.join(working_dir, "web-grounding.md")
            try:
                logger.info("Research grounding: searching web for task context")
                grounding_response = client.models.generate_content(
                    model=model,
                    contents=task,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(
                            google_search=types.GoogleSearch(),
                        )],
                        temperature=0.1,
                        max_output_tokens=_DEFAULT_MAX_OUTPUT,
                    ),
                )
                if grounding_response.text:
                    with open(grounding_file, "w", encoding="utf-8") as f:
                        f.write(f"# Web Research Grounding\n\n")
                        f.write(f"Query: {task}\n\n---\n\n")
                        f.write(grounding_response.text)
                    logger.info(
                        "Grounding saved: %s (%d chars)",
                        grounding_file, len(grounding_response.text),
                    )
                if (hasattr(grounding_response, "usage_metadata")
                        and grounding_response.usage_metadata):
                    _log_usage(
                        "gemini_agent_grounding", model,
                        grounding_response.usage_metadata,
                    )
            except Exception as e:
                logger.warning("Web grounding failed (continuing without): %s", e)
                grounding_file = ""

        # Build config — agent loop uses file tools only (no search)
        config_kwargs = {
            "tools": [types.Tool(
                function_declarations=_AGENT_TOOL_DECLARATIONS,
            )],
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if thinking:
            try:
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=8192,
                )
            except Exception:
                logger.warning("ThinkingConfig not supported — falling back to prompt")
        config = types.GenerateContentConfig(**config_kwargs)

        # Select system prompt based on mode
        if mode == "research":
            if grounding_file:
                search_note = (
                    f"Web search results have been saved to "
                    f"{grounding_file} — read this file during GATHER."
                )
            else:
                search_note = "You can only work with local files."
            system_prompt = _RESEARCH_SYSTEM_PROMPT.format(
                working_dir=working_dir,
                search_note=search_note,
                findings_file=findings_file,
                output_file=output_file,
            )
        else:
            system_prompt = _DOCUMENT_SYSTEM_PROMPT.format(working_dir=working_dir)

        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=f"{system_prompt}\n\nTask: {task}")],
            )
        ]

        turns_used = 0
        tool_calls_total = 0
        context_chars = len(system_prompt) + len(task)
        final_text = ""

        # Pre-compute DEVELOP phase start for targeted grounding trigger
        _develop_start = 0
        _grounding_2_done = False
        if mode == "research":
            prev = 0
            for p_name, p_cum_pct, _ in _RESEARCH_PHASES:
                boundary = round(p_cum_pct * max_turns)
                if p_name == "DEVELOP":
                    _develop_start = prev
                    break
                prev = boundary

        for turn in range(max_turns):
            # Context management: compact old results if over threshold
            pct = context_chars / _CONTEXT_BUDGET_CHARS
            if pct > compact_pct:
                logger.info(
                    "Context at %.0f%% — compacting old results", pct * 100,
                )
                contents = _compact_old_results(contents)
                context_chars = _estimate_context_chars(contents)

            response = client.models.generate_content(
                model=model, contents=contents, config=config,
            )

            if hasattr(response, "usage_metadata") and response.usage_metadata:
                _log_usage("gemini_agent", model, response.usage_metadata)

            if not response.candidates:
                final_text = (
                    "[No response from model — content may have been blocked]"
                )
                break

            model_content = response.candidates[0].content
            contents.append(model_content)
            turns_used += 1

            # Update context estimate with model response
            for part in model_content.parts:
                if hasattr(part, "text") and part.text:
                    context_chars += len(part.text)

            # Collect function calls from all parts
            function_calls = [
                part for part in model_content.parts
                if hasattr(part, "function_call") and part.function_call
            ]

            if not function_calls:
                # No tool calls — agent is done; extract text
                for part in model_content.parts:
                    if hasattr(part, "text") and part.text:
                        final_text += part.text
                break

            # Determine read budget based on remaining context and mode
            pct = context_chars / _CONTEXT_BUDGET_CHARS
            if mode == "research":
                if pct > compact_pct:
                    max_read = 30_000   # tight budget
                elif pct > warn_pct:
                    max_read = 60_000   # warn zone
                else:
                    max_read = 100_000  # normal
            else:
                if pct > compact_pct:
                    max_read = 50_000   # tight budget
                elif pct > warn_pct:
                    max_read = 100_000  # warn zone
                else:
                    max_read = 200_000  # normal

            # Execute function calls and send results back
            fn_response_parts = []
            for part in function_calls:
                fc = part.function_call
                tool_args = dict(fc.args) if fc.args else {}
                logger.info("Agent tool: %s(%s)", fc.name, list(tool_args.keys()))
                result = _execute_agent_tool(
                    fc.name, tool_args, max_read_chars=max_read,
                )
                tool_calls_total += 1

                # Track result size in context estimate
                result_str = str(result)
                context_chars += len(result_str)

                fn_response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name, response=result,
                    )
                )

            # Add context warning if approaching budget
            pct = context_chars / _CONTEXT_BUDGET_CHARS
            if pct > warn_pct:
                fn_response_parts.append(
                    types.Part(
                        text=(
                            f"CONTEXT WARNING: You have used ~{pct:.0%} of your "
                            "context budget. Avoid reading additional large files. "
                            "Work with what you have and prioritise writing output."
                        )
                    )
                )

            # Phase-aware status line (research mode only)
            if mode == "research":
                phase_name = _RESEARCH_PHASES[-1][0]
                phase_goal = _RESEARCH_PHASES[-1][2]
                phase_turn = 1
                phase_total = 1
                prev_boundary = 0
                for p_name, p_cum_pct, p_goal in _RESEARCH_PHASES:
                    boundary = round(p_cum_pct * max_turns)
                    if turn < boundary:
                        phase_name = p_name
                        phase_goal = p_goal
                        phase_turn = turn - prev_boundary + 1
                        phase_total = boundary - prev_boundary
                        break
                    prev_boundary = boundary

                # Targeted grounding: at DEVELOP start, search for
                # the specific themes the agent identified in STRUCTURE
                if (enable_search and not _grounding_2_done
                        and turn >= _develop_start
                        and output_file and os.path.exists(output_file)):
                    _grounding_2_done = True
                    try:
                        with open(output_file, "r", encoding="utf-8") as f:
                            headings = f.read()[:2000]
                        if headings.strip():
                            targeted_query = (
                                f"For each of these themes, find evidence, "
                                f"best practices, and examples:\n\n{headings}"
                            )
                            logger.info("Targeted grounding: searching "
                                        "web for structured themes")
                            tg_response = client.models.generate_content(
                                model=model,
                                contents=targeted_query,
                                config=types.GenerateContentConfig(
                                    tools=[types.Tool(
                                        google_search=types.GoogleSearch(),
                                    )],
                                    temperature=0.1,
                                    max_output_tokens=_DEFAULT_MAX_OUTPUT,
                                ),
                            )
                            if tg_response.text:
                                tg_file = os.path.join(
                                    working_dir,
                                    "web-grounding-targeted.md",
                                )
                                with open(tg_file, "w", encoding="utf-8") as f:
                                    f.write("# Targeted Web Research\n\n")
                                    f.write(f"Query themes from: "
                                            f"{output_file}\n\n---\n\n")
                                    f.write(tg_response.text)
                                logger.info(
                                    "Targeted grounding saved: %s (%d chars)",
                                    tg_file, len(tg_response.text),
                                )
                                fn_response_parts.append(
                                    types.Part(
                                        text=(
                                            f"NEW SOURCE: Targeted web "
                                            f"research for your themes has "
                                            f"been saved to {tg_file}. "
                                            f"Read it to enrich your "
                                            f"DEVELOP analysis."
                                        )
                                    )
                                )
                            if (hasattr(tg_response, "usage_metadata")
                                    and tg_response.usage_metadata):
                                _log_usage(
                                    "gemini_agent_grounding", model,
                                    tg_response.usage_metadata,
                                )
                    except Exception as e:
                        logger.warning(
                            "Targeted grounding failed (continuing): %s", e,
                        )

                fn_response_parts.append(
                    types.Part(
                        text=(
                            f"PHASE: {phase_name} (turn {phase_turn} of "
                            f"{phase_total}). Goal: {phase_goal}"
                        )
                    )
                )

            contents.append(types.Content(role="user", parts=fn_response_parts))

        else:
            # Exhausted max_turns
            final_text = f"[Agent stopped after {max_turns} turns]"
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        final_text += "\n" + part.text

        result = {
            "model": model,
            "mode": mode,
            "turns_used": turns_used,
            "tool_calls": tool_calls_total,
            "context_used_pct": f"{context_chars / _CONTEXT_BUDGET_CHARS:.0%}",
            "result": final_text,
        }

        # Include output file content if it exists (research mode writes here)
        if output_file and os.path.exists(output_file):
            result["output_file"] = output_file
            with open(output_file, "r", encoding="utf-8", errors="replace") as f:
                result["output_content"] = f.read()[:50_000]

        return result

    except Exception as e:
        logger.exception("gemini_agent failed")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

# Available image models (checked via ListModels Feb 2026)
_IMAGE_MODELS = {
    "gemini-2.5-flash-image",       # Gemini native — text+image multimodal
    "gemini-3-pro-image-preview",   # Gemini native — advanced reasoning
    "imagen-4.0-generate-001",      # Imagen 4 — dedicated image model
    "imagen-4.0-ultra-generate-001",
    "imagen-4.0-fast-generate-001",
}

_IMAGEN_MODELS = {
    "imagen-4.0-generate-001",
    "imagen-4.0-ultra-generate-001",
    "imagen-4.0-fast-generate-001",
}

# Pricing per million tokens (image output counted in tokens)
_IMAGE_PRICING = {
    "gemini-2.5-flash-image": {"input": 0.15, "output": 0.60},
    "gemini-3-pro-image-preview": {"input": 1.25, "output": 5.00},
    "imagen-4.0-generate-001": {"per_image": 0.04},
    "imagen-4.0-ultra-generate-001": {"per_image": 0.08},
    "imagen-4.0-fast-generate-001": {"per_image": 0.02},
}


@mcp.tool()
def gemini_image(
    prompt: str,
    output_file: str = "/tmp/gemini-image.png",
    model: str = "gemini-2.5-flash-image",
    aspect_ratio: str = "16:9",
    number_of_images: int = 1,
    reference_image: str = "",
) -> dict:
    """Generate images using Gemini or Imagen models.

    Two model families available:
    - **Gemini** (gemini-2.5-flash-image, gemini-3-pro-image-preview):
      Multimodal — can accept reference images as input, returns text+image.
      Good for: described scenes, illustrations, covers with specific text.
    - **Imagen 4** (imagen-4.0-generate-001, imagen-4.0-ultra-generate-001,
      imagen-4.0-fast-generate-001): Dedicated image model. Higher quality
      photorealistic output. No reference image input.

    Args:
        prompt: Image description. Be specific about style, composition,
            colors, and mood.
        output_file: Where to save the generated image. For multiple images,
            files are numbered (e.g. image-1.png, image-2.png). Default:
            /tmp/gemini-image.png
        model: Model to use. Default: gemini-2.5-flash-image.
        aspect_ratio: Aspect ratio — "1:1", "3:4", "4:3", "9:16", "16:9".
            Default: 16:9.
        number_of_images: Number of images to generate (1-4). Imagen models
            only — Gemini always returns 1.
        reference_image: Path to a local image file to use as reference input.
            Gemini models only — Imagen does not support reference images.
    """
    try:
        from google import genai
        from google.genai import types

        if model not in _IMAGE_MODELS:
            return {
                "error": f"Unknown image model: {model}. "
                f"Available: {', '.join(sorted(_IMAGE_MODELS))}"
            }

        client = _get_client()
        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

        # Route to the right API based on model family
        if model in _IMAGEN_MODELS:
            # --- Imagen 4: dedicated generate_images API ---
            config = types.GenerateImagesConfig(
                number_of_images=number_of_images,
                aspect_ratio=aspect_ratio,
                output_mime_type="image/png",
            )

            response = client.models.generate_images(
                model=model,
                prompt=prompt,
                config=config,
            )

            if not response.generated_images:
                return {"error": "No images generated (may have been filtered by safety)"}

            saved_files = []
            base, ext = os.path.splitext(output_file)
            for i, gen_img in enumerate(response.generated_images):
                if len(response.generated_images) == 1:
                    path = output_file
                else:
                    path = f"{base}-{i + 1}{ext}"
                gen_img.image.save(path)
                saved_files.append({
                    "path": path,
                    "size_bytes": os.path.getsize(path),
                })

            # Log cost (flat per-image pricing)
            per_image = _IMAGE_PRICING.get(model, {}).get("per_image", 0.04)
            cost = per_image * len(response.generated_images)
            try:
                _init_usage_db()
                conn = sqlite3.connect(_USAGE_DB)
                conn.execute(
                    "INSERT INTO usage (timestamp, tool_name, model, "
                    "input_tokens, output_tokens, thinking_tokens, "
                    "total_tokens, estimated_cost_usd) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        "gemini_image", model, 0, 0, 0, 0,
                        round(cost, 6),
                    ),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning("Failed to log image usage: %s", e)

            return {
                "model": model,
                "images_generated": len(response.generated_images),
                "files": saved_files,
                "estimated_cost_usd": round(cost, 6),
            }

        else:
            # --- Gemini native: generate_content with IMAGE modality ---
            contents = []

            # Add reference image if provided
            if reference_image:
                if not os.path.exists(reference_image):
                    return {"error": f"Reference image not found: {reference_image}"}

                ext_lower = os.path.splitext(reference_image)[1].lower()
                mime_map = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".bmp": "image/bmp",
                }
                mime_type = mime_map.get(ext_lower, "image/png")

                with open(reference_image, "rb") as f:
                    image_bytes = f.read()

                contents.append(
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                )

            contents.append(prompt)

            config = types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            )

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )

            # Track usage
            usage = {}
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage = _log_usage("gemini_image", model, response.usage_metadata)

            if not response.parts:
                return {"error": "No response parts (may have been filtered by safety)"}

            saved_files = []
            text_parts = []
            base, ext = os.path.splitext(output_file)
            img_idx = 0

            for part in response.parts:
                if part.text is not None:
                    text_parts.append(part.text)
                elif part.inline_data is not None:
                    if img_idx == 0:
                        path = output_file
                    else:
                        path = f"{base}-{img_idx + 1}{ext}"
                    img = part.as_image()
                    img.save(path)
                    saved_files.append({
                        "path": path,
                        "size_bytes": os.path.getsize(path),
                    })
                    img_idx += 1

            if not saved_files:
                return {
                    "error": "No image in response",
                    "text": " ".join(text_parts) if text_parts else None,
                }

            return {
                "model": model,
                "images_generated": len(saved_files),
                "files": saved_files,
                "text": " ".join(text_parts) if text_parts else None,
                "usage": usage,
            }

    except Exception as e:
        logger.exception("gemini_image failed")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Usage reporting tool
# ---------------------------------------------------------------------------

@mcp.tool()
def gemini_usage(period: str = "today") -> dict:
    """Query Gemini API usage and estimated costs.

    Args:
        period: One of "today", "week", "month", "all".
    """
    try:
        _init_usage_db()
        conn = sqlite3.connect(_USAGE_DB)
        conn.row_factory = sqlite3.Row

        # Date filter
        if period == "today":
            date_filter = "DATE(timestamp) = DATE('now')"
        elif period == "week":
            date_filter = "timestamp >= datetime('now', '-7 days')"
        elif period == "month":
            date_filter = "timestamp >= datetime('now', '-30 days')"
        else:
            date_filter = "1=1"

        # Summary by tool
        rows = conn.execute(f"""
            SELECT tool_name, model,
                   COUNT(*) as calls,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(thinking_tokens) as thinking_tokens,
                   SUM(total_tokens) as total_tokens,
                   SUM(estimated_cost_usd) as cost
            FROM usage
            WHERE {date_filter}
            GROUP BY tool_name, model
            ORDER BY cost DESC
        """).fetchall()

        by_tool = [dict(r) for r in rows]

        # Grand totals
        totals = conn.execute(f"""
            SELECT COUNT(*) as total_calls,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(thinking_tokens) as total_thinking,
                   SUM(total_tokens) as total_tokens,
                   SUM(estimated_cost_usd) as total_cost
            FROM usage
            WHERE {date_filter}
        """).fetchone()

        # Daily breakdown (last 7 days)
        daily = conn.execute("""
            SELECT DATE(timestamp) as date,
                   COUNT(*) as calls,
                   SUM(estimated_cost_usd) as cost
            FROM usage
            WHERE timestamp >= datetime('now', '-7 days')
            GROUP BY DATE(timestamp)
            ORDER BY date DESC
        """).fetchall()

        conn.close()

        return {
            "period": period,
            "by_tool": by_tool,
            "totals": dict(totals) if totals else {},
            "daily_last_7": [dict(d) for d in daily],
        }

    except Exception as e:
        logger.exception("gemini_usage query failed")
        return {"error": str(e)}


@mcp.tool()
def gemini_vision(
    image_paths: list[str],
    prompt: str,
    model: str = "",
    temperature: float = 0.2,
    max_output_tokens: int = 8192,
) -> dict:
    """Analyze one or more images with Gemini's vision capabilities.

    Send local image files to Gemini for analysis, description, OCR,
    comparison, bug identification, UI review, or any visual task.

    Args:
        image_paths: List of absolute paths to image files. Supports
            PNG, JPEG, GIF, WebP, BMP. Max ~20 images per call.
        prompt: Instructions for how to analyze the images. Be specific
            about what you're looking for (e.g. "Describe the UI bug shown",
            "Extract all text from this screenshot", "Compare these two designs").
        model: Gemini model to use. Default: configured GEMINI_MODEL.
        temperature: Generation temperature (0.0-2.0). Lower = more precise.
        max_output_tokens: Maximum output tokens (default 8192).
    """
    try:
        from google.genai import types

        if not image_paths:
            return {"error": "No image paths provided"}

        if not prompt:
            return {"error": "Prompt is required"}

        # Validate and load images
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }

        contents = []
        total_bytes = 0
        for i, path in enumerate(image_paths):
            if not os.path.exists(path):
                return {"error": f"Image not found: {path}"}

            ext = os.path.splitext(path)[1].lower()
            mime_type = mime_map.get(ext)
            if not mime_type:
                return {
                    "error": f"Unsupported image format '{ext}' for {path}. "
                    f"Supported: {', '.join(sorted(mime_map))}"
                }

            with open(path, "rb") as f:
                image_bytes = f.read()

            total_bytes += len(image_bytes)
            if len(image_paths) > 1:
                contents.append(types.Part.from_text(
                    text=f"--- Image {i + 1}: {os.path.basename(path)} ---"
                ))
            contents.append(
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            )

        contents.append(types.Part.from_text(text=prompt))

        client = _get_client()
        use_model = model or _get_model()

        response = client.models.generate_content(
            model=use_model,
            contents=contents,
            config=types.GenerateContentConfig(
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        # Track usage
        usage = {}
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = _log_usage("gemini_vision", use_model, response.usage_metadata)

        text = response.text if response.text else ""

        return {
            "model": use_model,
            "images_analyzed": len(image_paths),
            "total_image_bytes": total_bytes,
            "analysis": text,
            **usage,
        }

    except Exception as e:
        logger.exception("gemini_vision failed")
        return {"error": str(e)}
