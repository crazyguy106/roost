"""Google Docs service — read, replace, append, template-based creation.

Shared service layer used by the MCP tools module.
"""

import logging

logger = logging.getLogger("roost.docs")


def _get_docs_service():
    """Build an authenticated Google Docs API v1 service."""
    from roost.gmail.client import build_docs_service
    return build_docs_service()


def _get_drive_service():
    """Build an authenticated Google Drive API v3 service."""
    from googleapiclient.discovery import build
    from roost.gmail.client import _build_credentials
    creds = _build_credentials()
    return build("drive", "v3", credentials=creds)


def gdocs_read_content(document_id: str) -> dict:
    """Read the full text content of a Google Doc.

    Args:
        document_id: The Google Docs document ID.

    Returns document title and full text content.
    """
    service = _get_docs_service()
    doc = service.documents().get(documentId=document_id).execute()

    title = doc.get("title", "")
    body = doc.get("body", {})
    content = body.get("content", [])

    # Extract all text from structural elements
    text_parts = []
    for element in content:
        _extract_text(element, text_parts)

    full_text = "".join(text_parts)

    return {
        "document_id": document_id,
        "title": title,
        "text": full_text,
        "character_count": len(full_text),
    }


def gdocs_replace_text(
    document_id: str,
    replacements: dict[str, str],
) -> dict:
    """Replace placeholder text throughout a Google Doc.

    Args:
        document_id: The Google Docs document ID.
        replacements: Dict mapping placeholder strings to replacement values,
            e.g. {"{{name}}": "Alice", "{{date}}": "2026-02-13"}.

    Returns counts of replacements made.
    """
    service = _get_docs_service()

    requests = []
    for old_text, new_text in replacements.items():
        requests.append({
            "replaceAllText": {
                "containsText": {
                    "text": old_text,
                    "matchCase": True,
                },
                "replaceText": new_text,
            }
        })

    if not requests:
        return {"total_replacements": 0, "per_placeholder": {}}

    response = service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": requests},
    ).execute()

    counts = {}
    replies = response.get("replies", [])
    placeholder_keys = list(replacements.keys())
    for idx, reply in enumerate(replies):
        replace_result = reply.get("replaceAllText", {})
        occurrences = replace_result.get("occurrencesChanged", 0)
        if idx < len(placeholder_keys):
            counts[placeholder_keys[idx]] = occurrences

    return {
        "document_id": document_id,
        "total_replacements": sum(counts.values()),
        "per_placeholder": counts,
    }


def gdocs_create_from_template(
    template_id: str,
    name: str,
    replacements: dict[str, str] | None = None,
    folder_id: str | None = None,
) -> dict:
    """Copy a template document and optionally fill placeholders.

    Args:
        template_id: ID of the template document to copy.
        name: Name for the new document.
        replacements: Optional dict of placeholder -> value replacements.
        folder_id: Optional Drive folder ID to place the copy in.

    Returns info about the created document.
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
    logger.info("Created document '%s' (ID: %s) from template %s", name, new_id, template_id)

    result = {
        "document_id": new_id,
        "name": name,
        "template_id": template_id,
        "url": f"https://docs.google.com/document/d/{new_id}/edit",
    }

    if replacements:
        replace_result = gdocs_replace_text(new_id, replacements)
        result["replacements"] = replace_result

    return result


def gdocs_append_text(
    document_id: str,
    text: str,
    style: str = "NORMAL_TEXT",
) -> dict:
    """Append text to the end of a Google Doc.

    Args:
        document_id: The Google Docs document ID.
        text: Text to append. Use \\n for newlines.
        style: Paragraph style — NORMAL_TEXT, HEADING_1, HEADING_2, HEADING_3,
            HEADING_4, HEADING_5, HEADING_6, TITLE, SUBTITLE.

    Returns update metadata.
    """
    service = _get_docs_service()

    # Get current document length to find the end index
    doc = service.documents().get(documentId=document_id).execute()
    body = doc.get("body", {})
    content = body.get("content", [])

    # The end index is the last structural element's endIndex - 1
    end_index = 1  # default: start of doc
    if content:
        last_element = content[-1]
        end_index = last_element.get("endIndex", 1) - 1

    requests = [
        {
            "insertText": {
                "location": {"index": end_index},
                "text": text,
            }
        }
    ]

    # Apply paragraph style if not NORMAL_TEXT
    if style != "NORMAL_TEXT":
        requests.append({
            "updateParagraphStyle": {
                "range": {
                    "startIndex": end_index,
                    "endIndex": end_index + len(text),
                },
                "paragraphStyle": {
                    "namedStyleType": style,
                },
                "fields": "namedStyleType",
            }
        })

    response = service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": requests},
    ).execute()

    return {
        "document_id": document_id,
        "text_length": len(text),
        "inserted_at_index": end_index,
        "style": style,
    }


def gdocs_insert_image(
    document_id: str,
    image_url: str,
    width_pts: float = 468.0,
    height_pts: float = 300.0,
) -> dict:
    """Insert an image at the end of a Google Doc.

    Args:
        document_id: The Google Docs document ID.
        image_url: Public URL of the image to insert.
        width_pts: Image width in points (default 468 = full page width).
        height_pts: Image height in points (default 300).

    Returns insert metadata.
    """
    service = _get_docs_service()

    # Get end index
    doc = service.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    end_index = 1
    if content:
        end_index = content[-1].get("endIndex", 1) - 1

    requests = [{
        "insertInlineImage": {
            "location": {"index": end_index},
            "uri": image_url,
            "objectSize": {
                "width": {"magnitude": width_pts, "unit": "PT"},
                "height": {"magnitude": height_pts, "unit": "PT"},
            },
        }
    }]

    response = service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": requests},
    ).execute()

    return {
        "document_id": document_id,
        "image_url": image_url,
        "inserted_at_index": end_index,
    }


# ── Helpers ───────────────────────────────────────────────────────────


def _extract_text(element: dict, parts: list[str]) -> None:
    """Recursively extract text from a Docs structural element."""
    if "paragraph" in element:
        for el in element["paragraph"].get("elements", []):
            text_run = el.get("textRun", {})
            content = text_run.get("content", "")
            if content:
                parts.append(content)
    elif "table" in element:
        for row in element["table"].get("tableRows", []):
            for cell in row.get("tableCells", []):
                for cell_element in cell.get("content", []):
                    _extract_text(cell_element, parts)
    elif "tableOfContents" in element:
        for toc_element in element["tableOfContents"].get("content", []):
            _extract_text(toc_element, parts)
