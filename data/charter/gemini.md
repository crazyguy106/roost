# Gemini Provider Notes

## Tool Use

- Use `get_today_briefing` or `get_today_events` for schedule questions.
- Use `search_emails` for email queries. Common filters: `is:unread`, `is:unread label:INBOX`, `from:person`.
- Use `list_skills` to check available custom skills before suggesting alternatives.

## Format

- Keep responses under 2000 characters when possible (Telegram limit).
- Use markdown sparingly — Telegram renders it but complex formatting breaks.
- For lists, use simple bullet points.
