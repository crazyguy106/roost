# Roost — What It Costs

What you'll actually pay to run Roost. All prices in **SGD** (1 USD = 1.34 SGD, April 2026). Roost itself is free and open-source (MIT license).

**Last updated:** 2026-04-13

---

## Upfront Costs (One-Time)

| Item | Cost (SGD) | Notes |
|------|-----------|-------|
| Domain name (.com) | $13-20/yr | Annual registration |
| Google Cloud project | $0 | Needed for OAuth consent screen |
| Meta Business account | $0 | Needed for WhatsApp Cloud API |
| Telegram bot token | $0 | Free from @BotFather |
| Notion API token | $0 | Free |
| Roost software | $0 | MIT license, forever |

**Total upfront: ~$15-20 SGD** (just the domain)

---

## Monthly Costs

### Infrastructure (required)

| Item | SGD/mo | Notes |
|------|--------|-------|
| VPS (Hetzner CX22, 2vCPU/4GB) | ~$8 | EUR 4.49/mo (Apr 2026 pricing) |
| Roost Lite on your laptop | $0 | No VPS needed, localhost only |

### AI Coding Agent (pick one)

The AI agent is the core of Roost. This is where most of the cost goes.

**Claude (recommended)**

| Plan | SGD/mo | Codex/Agent Access | Notes |
|------|--------|---------------------|-------|
| Claude Pro | $27 | Limited Claude Code | Basic — will hit limits quickly |
| **Claude Max 5x** | **$134** | **Full Claude Code** | **Recommended for daily use** |
| Claude Max 20x | $268 | Full Claude Code | Heavy/professional use |
| Claude Team | $34-40/user | Full Claude Code | Per-seat, annual billing |

**OpenAI**

| Plan | SGD/mo | Codex Access | Notes |
|------|--------|--------------|-------|
| ChatGPT Plus | $27 | Codex included (capped) | Basic Codex access |
| **ChatGPT Pro** | **$134** | **5x Codex usage** | **New Apr 2026 — mid-tier for coders** |
| ChatGPT Pro (Ultra) | $268 | Unlimited Codex | Top tier, unlimited everything |
| ChatGPT Team | $34-40/user | Codex included | Per-seat, min 2 users |
| ChatGPT Business | $40/user | Codex included | Admin controls, no training |

**Budget options**

| Plan | SGD/mo | Notes |
|------|--------|-------|
| Gemini API (free tier) | $0 | 1,000 req/day — viable for light use, no coding agent |
| OpenAI Go | $11 | Minimal tier, limited features |
| Ollama (local) | $0 | Needs own GPU (8GB+ VRAM), fully offline |

### Google Workspace (recommended)

Needed for Gmail, Google Calendar, and Google Drive integration. Priced per user.

| Plan | SGD/mo | What You Get |
|------|--------|--------------|
| Personal Google account | $0 | OAuth works but limited API quotas |
| **Business Starter** | **$9.40/user** | **Custom email, 30GB Drive, Calendar** |
| Business Standard | $18.80/user | 2TB Drive, recording, shared drives |

### Microsoft 365 (optional)

For Outlook, Teams, OneDrive, SharePoint, Excel Online integration.

| Plan | SGD/mo | What You Get |
|------|--------|--------------|
| Business Basic | ~$8/user | Web apps, 1TB OneDrive, Teams |
| Business Standard | ~$17/user | + Desktop Office apps |

*Note: M365 prices rising ~$1-2/user from Jul 2026.*

### Messaging (optional)

| Integration | SGD/mo | Notes |
|-------------|--------|-------|
| Telegram | $0 | Free forever |
| WhatsApp Cloud API | per-message | 1,000 free service msgs/mo, then ~$0.03-0.19/msg |
| Notion API | $0 | Free tier sufficient |

---

## Realistic Monthly Totals (SGD)

| Profile | Monthly | Breakdown |
|---------|---------|-----------|
| **Student / evaluator** | **$0** | Laptop + Gemini free tier |
| **Solo minimal** | **~$8** | VPS + Gemini free + Telegram |
| **Solo + Google** | **~$17** | VPS + Gemini free + Google Starter |
| **Recommended (Claude)** | **~$151** | VPS + Claude Max 5x + Google Starter |
| **Recommended (OpenAI)** | **~$151** | VPS + ChatGPT Pro + Google Starter |
| **Full stack** | **~$175-210** | VPS + Claude/OpenAI + Google + M365 |
| **Team (3 users)** | **~$300-400+** | VPS + Team plan + Google + M365 |

---

## Where the Money Goes

```
Typical "Recommended" setup ($151/mo):

Claude Max 5x    ████████████████████████████████████████  $134  (89%)
Google Starter    ██████                                    $9   (6%)
VPS (Hetzner)     █████                                     $8   (5%)
```

The AI subscription is 85-90% of the cost. Everything else is cheap.

---

## Our Recommendation

**Claude Max 5x ($134/mo) + Google Workspace Starter ($9.40/mo) + Hetzner CX22 ($8/mo) = ~$151 SGD/mo**

This gives you full Claude Code access (the best coding agent), Gmail/Calendar/Drive integration, and a dedicated server. OpenAI's new ChatGPT Pro at the same price point is a comparable alternative if you prefer Codex.

The $0 path (laptop + Gemini free) is genuinely usable for evaluation and light personal use — all core features work without paying anything.

---

## Sources

- [Hetzner Cloud Pricing (Apr 2026)](https://www.hetzner.com/cloud/)
- [Claude Plans & Pricing](https://claude.com/pricing)
- [ChatGPT Plans & Pricing](https://chatgpt.com/pricing/)
- [OpenAI Codex Pricing](https://developers.openai.com/codex/pricing)
- [OpenAI $100 Pro Plan (Apr 2026)](https://techcrunch.com/2026/04/09/chatgpt-pro-plan-100-month-codex/)
- [Google Workspace Pricing Singapore](https://workspace.google.com/pricing)
- [Microsoft 365 Business Pricing](https://www.microsoft.com/en-sg/microsoft-365/business/compare-all-plans)
- [WhatsApp Cloud API Pricing](https://developers.facebook.com/docs/whatsapp/pricing)
