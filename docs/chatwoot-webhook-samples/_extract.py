#!/usr/bin/env python3
"""Extract one sanitised sample per Chatwoot webhook event from the
webhook.site raw dump. Writes 01_..json through NN_..json plus a
fields summary table for the README. Sanitisation rules:
    - admin email  -> agent@example.com
    - admin name   -> Admin Agent
    - admin avatar URL -> https://example.com/avatar
    - test contact email -> contact@example.com
Real IDs, timestamps, account/inbox structure preserved (they're how
the adapter routes).
"""
from __future__ import annotations

import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
RAW = HERE / "raw-webhook-site-dump.json"

# Distinguishing rules per event — we want one of each kind.
WANTED = [
    ("01_contact_created.json", lambda b: b.get("event") == "contact_created"),
    ("02_conversation_created.json", lambda b: b.get("event") == "conversation_created"),
    ("03_message_created_incoming.json", lambda b: b.get("event") == "message_created" and b.get("message_type") == "incoming"),
    ("04_message_created_outgoing.json", lambda b: b.get("event") == "message_created" and b.get("message_type") == "outgoing"),
    ("05_contact_updated.json", lambda b: b.get("event") == "contact_updated"),
    ("06_conversation_updated.json", lambda b: b.get("event") == "conversation_updated"),
    ("07_conversation_status_changed.json", lambda b: b.get("event") == "conversation_status_changed"),
]

REDACT = [
    ("ethan+chatwoot@verixiom.com", "agent@example.com"),
    ("roost-test@example.com",      "contact@example.com"),
]


def scrub(text: str) -> str:
    for needle, repl in REDACT:
        text = text.replace(needle, repl)
    # Strip Gravatar URLs that derive from the email
    text = re.sub(r"https?://[^\"]*gravatar[^\"]*", "https://example.com/avatar", text)
    return text


def main() -> None:
    raw = json.loads(RAW.read_text())
    items = raw.get("data", [])
    # webhook.site returns newest-first; reverse for chronological order
    items = list(reversed(items))

    bodies: list[dict] = []
    for item in items:
        content = item.get("content", "")
        if not content:
            continue
        try:
            body = json.loads(content)
        except json.JSONDecodeError:
            continue
        bodies.append(body)

    print(f"loaded {len(bodies)} json bodies from {len(items)} requests")

    summary_rows: list[tuple[str, str, list[str]]] = []
    for fname, pred in WANTED:
        match = next((b for b in bodies if pred(b)), None)
        if match is None:
            print(f"!! no match for {fname}")
            continue
        scrubbed_json = json.loads(scrub(json.dumps(match)))
        out = HERE / fname
        out.write_text(json.dumps(scrubbed_json, indent=2, sort_keys=False) + "\n")
        keys = sorted(scrubbed_json.keys())
        summary_rows.append((fname, scrubbed_json.get("event", "?"), keys))
        print(f"wrote {fname}  event={scrubbed_json.get('event')}  top_keys={len(keys)}")

    summary_md = HERE / "_field_summary.md"
    with summary_md.open("w") as f:
        f.write("# Top-level keys per event (Chatwoot 4.14.1)\n\n")
        f.write("| file | event | top-level keys |\n|---|---|---|\n")
        for fname, evt, keys in summary_rows:
            f.write(f"| `{fname}` | `{evt}` | `{', '.join(keys)}` |\n")
    print(f"wrote {summary_md.name}")


if __name__ == "__main__":
    main()
