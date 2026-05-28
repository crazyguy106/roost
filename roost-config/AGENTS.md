# AGENTS.md — Brief for AI CLIs working inside `roost-config/`

You are an AI coding assistant (Claude Code, Gemini CLI, Codex, or similar)
helping a non-engineer customise their Roost installation. This file is
your full briefing. Read it once before you touch anything in this
directory.

If the user asks you "what is this directory?" or "how do I customise
Roost?", the answer lives here. Don't guess.

---

## 1. What Roost is (90 seconds)

Roost is the user's **personal AI ops assistant**. It runs as a small
service (Docker container) on their own server. It does three things:

1. **Listens** on messaging channels (WhatsApp, Telegram, email) for
   inbound leads / clients.
2. **Classifies** every inbound message through an "AI CDR" (Content
   Disarm & Reconstruct) pipeline — a tool-less Gemini sandbox that
   extracts `intent`, `urgency`, and structured fields, defending against
   prompt injection.
3. **Acts** by enrolling the contact in a *cadence* (a scheduled multi-
   day follow-up sequence), asking *qualifying questions* back over the
   channel, scoring the answers, and routing hot leads to the operator
   via Telegram.

The user is most likely a **financial advisor, property agent, insurance
broker, or other relationship-driven SME operator** in Singapore. They
are not a software engineer. They want to teach the system to handle
their specific clientele and tone.

Roost's "core" is fixed (the message pipeline, scheduler, scoring engine,
Guardian safety gate). What lives in **this directory** is everything
the user is *meant* to change.

---

## 2. What this directory is

`roost-config/` is the user's **personal override layer**. Files here
take precedence over the equivalent files Roost ships under
`roost/extras/lead_nurture/services/cadences/library/` and the
in-code `QUESTIONS_BY_CADENCE` dict.

```
roost-config/
├── AGENTS.md              ← you are here
├── README.md              ← human-facing intro
├── settings.yaml          ← cross-cutting runtime tunables (debouncer, default vertical, debug)
├── cadences/              ← multi-day follow-up sequences (YAML)
├── question-packs/        ← qualifying questions per cadence (YAML)
├── templates/             ← reusable message bodies (YAML)
└── examples/              ← read-only reference copies + annotated samples
```

**The user edits files in `cadences/`, `question-packs/`, `templates/`.**
**They never edit `examples/` (read-only) and rarely edit this file.**

Reloads are picked up live — no container restart needed.

---

## 3. Your job when invoked here

The user will say things like:

- *"Make this cadence sound more like me — I'm casual, I use SG slang, my
  clients are mid-30s professionals."*
- *"Add a question about CPF balance after the household one."*
- *"Change the day-3 follow-up to day-2, and shorten it."*
- *"My clients are first-time HDB buyers, not condo investors — rewrite
  the keywords."*

Your job is to:

1. **Read first.** Always read the relevant file in
   `roost-config/examples/` to see the schema, then read whatever exists
   in the equivalent live directory (`cadences/`, `question-packs/`)
   before editing. Never write from memory.
2. **Edit minimally.** Change only what the user asked for. Preserve
   structure, comments, ordering.
3. **Stay in YAML.** Don't touch Python code. If a change needs Python,
   say so and stop — that's a job for the engineer.
4. **Validate before declaring done.** Run `python3 -m roost.config_check`
   (see §7) if it exists; otherwise paste the diff and ask the user to
   confirm it reads well.
5. **Sign as the user.** When templates include a `{{advisor_signoff}}`
   placeholder or similar, ask what they want there if it's missing.

---

## 4. Schema cheatsheet

### Cadence (`cadences/<slug>.yaml`)

```yaml
slug: my_cadence              # stable id, snake_case, no spaces
name: Human-readable Name
description: |
  What this cadence is for.
vertical: financial_advisor   # property | financial_advisor | insurance | sme | other
enabled: true

templates:
  - name: t_day0              # local id, referenced by `steps[].template`
    sequence_day: 0           # informational
    category: greeting        # greeting | qualification | nurture | follow_up
    channel: any              # any | email | whatsapp | telegram
    subject: "..."
    body: |
      Multiline body.
      Placeholders: {{first_name}}, {{advisor_name}}, {{advisor_firm}},
      {{advisor_signoff}}, {{advisor_booking_link}}.

steps:
  - day_offset: 0             # days after enrolment
    minute_offset: 5          # optional fine-grain delay
    hour: 10                  # local-time send (uses `tz`)
    tz: Asia/Singapore
    template: t_day0
    channel: email            # delivery channel for this step
```

### Question pack (`question-packs/<name>.yaml`)

