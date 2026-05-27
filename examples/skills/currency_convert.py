"""Simple currency converter — the minimal viable skill.

Uses hardcoded mid-market rates (refreshed manually). No network calls,
no credentials, no state. Good starting point to understand the skill
contract without getting lost in API details.
"""

SKILL_META = {
    "name": "currency_convert",
    "description": "Convert between common currencies using built-in rates.",
    "trigger": "convert",
    "version": "1.0.0",
    "risk_tier": "read_only",
}

# Rates relative to USD. Update manually when they drift.
_RATES = {
    "USD": 1.00,
    "EUR": 0.92,
    "GBP": 0.79,
    "SGD": 1.35,
    "JPY": 150.0,
    "CNY": 7.25,
    "MYR": 4.68,
    "AUD": 1.52,
    "CAD": 1.37,
    "INR": 83.5,
}


async def run(args: dict) -> str:
    """
    Expected args:
        amount: float     — how much
        from:   str       — source currency code (e.g. "USD")
        to:     str       — target currency code (e.g. "SGD")

    Example trigger message:
        "convert 100 USD to SGD"
    """
    try:
        amount = float(args.get("amount", 0))
    except (TypeError, ValueError):
        return "Error: amount must be a number."

    src = (args.get("from") or "").upper()
    dst = (args.get("to") or "").upper()

    if src not in _RATES:
        return f"Unknown source currency: {src}. Known: {', '.join(sorted(_RATES))}"
    if dst not in _RATES:
        return f"Unknown target currency: {dst}. Known: {', '.join(sorted(_RATES))}"
    if amount <= 0:
        return "Amount must be positive."

    # Convert via USD as pivot
    in_usd = amount / _RATES[src]
    out = in_usd * _RATES[dst]

    return f"{amount:,.2f} {src} = {out:,.2f} {dst}"
