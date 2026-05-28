# `roost-config/` — Your personal Roost configuration

This is where **you** teach Roost about your business: your tone, your
clients, the questions you'd ask a new lead, the follow-up schedule you
want.

You don't need to write code. You write YAML — a simple text format —
and Roost picks up your changes within seconds.

The intended way to edit these files is with an AI coding assistant
(Claude Code, Gemini CLI, ChatGPT, Codex) sitting next to you. The
file [`AGENTS.md`](AGENTS.md) in this directory is a briefing those
assistants read automatically. It tells them what Roost is, what each
file means, and how to validate edits — so you can speak to the AI in
plain English and trust the result.

---

## Layout

| Folder | What goes here |
|---|---|
| `cadences/` | Multi-day follow-up sequences. One file per cadence. |
| `question-packs/` | The 3-5 qualifying questions Roost asks a new lead. |
| `templates/` | Reusable message bodies (email / WhatsApp). |
| `examples/` | Read-only working examples. Study them, don't edit. |

---

## Getting started

1. Open this folder in your AI assistant.
2. Look at `examples/financial_advisor_intro.yaml` and
   `examples/question-pack.example.yaml`. They have inline comments
   explaining every field.
3. Ask your assistant: *"Copy the financial advisor example into the
   `cadences/` folder and rewrite it to sound like me — I'm [your
   description]. My clients are [your audience]."*
4. The assistant edits the file. You read the result. If it's wrong,
   say so — it'll redo it.
5. Save. Roost reloads automatically.

---

## What you should never do

- Edit anything outside this directory unless you're the engineer.
- Send marketing messages to people who haven't given consent — the
  STOP keyword unsubscribes them and that's permanent.
- Change financial / property advice in the templates unless you're a
  licensed practitioner. The defaults are deliberately conservative.

---

## Need help?

Ask your AI assistant. It knows what this directory is for — `AGENTS.md`
is its briefing. If something is genuinely broken (Roost won't load,
errors keep appearing), that's an engineer problem, not a YAML problem.
