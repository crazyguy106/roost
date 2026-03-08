"""MCP tools for Microsoft Teams — channels and chats via Graph API."""

from roost.mcp.server import mcp


@mcp.tool()
def ms_teams_list_teams() -> dict:
    """List all Microsoft Teams the user has joined."""
    try:
        from roost.mcp.ms_graph_helpers import teams_list_teams

        teams = teams_list_teams()
        return {"count": len(teams), "teams": teams}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_list_channels(team_id: str) -> dict:
    """List channels in a Microsoft Team.

    Args:
        team_id: The team ID (from ms_teams_list_teams).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_list_channels

        channels = teams_list_channels(team_id)
        return {"team_id": team_id, "count": len(channels), "channels": channels}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_read_messages(team_id: str, channel_id: str, max_results: int = 20) -> dict:
    """Read recent messages from a Teams channel.

    Args:
        team_id: The team ID.
        channel_id: The channel ID (from ms_teams_list_channels).
        max_results: Maximum messages to return (default 20).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_read_messages

        messages = teams_read_messages(team_id, channel_id, max_results)
        return {
            "team_id": team_id,
            "channel_id": channel_id,
            "count": len(messages),
            "messages": messages,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_send_message(team_id: str, channel_id: str, content: str) -> dict:
    """Send a message to a Teams channel.

    IMPORTANT: The user must approve the message content before this tool is called.

    Args:
        team_id: The team ID.
        channel_id: The channel ID.
        content: Message content (plain text or HTML).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_send_message

        return teams_send_message(team_id, channel_id, content)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_list_chats(max_results: int = 20) -> dict:
    """List the user's recent Teams chats (1:1 and group).

    Args:
        max_results: Maximum chats to return (default 20).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_list_chats

        chats = teams_list_chats(max_results)
        return {"count": len(chats), "chats": chats}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_read_chat(chat_id: str, max_results: int = 20) -> dict:
    """Read recent messages from a Teams chat.

    Args:
        chat_id: The chat ID (from ms_teams_list_chats).
        max_results: Maximum messages to return (default 20).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_read_chat

        messages = teams_read_chat(chat_id, max_results)
        return {
            "chat_id": chat_id,
            "count": len(messages),
            "messages": messages,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_send_chat(chat_id: str, content: str) -> dict:
    """Send a message in a Teams chat.

    IMPORTANT: The user must approve the message content before this tool is called.

    Args:
        chat_id: The chat ID.
        content: Message content (plain text or HTML).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_send_chat

        return teams_send_chat(chat_id, content)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_reply_channel_message(
    team_id: str,
    channel_id: str,
    message_id: str,
    content: str,
) -> dict:
    """Reply to a message in a Teams channel (threaded reply).

    IMPORTANT: The user must approve the reply content before this tool is called.

    Args:
        team_id: The team ID.
        channel_id: The channel ID.
        message_id: The parent message ID to reply to.
        content: Reply content (plain text or HTML).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_reply_channel_message

        return teams_reply_channel_message(team_id, channel_id, message_id, content)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_list_chat_members(chat_id: str) -> dict:
    """List members of a Teams chat with their user IDs and emails.

    Also auto-links members to contacts in the Roost contacts database
    by matching email addresses and storing their Microsoft user IDs.

    Args:
        chat_id: The chat ID (from ms_teams_list_chats).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_list_chat_members

        members = teams_list_chat_members(chat_id)

        # Auto-link to contacts
        linked = 0
        for m in members:
            if m.get("email"):
                try:
                    from roost.services.contacts import (
                        get_contact_by_email,
                        set_contact_identifier,
                    )
                    contact = get_contact_by_email(m["email"])
                    if contact and m.get("user_id"):
                        set_contact_identifier(
                            contact.id, "microsoft", m["user_id"],
                            label="teams", is_primary=1,
                        )
                        m["contact_id"] = contact.id
                        m["contact_name"] = contact.name
                        linked += 1
                except Exception:
                    pass  # Non-critical

        return {
            "chat_id": chat_id,
            "count": len(members),
            "linked_to_contacts": linked,
            "members": members,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_create_chat(
    member_emails: list[str],
    topic: str = "",
    message: str = "",
) -> dict:
    """Create a new Teams chat (1:1 or group).

    IMPORTANT: The user must approve before creating a chat.

    Args:
        member_emails: Email addresses to include. 1 email = 1:1 chat, 2+ = group chat.
        topic: Chat topic (group chats only).
        message: Optional first message to send in the new chat.
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_create_chat

        return teams_create_chat(member_emails, topic, message)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_lookup_user(email: str) -> dict:
    """Look up a Microsoft 365 user by email address. Returns their Azure AD user ID.

    Useful for resolving emails to user IDs needed by other Teams operations.
    Also auto-links the Microsoft user ID to the matching contact if found.

    Args:
        email: Email address to look up.
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_lookup_user

        user = teams_lookup_user(email)
        if not user:
            return {"error": f"User not found: {email}"}

        # Auto-link to contact
        if user.get("user_id"):
            try:
                from roost.services.contacts import (
                    get_contact_by_email,
                    set_contact_identifier,
                )
                contact = get_contact_by_email(email)
                if contact:
                    set_contact_identifier(
                        contact.id, "microsoft", user["user_id"],
                        label="teams", is_primary=1,
                    )
                    user["contact_id"] = contact.id
                    user["contact_name"] = contact.name
            except Exception:
                pass

        return user
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_add_reaction(chat_id: str, message_id: str, reaction: str) -> dict:
    """Add a reaction to a Teams chat message.

    Args:
        chat_id: The chat ID.
        message_id: The message ID.
        reaction: Reaction type: like, angry, sad, laugh, heart, surprised.
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_add_reaction

        return teams_add_reaction(chat_id, message_id, reaction)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_remove_reaction(chat_id: str, message_id: str, reaction: str) -> dict:
    """Remove a reaction from a Teams chat message.

    Args:
        chat_id: The chat ID.
        message_id: The message ID.
        reaction: Reaction type to remove: like, angry, sad, laugh, heart, surprised.
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_remove_reaction

        return teams_remove_reaction(chat_id, message_id, reaction)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_download_images(
    chat_id: str,
    local_dir: str = "/tmp/teams-images/",
    max_messages: int = 50,
) -> dict:
    """Download all images and file attachments from a Teams chat.

    Scans recent messages for:
    - Inline pasted images (hostedContents)
    - Direct Graph API image URLs
    - File attachments shared from SharePoint/OneDrive

    All files are saved to the local directory.

    Args:
        chat_id: The chat ID.
        local_dir: Local directory to save files into (default: /tmp/teams-images/).
        max_messages: Maximum messages to scan (default: 50).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_download_chat_images

        return teams_download_chat_images(chat_id, local_dir, max_messages)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_list_channel_files(
    team_id: str,
    channel_id: str,
    path: str = "/",
    max_results: int = 100,
) -> dict:
    """List files in a Teams channel's SharePoint folder.

    Each Teams channel has a backing SharePoint folder where shared files are stored.

    Args:
        team_id: The team ID.
        channel_id: The channel ID.
        path: Sub-path within the channel folder (default: root "/").
        max_results: Maximum items to return (default: 100).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_list_channel_files

        files = teams_list_channel_files(team_id, channel_id, path, max_results)
        return {
            "team_id": team_id,
            "channel_id": channel_id,
            "path": path,
            "count": len(files),
            "files": files,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_teams_download_channel_file(
    drive_id: str,
    item_id: str,
    local_dir: str = "/tmp/teams-files/",
) -> dict:
    """Download a file from a Teams channel's SharePoint folder.

    Use ms_teams_list_channel_files first to get the drive_id and item_id.

    Args:
        drive_id: The SharePoint drive ID (from ms_teams_list_channel_files).
        item_id: The file item ID (from ms_teams_list_channel_files).
        local_dir: Local directory to save into (default: /tmp/teams-files/).
    """
    try:
        from roost.mcp.ms_graph_helpers import teams_download_channel_file

        return teams_download_channel_file(drive_id, item_id, local_dir)
    except Exception as e:
        return {"error": str(e)}
