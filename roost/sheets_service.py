"""Google Sheets service — read, write, append, template-based creation.

Shared service layer used by the MCP tools module.
"""

import logging

logger = logging.getLogger("roost.sheets")


def _get_sheets_service():
    """Build an authenticated Google Sheets API v4 service."""
    from roost.gmail.client import build_sheets_service
    return build_sheets_service()


def _get_drive_service():
    """Build an authenticated Google Drive API v3 service."""
    from googleapiclient.discovery import build
    from roost.gmail.client import _build_credentials
    creds = _build_credentials()
    return build("drive", "v3", credentials=creds)


def gsheets_read_range(
    spreadsheet_id: str,
    range_: str,
    value_render: str = "FORMATTED_VALUE",
) -> dict:
    """Read cell values from a spreadsheet range.

    Args:
        spreadsheet_id: The Google Sheets spreadsheet ID.
        range_: A1 notation range (e.g. "Sheet1!A1:D10", "A1:C", "Sheet1").
        value_render: How values should be rendered — FORMATTED_VALUE,
            UNFORMATTED_VALUE, or FORMULA.

    Returns dict with values (2D list), range, and metadata.
    """
    service = _get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_,
        valueRenderOption=value_render,
    ).execute()

    values = result.get("values", [])
    return {
        "spreadsheet_id": spreadsheet_id,
        "range": result.get("range", range_),
        "row_count": len(values),
        "col_count": max((len(row) for row in values), default=0),
        "values": values,
    }


def gsheets_write_range(
    spreadsheet_id: str,
    range_: str,
    values: list[list],
    value_input: str = "USER_ENTERED",
) -> dict:
    """Write values to a spreadsheet range.

    Args:
        spreadsheet_id: The Google Sheets spreadsheet ID.
        range_: A1 notation range (e.g. "Sheet1!A1:D10").
        values: 2D list of values to write, e.g. [["Name", "Score"], ["Alice", 95]].
        value_input: How input should be interpreted — USER_ENTERED (parses
            formulas, dates) or RAW (stores as-is).

    Returns update metadata.
    """
    service = _get_sheets_service()
    body = {"values": values}
    result = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_,
        valueInputOption=value_input,
        body=body,
    ).execute()

    return {
        "spreadsheet_id": spreadsheet_id,
        "updated_range": result.get("updatedRange", ""),
        "updated_rows": result.get("updatedRows", 0),
        "updated_columns": result.get("updatedColumns", 0),
        "updated_cells": result.get("updatedCells", 0),
    }


def gsheets_append_rows(
    spreadsheet_id: str,
    range_: str,
    values: list[list],
    value_input: str = "USER_ENTERED",
) -> dict:
    """Append rows after the last row with data in a range.

    Args:
        spreadsheet_id: The Google Sheets spreadsheet ID.
        range_: A1 notation range indicating the table to append to
            (e.g. "Sheet1!A:D" or "Sheet1").
        values: 2D list of rows to append, e.g. [["Alice", 95], ["Bob", 87]].
        value_input: How input should be interpreted — USER_ENTERED or RAW.

    Returns append metadata.
    """
    service = _get_sheets_service()
    body = {"values": values}
    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=range_,
        valueInputOption=value_input,
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()

    updates = result.get("updates", {})
    return {
        "spreadsheet_id": spreadsheet_id,
        "updated_range": updates.get("updatedRange", ""),
        "updated_rows": updates.get("updatedRows", 0),
        "updated_cells": updates.get("updatedCells", 0),
    }


def gsheets_create_from_template(
    template_id: str,
    name: str,
    replacements: dict[str, list[list]] | None = None,
    folder_id: str | None = None,
) -> dict:
    """Copy a template spreadsheet and optionally write to named ranges.

    Args:
        template_id: ID of the template spreadsheet to copy.
        name: Name for the new spreadsheet.
        replacements: Optional dict mapping range (A1 notation) to 2D list of values.
            e.g. {"Sheet1!B2:B5": [["Alice"], ["Bob"], ["Carol"], ["Dave"]]}.
        folder_id: Optional Drive folder ID to place the copy in.

    Returns info about the created spreadsheet.
    """
    drive_service = _get_drive_service()

    body = {"name": name}
    if folder_id:
        body["parents"] = [folder_id]

    copy = drive_service.files().copy(
        fileId=template_id,
        body=body,
    ).execute()

    new_id = copy["id"]
    logger.info("Created spreadsheet '%s' (ID: %s) from template %s", name, new_id, template_id)

    result = {
        "spreadsheet_id": new_id,
        "name": name,
        "template_id": template_id,
        "url": f"https://docs.google.com/spreadsheets/d/{new_id}/edit",
    }

    if replacements:
        write_results = {}
        for range_, values in replacements.items():
            wr = gsheets_write_range(new_id, range_, values)
            write_results[range_] = wr
        result["writes"] = write_results

    return result


def gsheets_get_metadata(spreadsheet_id: str) -> dict:
    """Get spreadsheet metadata — title, sheets, named ranges.

    Args:
        spreadsheet_id: The Google Sheets spreadsheet ID.

    Returns spreadsheet properties and sheet list.
    """
    service = _get_sheets_service()
    result = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="properties,sheets.properties,namedRanges",
    ).execute()

    sheets = []
    for sheet in result.get("sheets", []):
        props = sheet.get("properties", {})
        sheets.append({
            "sheet_id": props.get("sheetId"),
            "title": props.get("title"),
            "index": props.get("index"),
            "row_count": props.get("gridProperties", {}).get("rowCount"),
            "col_count": props.get("gridProperties", {}).get("columnCount"),
        })

    named_ranges = []
    for nr in result.get("namedRanges", []):
        named_ranges.append({
            "name": nr.get("name"),
            "named_range_id": nr.get("namedRangeId"),
            "range": nr.get("range"),
        })

    return {
        "spreadsheet_id": spreadsheet_id,
        "title": result.get("properties", {}).get("title", ""),
        "sheets": sheets,
        "named_ranges": named_ranges,
    }
