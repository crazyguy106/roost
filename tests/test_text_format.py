"""collapse_soft_wraps — fold hard-wrapped prose for chat surfaces while
preserving paragraphs, lists, and short intentional line breaks."""

from __future__ import annotations

from roost.extras.messaging_external.services.text_format import collapse_soft_wraps


def test_folds_wrapped_prose_into_one_line():
    src = (
        "Thanks for reaching out! Quick question to help me find the right\n"
        "places for you — when are you hoping to move in by? (e.g. 'this\n"
        "month', 'in 3 months', 'no rush')"
    )
    out = collapse_soft_wraps(src)
    assert "\n" not in out
    assert out.startswith("Thanks for reaching out!")
    assert "right places for you" in out
    assert "(e.g. 'this month', 'in 3 months', 'no rush')" in out


def test_preserves_paragraph_breaks():
    src = (
        "Hi Ethan,\n\n"
        "Thanks for your message and for getting in touch with us today.\n\n"
        "Best"
    )
    assert collapse_soft_wraps(src) == src


def test_preserves_lists():
    src = (
        "To help me line up the right options for you, could you confirm:\n"
        "1. Budget range you're working with\n"
        "2. Preferred districts or estates\n"
        "3. Timeline (move-in date)"
    )
    lines = collapse_soft_wraps(src).split("\n")
    assert lines[0].endswith("could you confirm:")
    assert lines[1].startswith("1. Budget")
    assert lines[2].startswith("2. Preferred")
    assert lines[3].startswith("3. Timeline")


def test_preserves_short_signature_lines():
    src = (
        "Reply at your convenience whenever you get a moment to take a look.\n\n"
        "Best regards,\nEthan Seow\nVerixiom"
    )
    assert "Best regards,\nEthan Seow\nVerixiom" in collapse_soft_wraps(src)


def test_idempotent_and_singleline_passthrough():
    assert collapse_soft_wraps("Just one line.") == "Just one line."
    once = collapse_soft_wraps(
        "A long line that surely exceeds forty-five characters in width\n"
        "and continues right here."
    )
    assert "\n" not in once
    assert collapse_soft_wraps(once) == once  # idempotent


def test_empty_and_none():
    assert collapse_soft_wraps("") == ""
    assert collapse_soft_wraps(None) == ""
