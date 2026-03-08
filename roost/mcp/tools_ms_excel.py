"""MCP tools for Microsoft Excel (workbooks) via Graph API.

Operates on .xlsx files stored in OneDrive. Requires Files.ReadWrite scope.
"""

from roost.mcp.server import mcp


@mcp.tool()
def ms_excel_list_worksheets(file_path: str) -> dict:
    """List worksheets in an Excel workbook stored in OneDrive.

    Args:
        file_path: OneDrive path to the .xlsx file (e.g. "/Documents/budget.xlsx").
    """
    try:
        from roost.mcp.ms_graph_helpers import excel_list_worksheets

        sheets = excel_list_worksheets(file_path)
        return {
            "file": file_path,
            "count": len(sheets),
            "worksheets": sheets,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_excel_read_range(file_path: str, worksheet: str, cell_range: str) -> dict:
    """Read a range of cells from an Excel workbook in OneDrive.

    Returns a 2D array of values plus formulas.

    Args:
        file_path: OneDrive path to the .xlsx file (e.g. "/Documents/budget.xlsx").
        worksheet: Worksheet name (e.g. "Sheet1").
        cell_range: Cell range in A1 notation (e.g. "A1:D10", "B2:B50").
    """
    try:
        from roost.mcp.ms_graph_helpers import excel_read_range

        return excel_read_range(file_path, worksheet, cell_range)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_excel_write_range(file_path: str, worksheet: str, cell_range: str, values: list[list]) -> dict:
    """Write values to a range of cells in an Excel workbook in OneDrive.

    The values array must match the dimensions of the range.
    Example: cell_range="A1:C2", values=[["Name","Age","City"],["Alice",30,"London"]]

    Args:
        file_path: OneDrive path to the .xlsx file.
        worksheet: Worksheet name (e.g. "Sheet1").
        cell_range: Cell range in A1 notation (e.g. "A1:C2").
        values: 2D array of values — rows x columns, matching the range dimensions.
    """
    try:
        from roost.mcp.ms_graph_helpers import excel_write_range

        return excel_write_range(file_path, worksheet, cell_range, values)
    except Exception as e:
        return {"error": str(e)}
