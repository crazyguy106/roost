"""MCP tools for Notion API operations.

Wraps the notion-client SDK via the existing rate-limited singleton client
in roost.notion.client. Provides direct Notion workspace access — creating
pages, querying databases, managing blocks and comments.

These tools complement (not replace) the bidirectional sync in notion/sync.py.
"""

import copy
from typing import Optional

from roost.mcp.server import mcp


def _get_notion():
    """Get Notion client and rate_limited_call, or raise."""
    from roost.notion.client import get_client, rate_limited_call

    client = get_client()
    if client is None:
        raise RuntimeError("Notion client unavailable — check NOTION_API_TOKEN")
    return client, rate_limited_call


# --- Search ---


@mcp.tool()
def notion_search(
    query: str = "",
    filter_type: Optional[str] = None,
    page_size: int = 10,
) -> dict:
    """Search Notion pages and databases by title.

    Args:
        query: Search text to match against page/database titles.
            Empty string returns recent items.
        filter_type: Optional filter — "page" or "database". Omit for both.
        page_size: Number of results (1-100, default 10).
    """
    try:
        client, call = _get_notion()
        kwargs = {"query": query, "page_size": min(max(page_size, 1), 100)}
        if filter_type in ("page", "database"):
            kwargs["filter"] = {"value": filter_type, "property": "object"}
        result = call(client.search, **kwargs)
        return {
            "query": query,
            "count": len(result.get("results", [])),
            "results": result.get("results", []),
            "has_more": result.get("has_more", False),
        }
    except Exception as e:
        return {"error": str(e)}


# --- Pages ---


