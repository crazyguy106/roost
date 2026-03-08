"""MCP tools for Google Sheets — read, write, append, template creation."""

from roost.mcp.server import mcp


@mcp.tool()
def gsheets_read_range(
    spreadsheet_id: str,
    range: str,
    value_render: str = "FORMATTED_VALUE",
) -> dict:
    """Read cell values from a Google Sheets range.

    Args:
        spreadsheet_id: The Google Sheets spreadsheet ID (from the URL).
        range: A1 notation range, e.g. "Sheet1!A1:D10", "A1:C", or "Sheet1".
        value_render: How values are rendered — FORMATTED_VALUE (default),
            UNFORMATTED_VALUE, or FORMULA.
    """
    try:
        from roost.sheets_service import gsheets_read_range as _read
        return _read(spreadsheet_id, range, value_render)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gsheets_write_range(
    spreadsheet_id: str,
    range: str,
    values: list,
    value_input: str = "USER_ENTERED",
) -> dict:
    """Write values to a Google Sheets range.

    Args:
        spreadsheet_id: The Google Sheets spreadsheet ID.
        range: A1 notation range, e.g. "Sheet1!A1:D10".
        values: 2D JSON array of values to write,
            e.g. [["Name", "Score"], ["Alice", 95]].
        value_input: How input is interpreted — USER_ENTERED (parses formulas,
            dates, numbers) or RAW (stores as-is).
    """
    try:
        from roost.sheets_service import gsheets_write_range as _write
        return _write(spreadsheet_id, range, values, value_input)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gsheets_append_rows(
    spreadsheet_id: str,
    range: str,
    values: list,
    value_input: str = "USER_ENTERED",
) -> dict:
    """Append rows after the last row with data in a Google Sheets range.

    Args:
        spreadsheet_id: The Google Sheets spreadsheet ID.
        range: A1 notation range indicating the table to append to,
            e.g. "Sheet1!A:D" or "Sheet1".
        values: 2D JSON array of rows to append,
            e.g. [["Alice", 95], ["Bob", 87]].
        value_input: How input is interpreted — USER_ENTERED or RAW.
    """
    try:
        from roost.sheets_service import gsheets_append_rows as _append
        return _append(spreadsheet_id, range, values, value_input)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gsheets_create_from_template(
    template_id: str,
    name: str,
    replacements: str = "",
    folder_id: str = "",
) -> dict:
    """Create a new spreadsheet by copying a template and optionally writing data.

    Args:
        template_id: ID of the template spreadsheet to copy.
        name: Name for the new spreadsheet.
        replacements: Optional JSON string mapping A1 ranges to 2D value arrays,
            e.g. '{"Sheet1!B2:B5": [["Alice"], ["Bob"]]}'.  Empty = no writes.
        folder_id: Optional Google Drive folder ID to place the copy in.
    """
    try:
        import json
        from roost.sheets_service import gsheets_create_from_template as _create
        repl = json.loads(replacements) if replacements else None
        return _create(template_id, name, repl, folder_id or None)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in replacements: {e}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gsheets_get_metadata(spreadsheet_id: str) -> dict:
    """Get spreadsheet metadata — title, sheet names, named ranges.

    Args:
        spreadsheet_id: The Google Sheets spreadsheet ID.
    """
    try:
        from roost.sheets_service import gsheets_get_metadata as _meta
        return _meta(spreadsheet_id)
    except Exception as e:
        return {"error": str(e)}