```yaml
cadence_slug: my_cadence      # binds this pack to that cadence
enabled: true

questions:
  - key: timeline             # slug; becomes a field name in answers
    question: |               # exact text sent to the lead
      When are you hoping to...?
    weight: 0.4               # relative score contribution
    hot_keywords: [list]      # any substring → +weight * 1.0
    warm_keywords: [list]     # any substring → +weight * 0.5
```

Scoring is keyword-only, case-insensitive substring match. No NLP.
Weights are normalised at scoring time (don't need to sum to 1.0).

See `examples/financial_advisor_intro.yaml` and
`examples/question-pack.example.yaml` for fully annotated working files.

---

## 5. Tone guidance

Roost users are not marketers. **Match their voice, don't impose one.**
When in doubt:

- Singapore English defaults: "HDB", "condo", "CPF", "IPA", "MAS",
  "fact-find" are normal. Don't translate them out.
- Avoid US sales-y phrasing ("Hey there!", "absolutely!", "stoked",
  emoji walls). Most SG advisors find this off-putting to local clients.
- Short paragraphs. WhatsApp is read on phones; nobody scrolls a wall
  of text.
- One question per message in question packs.
- Use the user's own name and firm — never invent placeholder identities
  like "John Smith, Acme Financial".

If the user gives you a sample of their own writing (an email they sent,
a WhatsApp screenshot), mirror that. Mirror cadence, mirror vocabulary.

---

## 6. SG regulatory notes (do not invent advice)

Roost users in regulated verticals must obey:

- **MAS (Monetary Authority of Singapore)** — financial advisors must
  conduct a **fact-find** before any product recommendation. Cadences
  must not push a specific product (insurance plan, fund, mortgage
  package) in the first few touches. Generic information, scheduling a
  meeting, and asking discovery questions are fine.
- **CEA (Council for Estate Agencies)** — property agents must complete
  CDD (customer due diligence) before transacting. Cadences may ask for
  basic info; full ID screening happens through Roost's CDD flow, not
  here.
- **PDPA s.43 / Spam Control Act** — never send marketing SMS / WhatsApp
  to a number that hasn't consented. The cadence engine respects
  unsubscribe (STOP keyword) automatically; do not add prompts asking
  users to consent retroactively.

If the user asks for copy that would violate any of these, say so and
suggest an alternative. Don't refuse silently.

---

## 7. Validating your edits

Roost ships validation commands. Run them after every edit:

```bash
# Inside the Roost container or repo root:
python3 -m roost.config_check        # YAML schema + cross-references
python3 -m roost.config_check --dry-run cadence financial_advisor_intro
                                     # simulates one enrolment, prints
                                     # the schedule that would run
```

If these commands don't exist yet in this installation, fall back to:

```bash
python3 -c "import yaml; yaml.safe_load(open('roost-config/cadences/<file>.yaml'))"
```

…and show the user the diff before declaring done.

---

## 8. Hot-reload behaviour

Roost watches `roost-config/` and reloads on file change. You do **not**
need to restart the container. The poller checks every 3 seconds, so
edits land within a few seconds. Two caveats:

1. A YAML parse error logs a warning and keeps the previous version
   live. Check container logs (`docker logs roost --tail 20`) after a
   save to confirm the new version loaded.
2. Active enrolments keep running on the version of the cadence that
   was live when they started. Edits affect *new* enrolments only.

## 9. Inbound message debouncing

When a lead pings on WhatsApp / WeChat they often send a single thought
across three or four fragments. Roost buffers those fragments and waits
until the typer pauses before classifying. This means:

- The first auto-reply (qualification question, etc.) lands **after** a
  short delay (default ~20s). That's by design — the alternative is
  Roost answering "hi" with a budget question.
- STOP / HELP keywords **bypass** the buffer — those responses are
  always immediate.
- The delay is tunable in `settings.yaml` under `fragmented_messages`.
  Lowering `debounce_seconds` to 5-10s feels snappier for demos but
  raises mis-classification risk on real fragmented messages.

If the operator asks "why is Roost slow to reply to the first message?"
— this is the answer. Don't shorten the debounce without a reason.

---

## 10. When you're stuck

- The full Roost docs live in the repo at `/docs/` — `lead-nurture.md`,
  `whatsapp-adapter.md`, `aml-screening.md` are the most relevant.
- The library cadence (untouched ground truth) lives at
  `roost/extras/lead_nurture/services/cadences/library/`.
- Question pack source lives in
  `roost/extras/lead_nurture/services/qualification.py`
  (`QUESTIONS_BY_CADENCE` dict).
- If the user asks you to change something that isn't a cadence, a
  question pack, or a template — say so and stop. That edit belongs
  in the engineer's codebase, not this directory.

---

## 11. One-liner summary you can show the user

> *"This directory is where you teach Roost about your clients and your
> voice. I edit the YAML files; the running system picks up changes
> within seconds. I never touch the Python code — that's the engine."*
