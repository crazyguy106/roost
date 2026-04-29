"""MCP tools for cross-channel conversation memory."""

from roost.mcp.server import mcp


@mcp.tool()
def remember(
    content: str,
    category: str = "fact",
    pinned: bool = False,
) -> dict:
    """Store something in cross-channel memory.

    Memory entries persist across Telegram, Web, and MCP interfaces.
    Unpinned entries expire after 7 days.

    Args:
        content: The thing to remember (max 500 chars).
        category: One of: fact, decision, preference, context.
        pinned: Pinned entries don't auto-expire.
    """
    from roost.services.conversation_memory import remember as _remember
    memory_id = _remember(content=content, channel="mcp", category=category, pinned=pinned)
    if memory_id:
        return {"ok": True, "memory_id": memory_id}
    return {"error": "Failed to save memory"}


@mcp.tool()
def recall(
    query: str = "",
    category: str = "",
    limit: int = 20,
) -> dict:
    """Recall cross-channel memory entries.

    Args:
        query: Search text (substring match). Empty = all recent.
        category: Filter by category: fact, decision, preference, context.
        limit: Max entries to return.
    """
    from roost.services.conversation_memory import recall as _recall
    memories = _recall(query=query, category=category, limit=limit)
    return {"count": len(memories), "memories": memories}


@mcp.tool()
def forget(memory_id: int) -> dict:
    """Delete a specific memory entry.

    Args:
        memory_id: The memory ID to delete.
    """
    from roost.services.conversation_memory import forget as _forget
    return _forget(memory_id)


@mcp.tool()
def pin_memory(memory_id: int, pinned: bool = True) -> dict:
    """Pin or unpin a memory entry. Pinned entries don't auto-expire.

    Args:
        memory_id: The memory ID.
        pinned: True to pin, False to unpin.
    """
    from roost.services.conversation_memory import pin_memory as _pin
    return _pin(memory_id, pinned=pinned)
