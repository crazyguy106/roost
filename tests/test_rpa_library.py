"""Tests for the RPA flow library — schema validator, loader, seed-on-boot,
and round-trip import/export.

These tests exercise the data path only (DB + YAML files). They do not run
the interpreter against a live portal — that requires real credentials and
a browserless sidecar, see docs/rpa-authoring.md for the manual smoke test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def clean_rpa_table():
    """Wipe rpa_flow_configs so each test starts from empty."""
    from roost.database import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM rpa_flow_configs")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        conn.execute("DELETE FROM rpa_flow_configs")
        conn.commit()
    finally:
        conn.close()


def test_library_files_all_validate():
    """Every shipped YAML in library/ must parse and validate."""
    from roost.extras.rpa.services.rpa_flows import LIBRARY_DIR, load_yaml
    files = sorted(LIBRARY_DIR.glob("*.yaml"))
    assert files, "no library files found"
    for p in files:
        load_yaml(p)  # raises FlowValidationError on any issue


def test_validator_catches_missing_op():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    errs = validate_flow({"portal": "x", "steps": [{"selector": "a"}]})
    assert any("missing `op`" in e for e in errs)


def test_validator_catches_unknown_op():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    errs = validate_flow({"portal": "x", "steps": [{"op": "teleport"}]})
    assert any("unknown op" in e for e in errs)


def test_validator_catches_missing_required_arg():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    errs = validate_flow({"portal": "x", "steps": [{"op": "fill", "selector": "a"}]})
    assert any("missing required arg `value`" in e for e in errs)


def test_validator_catches_bad_placeholder():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    errs = validate_flow({
        "portal": "x",
        "steps": [{"op": "fill", "selector": "a", "value": "$nosuch:bad"}],
    })
    assert any("malformed placeholder" in e for e in errs)


def test_validator_accepts_all_known_placeholders():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    cfg = {
        "portal": "x",
        "steps": [
            {"op": "goto", "url": "$param:login_url"},
            {"op": "fill", "selector": "a", "value": "$cred:user"},
            {"op": "fill", "selector": "b", "value": "$var:foo"},
            {"op": "fill", "selector": "c", "value": "$state:bar"},
            {"op": "fill", "selector": "d", "value": "$otp"},
        ],
    }
    assert validate_flow(cfg) == []


def test_validator_rejects_empty_steps():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    errs = validate_flow({"portal": "x", "steps": []})
    assert any("non-empty list" in e for e in errs)


def test_seed_library_writes_to_db(clean_rpa_table):
    from roost.extras.rpa.services.rpa_flows import seed_library
    from roost.extras.rpa.services.rpa_flows.configs import get_config

    result = seed_library()
    assert result["seeded"] >= 1
    assert not result.get("errors"), result["errors"]

    aia = get_config("aia", user_id="")
    assert aia is not None
    assert aia["portal_slug"] == "aia"
    assert isinstance(aia.get("steps"), list) and aia["steps"]


def test_seed_library_is_idempotent(clean_rpa_table):
    from roost.extras.rpa.services.rpa_flows import seed_library
    first = seed_library()
    second = seed_library()
    assert first["seeded"] == second["skipped"]
    assert second["seeded"] == 0


def test_seed_library_does_not_clobber_user_edits(clean_rpa_table):
    from roost.extras.rpa.services.rpa_flows import seed_library
    from roost.extras.rpa.services.rpa_flows.configs import set_config, get_config

    set_config(
        "aia", user_id="",
        name="Customised", login_url="https://custom",
        steps=[{"op": "log", "message": "customised"}],
        otp_config={}, enabled=True,
    )
    result = seed_library()
    # aia must be skipped because it pre-existed; other library files seed normally.
    after = get_config("aia", user_id="")
    assert after["name"] == "Customised"
    assert after["login_url"] == "https://custom"
    assert after["steps"] == [{"op": "log", "message": "customised"}]


def test_round_trip_import_export(tmp_path: Path, clean_rpa_table):
    """YAML → DB → YAML round-trip preserves the steps."""
    from roost.extras.rpa.services.rpa_flows import LIBRARY_DIR, USER_DIR
    from roost.extras.rpa.services.rpa_flows import import_to_db, export_from_db

    src = LIBRARY_DIR / "example.yaml"

    # Both import and export require paths under USER_DIR or LIBRARY_DIR.
    USER_DIR.mkdir(parents=True, exist_ok=True)
    out_path = USER_DIR / "_test_roundtrip.yaml"
    try:
        import_to_db(src, user_id="")
        export_from_db("example", out_path, user_id="")

        original = yaml.safe_load(src.read_text())
        roundtripped = yaml.safe_load(out_path.read_text())
        assert roundtripped["portal"] == original["portal"]
        assert roundtripped["steps"] == original["steps"]
        assert roundtripped["enabled"] == original.get("enabled", True)
    finally:
        if out_path.exists():
            out_path.unlink()


def test_import_rejects_path_traversal(tmp_path: Path, clean_rpa_table):
    """Files outside library/ and data/rpa_flows/ must be rejected."""
    from roost.extras.rpa.services.rpa_flows import import_to_db

    rogue = tmp_path / "rogue.yaml"
    rogue.write_text("portal: rogue\nsteps:\n  - op: log\n")
    with pytest.raises(ValueError, match="outside allowed roots"):
        import_to_db(rogue, user_id="")


def test_validator_accepts_whatsapp_send_op():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow, KNOWN_OPS
    assert "whatsapp_send" in KNOWN_OPS
    cfg = {
        "portal": "x",
        "steps": [
            {"op": "whatsapp_send", "to": "$param:client_phone", "body": "hello"},
            {"op": "whatsapp_send", "to": "+6591234567",
             "document": "$var:last_download", "caption": "AIA $param:date"},
            {"op": "whatsapp_send", "to": "+6591234567",
             "source": "last_screenshot"},
        ],
    }
    assert validate_flow(cfg) == []


def test_validator_rejects_whatsapp_send_without_to():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    errs = validate_flow({
        "portal": "x",
        "steps": [{"op": "whatsapp_send", "body": "hi"}],
    })
    assert any("missing required arg `to`" in e for e in errs)


def test_validator_accepts_screenshot_op():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow, KNOWN_OPS
    assert "screenshot" in KNOWN_OPS
    cfg = {
        "portal": "x",
        "steps": [
            {"op": "screenshot"},
            {"op": "screenshot", "selector": "#chart", "name": "chart"},
            {"op": "screenshot", "full_page": True, "path": "$param:out_path"},
        ],
    }
    assert validate_flow(cfg) == []


def test_list_library_reports_each_file():
    from roost.extras.rpa.services.rpa_flows import list_library
    items = list_library()
    portals = {it["portal"] for it in items}
    assert {"example", "aia", "great_eastern", "hdb_eip"} <= portals
    for it in items:
        assert it["valid"], f"{it['file']} invalid: {it['errors']}"


def test_validator_accepts_select_option_op():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow, KNOWN_OPS
    assert "select_option" in KNOWN_OPS
    cfg = {
        "portal": "x",
        "steps": [
            {"op": "select_option", "selector": "select#x", "value": "$param:choice"},
            {"op": "select_option", "selector": "select#y", "value": "Chinese", "by": "label"},
        ],
    }
    assert validate_flow(cfg) == []


def test_validator_rejects_select_option_without_value():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    errs = validate_flow({
        "portal": "x",
        "steps": [{"op": "select_option", "selector": "select#x"}],
    })
    assert any("missing required arg `value`" in e for e in errs)


def test_validator_accepts_await_user_session_op():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow, KNOWN_OPS
    assert "await_user_session" in KNOWN_OPS
    cfg = {
        "portal": "x",
        "steps": [
            {"op": "await_user_session", "selector": "div.post-singpass"},
            {"op": "await_user_session", "selector": "#done", "prompt": "$param:msg", "timeout_ms": 900_000},
        ],
    }
    assert validate_flow(cfg) == []


def test_validator_rejects_await_user_session_without_selector():
    from roost.extras.rpa.services.rpa_flows.schema import validate_flow
    errs = validate_flow({
        "portal": "x",
        "steps": [{"op": "await_user_session"}],
    })
    assert any("missing required arg `selector`" in e for e in errs)


def test_hdb_eip_library_file_validates():
    from roost.extras.rpa.services.rpa_flows import LIBRARY_DIR, load_yaml
    cfg = load_yaml(LIBRARY_DIR / "hdb_eip.yaml")
    ops = [s["op"] for s in cfg["steps"]]
    assert "select_option" in ops
    assert "screenshot" in ops
