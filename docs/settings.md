# Settings, Credentials & Setup Wizard

How to configure Roost — from first-time setup to ongoing credential management.

**Last updated:** 2026-03-19

---

## 1. Setup Wizard (`roost-onboard`)

The interactive CLI wizard is the fastest way to get Roost running. It generates a `.env` file and starts Docker.

```bash
roost-onboard
# or: roost-cli onboard
```

### Steps

1. **AI Provider** — Choose Gemini (free with Google account), Claude, OpenAI, or Ollama (self-hosted). Enter the API key (or Ollama URL).
2. **Telegram Bot** — Paste your bot token from @BotFather. The wizard validates it against the Telegram API and shows the bot username. Enter your Telegram user ID to restrict access.
3. **Web Credentials** — Set an admin username and password for the web dashboard.
4. **Launch** — The wizard generates `.env` with 0600 permissions, auto-generates a SESSION_SECRET, and runs `docker compose build && docker compose up -d`.

### What It Creates

| File | Permissions | Contents |
|------|-------------|----------|
| `.env` | `0600` | All secrets, feature flags, credentials |

The `.env` includes: `SESSION_SECRET` (auto-generated 32-byte hex), `AGENT_PROVIDER`, AI key, Telegram token/user ID, web username/password, and feature flags (`AI_ENABLED`, `TELEGRAM_ENABLED`, `GOOGLE_ENABLED`, `MS_ENABLED`).

### Classroom Use

The wizard is designed as a guided classroom exercise. An instructor walks students through each step, explaining:
- Why `.env` uses 0600 permissions (secret protection)
- What SESSION_SECRET does (credential encryption key derivation)
- Why Docker isolation matters (container as sandbox)
- How feature flags control the attack surface (principle of least privilege)

---

## 2. Settings Page (`/settings`)

After deployment, manage integrations, flags, and agent personality from the web UI.

### Integrations Tab

View and manage credentials for 7 integrations:

| Integration | Credentials | Test Method |
|-------------|------------|-------------|
| **Gemini AI** | `GEMINI_API_KEY` | Lists available models |
| **Claude AI** | `CLAUDE_API_KEY` | Validates against Anthropic API |
| **OpenAI** | `OPENAI_API_KEY` | Validates against OpenAI API |
| **Telegram Bot** | `TELEGRAM_BOT_TOKEN` | Calls `getMe`, shows bot username |
| **Google Workspace** | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | — |
| **Microsoft 365** | `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID` | — |
| **Notion** | `NOTION_API_TOKEN` | Calls `users/me`, shows workspace name |

Each integration shows:
- **Status badge** — "Configured" (green) or "Not Configured" (grey)
- **Masked credentials** — first 4 and last 4 characters visible (e.g., `sk-a••••3456`)
- **Save button** — stores the credential encrypted
- **Test button** — validates the credential against the live API

### Feature Flags Tab

Toggle features on/off without restarting. Changes are stored in the database and override `.env` defaults.

Available flags: AI, Telegram, Google, Microsoft, Gmail, Notion, Curriculum.

### Personality Tab

Edit the CAGE personality — a free-text description of how the AI agent should sound. Stored in user preferences and injected into the agent's system prompt via the CAGE framework.

Maximum 1000 characters. Examples:
- "Direct, concise, slightly formal. No emojis."
- "Warm and encouraging. Use bullet points. Explain technical terms."

### Charter Tab

Edit the agent's core identity and provider-specific tuning. The charter lives in `data/charter/` as markdown files:

```
data/charter/
├── charter.md       ← Core identity (all providers)
├── gemini.md        ← Gemini-specific behavioral tuning
├── claude.md        ← Claude-specific tuning
└── openai.md        ← OpenAI-specific tuning
```

**How it layers:** Charter (core identity) + Provider tuning + CAGE dynamic context (preferences, tasks, calendar) = the system prompt each AI provider receives.

The charter is the deep structural definition. The personality preference (Personality tab) is the lightweight runtime override. Both are active simultaneously.

---

## 3. Credential Encryption

All credentials stored via the settings page are encrypted at rest.

### How It Works

1. `SESSION_SECRET` (from `.env`) is hashed with SHA-256 to produce a 32-byte key
2. The key is base64-encoded to create a Fernet key (AES-128-CBC with HMAC)
3. Each credential is encrypted with `Fernet.encrypt()` before storage
4. Stored in the `user_settings` table with a `credential:` key prefix
5. Decrypted on retrieval with `Fernet.decrypt()`

### Security Properties

- **At rest:** Credentials are encrypted in SQLite, not plaintext
- **Key binding:** If `SESSION_SECRET` changes, all stored credentials become unreadable (by design)
- **Per-user isolation:** Credentials are scoped by `user_id` — each user's keys are separate
- **Admin-only access:** Only users with `admin` or `owner` roles can save/delete credentials
- **Masked display:** The UI only shows first 4 + last 4 characters, never the full value

### Implementation

- Service: `roost/services/credentials.py`
- API: `roost/web/api_settings.py`
- Storage: `user_settings` table (shared with other user preferences)
- Dependency: `cryptography` library (Fernet)

---

## 4. Settings API Reference

All endpoints require authentication. Credential management requires admin/owner role.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings/integrations` | List all integrations with credential status |
| POST | `/api/settings/credential/{key}` | Store an encrypted credential |
| DELETE | `/api/settings/credential/{key}` | Remove a stored credential |
| POST | `/api/settings/test/{integration}` | Test an integration's credentials |
| POST | `/api/settings/flag/{flag_name}` | Toggle a feature flag on/off |
| GET | `/api/settings/personality` | Get current personality text |
| POST | `/api/settings/personality` | Save personality text |
| GET | `/api/settings/charter` | Get all charter files (core + providers) |
| POST | `/api/settings/charter` | Save core charter.md (admin) |
| POST | `/api/settings/charter/{provider}` | Save provider charter (admin) |

### Example: Store a Credential

```bash
curl -X POST http://localhost:8080/api/settings/credential/GEMINI_API_KEY \
  -H "Content-Type: application/json" \
  -u admin:password \
  -d '{"value": "AIzaSy-your-key-here"}'
```

Response:
```json
{
  "ok": true,
  "key": "GEMINI_API_KEY",
  "masked": "AIza••••here",
  "restart_needed": true
}
```

### Example: Test a Connection

```bash
curl -X POST http://localhost:8080/api/settings/test/telegram \
  -u admin:password
```

Response:
```json
{
  "ok": true,
  "detail": "Connected to @my_roost_bot"
}
```