@mcp.tool()
def notion_get_page(page_id: str) -> dict:
    """Retrieve a Notion page's properties.

    Args:
        page_id: The page ID or URL. Accepts 32-char hex (with or without dashes)
            or a full Notion URL.
    """
    try:
        client, call = _get_notion()
        return call(client.pages.retrieve, page_id=page_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_create_page(
    parent: dict,
    properties: dict,
    children: Optional[list] = None,
) -> dict:
    """Create a new Notion page.

    Args:
        parent: Parent location. Either {"database_id": "..."} to add a row
            to a database, or {"page_id": "..."} to create a subpage.
        properties: Page properties dict. For database parents, must match
            the database schema. For page parents, typically just
            {"title": [{"text": {"content": "Page Title"}}]}.
        children: Optional list of block objects for initial page content.
            Each block follows the Notion block format, e.g.
            [{"object": "block", "type": "paragraph",
              "paragraph": {"rich_text": [{"text": {"content": "Hello"}}]}}]
    """
    try:
        client, call = _get_notion()
        kwargs = {"parent": parent, "properties": properties}
        if children:
            kwargs["children"] = children
        return call(client.pages.create, **kwargs)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_update_page(page_id: str, properties: dict) -> dict:
    """Update a Notion page's properties.

    Args:
        page_id: The page ID to update.
        properties: Dict of properties to update. Only include properties
            that should change — others remain untouched.
    """
    try:
        client, call = _get_notion()
        return call(client.pages.update, page_id=page_id, properties=properties)
    except Exception as e:
        return {"error": str(e)}


# --- Blocks ---


@mcp.tool()
def notion_get_block_children(
    block_id: str,
    page_size: int = 100,
) -> dict:
    """Get the content blocks (children) of a page or block.

    Args:
        block_id: The page ID or block ID to read children from.
        page_size: Number of blocks to return (1-100, default 100).
    """
    try:
        client, call = _get_notion()
        result = call(
            client.blocks.children.list,
            block_id=block_id,
            page_size=min(max(page_size, 1), 100),
        )
        return {
            "block_id": block_id,
            "count": len(result.get("results", [])),
            "results": result.get("results", []),
            "has_more": result.get("has_more", False),
            "next_cursor": result.get("next_cursor"),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_append_blocks(block_id: str, children: list) -> dict:
    """Append content blocks to a page or block.

    Args:
        block_id: The page ID or block ID to append to.
        children: List of block objects to append. Each block follows
            the Notion block format, e.g.
            [{"object": "block", "type": "paragraph",
              "paragraph": {"rich_text": [{"text": {"content": "Hello"}}]}}]
    """
    try:
        client, call = _get_notion()
        return call(
            client.blocks.children.append,
            block_id=block_id,
            children=children,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_update_block(block_id: str, block_data: dict) -> dict:
    """Update a single Notion block.

    Args:
        block_id: The block ID to update.
        block_data: Block type-specific update data. Must include the block
            type key, e.g. {"paragraph": {"rich_text": [{"text": {"content": "Updated"}}]}}
    """
    try:
        client, call = _get_notion()
        return call(client.blocks.update, block_id=block_id, **block_data)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_delete_block(block_id: str) -> dict:
    """Delete (archive) a Notion block.

    Args:
        block_id: The block ID to delete.
    """
    try:
        client, call = _get_notion()
        return call(client.blocks.delete, block_id=block_id)
    except Exception as e:
        return {"error": str(e)}


# --- Databases ---


@mcp.tool()
def notion_query_database(
    database_id: str,
    filter: Optional[dict] = None,
    sorts: Optional[list] = None,
    page_size: int = 50,
) -> dict:
    """Query a Notion database with optional filters and sorts.

    Args:
        database_id: The database ID to query.
        filter: Optional Notion filter object, e.g.
            {"property": "Status", "select": {"equals": "Done"}}
        sorts: Optional list of sort objects, e.g.
            [{"property": "Created", "direction": "descending"}]
        page_size: Number of results (1-100, default 50).
    """
    try:
        client, call = _get_notion()

        # API v2025-09-03: query via data_sources, not databases
        from roost.notion.databases import get_data_source_id
        ds_id = get_data_source_id(database_id)
        if not ds_id:
            return {"error": f"No data source found for database {database_id}"}

        kwargs = {
            "data_source_id": ds_id,
            "page_size": min(max(page_size, 1), 100),
        }
        if filter:
            kwargs["filter"] = filter
        if sorts:
            kwargs["sorts"] = sorts
        result = call(client.data_sources.query, **kwargs)
        return {
            "database_id": database_id,
            "data_source_id": ds_id,
            "count": len(result.get("results", [])),
            "results": result.get("results", []),
            "has_more": result.get("has_more", False),
            "next_cursor": result.get("next_cursor"),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_get_database(database_id: str) -> dict:
    """Retrieve a Notion database's schema and properties.

    Args:
        database_id: The database ID to retrieve.
    """
    try:
        client, call = _get_notion()
        return call(client.databases.retrieve, database_id=database_id)
    except Exception as e:
        return {"error": str(e)}


# --- Comments ---


@mcp.tool()
def notion_create_comment(
    parent_page_id: str,
    rich_text: list,
) -> dict:
    """Add a comment to a Notion page.

    Args:
        parent_page_id: The page ID to comment on.
        rich_text: Comment content as rich text array, e.g.
            [{"text": {"content": "This looks good!"}}]
    """
    try:
        client, call = _get_notion()
        return call(
            client.comments.create,
            parent={"page_id": parent_page_id},
            rich_text=rich_text,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_list_comments(block_id: str, page_size: int = 50) -> dict:
    """List comments on a Notion page or block.

    Args:
        block_id: The page or block ID to list comments for.
        page_size: Number of comments to return (1-100, default 50).
    """
    try:
        client, call = _get_notion()
        result = call(
            client.comments.list,
            block_id=block_id,
            page_size=min(max(page_size, 1), 100),
        )
        return {
            "block_id": block_id,
            "count": len(result.get("results", [])),
            "results": result.get("results", []),
            "has_more": result.get("has_more", False),
        }
    except Exception as e:
        return {"error": str(e)}


# --- Page Operations ---


@mcp.tool()
def notion_archive_page(page_id: str) -> dict:
    """Archive (soft-delete) a Notion page.

    Args:
        page_id: The page ID to archive.
    """
    try:
        client, call = _get_notion()
        return call(client.pages.update, page_id=page_id, archived=True)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_restore_page(page_id: str) -> dict:
    """Restore an archived Notion page.

    Args:
        page_id: The page ID to restore.
    """
    try:
        client, call = _get_notion()
        return call(client.pages.update, page_id=page_id, archived=False)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_create_database(
    parent_page_id: str,
    title: str,
    properties: dict,
) -> dict:
    """Create a new Notion database as a child of a page.

    Args:
        parent_page_id: The parent page ID to create the database in.
        title: Database title.
        properties: Database property schema, e.g.
            {"Name": {"title": {}}, "Status": {"select": {"options": [{"name": "Todo"}, {"name": "Done"}]}}}
    """
    try:
        client, call = _get_notion()
        return call(
            client.databases.create,
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": title}}],
            properties=properties,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def notion_duplicate_page(
    page_id: str,
    new_title: str = "",
) -> dict:
    """Duplicate a Notion page — copies properties and content blocks.

    Creates a new page with the same parent, properties, and up to 100
    content blocks from the source page.

    Args:
        page_id: The source page ID to duplicate.
        new_title: Title for the copy. Empty = "Copy of <original title>".
    """
    try:
        client, call = _get_notion()

        # 1. Read source page
        source = call(client.pages.retrieve, page_id=page_id)
        parent = source.get("parent", {})
        properties = copy.deepcopy(source.get("properties", {}))

        # 2. Build new title
        original_title = ""
        for key, prop in properties.items():
            if prop.get("type") == "title":
                title_items = prop.get("title", [])
                if title_items:
                    original_title = "".join(
                        t.get("plain_text", "") for t in title_items
                    )
                # Set new title
                final_title = new_title or f"Copy of {original_title}"
                properties[key] = {
                    "title": [{"text": {"content": final_title}}]
                }
                break

        # 3. Read source blocks (first 100)
        blocks_result = call(
            client.blocks.children.list,
            block_id=page_id,
            page_size=100,
        )
        source_blocks = blocks_result.get("results", [])

        # 4. Clean blocks for creation (remove IDs and system fields)
        children = []
        for block in source_blocks:
            block_type = block.get("type")
            if not block_type:
                continue
            new_block = {
                "object": "block",
                "type": block_type,
                block_type: copy.deepcopy(block.get(block_type, {})),
            }
            children.append(new_block)

        # 5. Create new page
        create_kwargs = {"parent": parent, "properties": properties}
        if children:
            create_kwargs["children"] = children

        new_page = call(client.pages.create, **create_kwargs)

        return {
            "ok": True,
            "source_page_id": page_id,
            "new_page_id": new_page.get("id"),
            "title": final_title,
            "blocks_copied": len(children),
            "url": new_page.get("url"),
        }
    except Exception as e:
        return {"error": str(e)}
