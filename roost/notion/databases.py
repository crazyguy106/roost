"""Auto-create Notion databases with matching property schemas.

Creates Tasks, Projects, Notes, Curriculum Docs, Programmes, and
Programme Modules databases under the configured parent page.
Database IDs are stored in the notion_sync_state table for future lookups.
"""

import logging

from roost.notion.client import get_client, rate_limited_call
from roost.database import get_connection
from roost.config import NOTION_PARENT_PAGE_ID

logger = logging.getLogger("roost.notion.databases")

# Database schemas: property definitions for each Notion DB
SCHEMAS = {
    "tasks": {
        "title": "Tasks",
        "properties": {
            "Title": {"title": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "todo", "color": "gray"},
                        {"name": "in_progress", "color": "blue"},
                        {"name": "done", "color": "green"},
                        {"name": "blocked", "color": "red"},
                    ]
                }
            },
            "Priority": {
                "select": {
                    "options": [
                        {"name": "low", "color": "gray"},
                        {"name": "medium", "color": "yellow"},
                        {"name": "high", "color": "orange"},
                        {"name": "urgent", "color": "red"},
                    ]
                }
            },
            "Deadline": {"date": {}},
            "Energy Level": {
                "select": {
                    "options": [
                        {"name": "low", "color": "gray"},
                        {"name": "medium", "color": "yellow"},
                        {"name": "high", "color": "green"},
                    ]
                }
            },
            "Context Note": {"rich_text": {}},
            "Urgency Score": {"number": {}},
            "Local ID": {"number": {}},
        },
    },
    "projects": {
        "title": "Projects",
        "properties": {
            "Name": {"title": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "active", "color": "green"},
                        {"name": "paused", "color": "yellow"},
                        {"name": "archived", "color": "gray"},
                    ]
                }
            },
            "Category": {"rich_text": {}},
            "Pinned": {"checkbox": {}},
            "Local ID": {"number": {}},
        },
    },
    "notes": {
        "title": "Notes",
        "properties": {
            "Content": {"title": {}},
            "Tag": {
                "select": {
                    "options": []  # Dynamic — populated from existing tags
                }
            },
            "Local ID": {"number": {}},
        },
    },
    "curriculum_docs": {
        "title": "Curriculum Docs",
        "properties": {
            "Title": {"title": {}},
            "Module": {"rich_text": {}},
            "Doc Type": {
                "select": {
                    "options": [
                        {"name": "lesson_plan", "color": "blue"},
                        {"name": "lab_guide", "color": "green"},
                        {"name": "assessment", "color": "orange"},
                        {"name": "assessment_a", "color": "orange"},
                        {"name": "assessment_b", "color": "yellow"},
                        {"name": "slides", "color": "purple"},
                        {"name": "outline", "color": "gray"},
                    ]
                }
            },
            "Status": {
                "select": {
                    "options": [
                        {"name": "draft", "color": "gray"},
                        {"name": "review", "color": "yellow"},
                        {"name": "final", "color": "green"},
                    ]
                }
            },
            "Framework": {"rich_text": {}},
            "Local ID": {"number": {}},
        },
    },
    "programmes": {
        "title": "Programmes",
        "properties": {
            "Name": {"title": {}},
            "Slug": {"rich_text": {}},
            "Tier": {
                "select": {
                    "options": [
                        {"name": "1", "color": "blue"},
                        {"name": "2", "color": "green"},
                        {"name": "3", "color": "purple"},
                        {"name": "Other", "color": "gray"},
                    ]
                }
            },
            "Provider": {"rich_text": {}},
            "Total Hours": {"number": {}},
            "Module Count": {"number": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "active", "color": "green"},
                        {"name": "inactive", "color": "gray"},
                    ]
                }
            },
            "Description": {"rich_text": {}},
            "Local ID": {"number": {}},
        },
    },
    "programme_modules": {
        "title": "Programme Modules",
        "needs_relation": "programmes",  # created after programmes DB exists
        "properties": {
            "Title": {"title": {}},
            "Module ID": {"rich_text": {}},
            "Phase": {"number": {}},
            "Phase Name": {"rich_text": {}},
            "Hours": {"number": {}},
            "Core TSC": {"rich_text": {}},
            "Topics": {"rich_text": {}},
            "Signature Lab": {"rich_text": {}},
            "Sort Order": {"number": {}},
            "Local ID": {"number": {}},
            # "Programme" relation added dynamically in ensure_databases()
        },
    },
}


# Order for database creation (programmes before programme_modules)
_CREATION_ORDER = [
    "tasks", "projects", "notes", "curriculum_docs",
    "programmes", "programme_modules",
]


def ensure_databases() -> dict[str, str]:
    """Ensure all Notion databases exist. Create missing ones.

    Returns dict mapping table_name -> notion_database_id.
    """
    client = get_client()
    if not client:
        logger.warning("Notion client not available, skipping database creation")
        return {}

    if not NOTION_PARENT_PAGE_ID:
        logger.warning("NOTION_PARENT_PAGE_ID not set, skipping database creation")
        return {}

    conn = get_connection()
    db_ids = {}

    for table_name in _CREATION_ORDER:
        schema = SCHEMAS[table_name]

        # Check if we already have the DB ID stored
        row = conn.execute(
            "SELECT last_notion_cursor FROM notion_sync_state WHERE table_name = ?",
            (table_name,),
        ).fetchone()

        if row and row["last_notion_cursor"]:
            db_ids[table_name] = row["last_notion_cursor"]
            continue

        # Build properties, injecting relation if needed
        properties = dict(schema["properties"])
        if schema.get("needs_relation"):
            rel_table = schema["needs_relation"]
            rel_db_id = db_ids.get(rel_table)
            if rel_db_id:
                # Look up the data_source_id for the related database
                try:
                    rel_db = rate_limited_call(
                        client.databases.retrieve, database_id=rel_db_id,
                    )
                    rel_ds_id = rel_db.get("data_sources", [{}])[0].get("id", "")
                except Exception:
                    rel_ds_id = ""
                rel_spec = {"database_id": rel_db_id, "single_property": {}}
                if rel_ds_id:
                    rel_spec["data_source_id"] = rel_ds_id
                properties["Programme"] = {"relation": rel_spec}

        # Create the database (SDK 2.7+ uses initial_data_source for properties)
        try:
            result = rate_limited_call(
                client.databases.create,
                parent={"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
                title=[{"type": "text", "text": {"content": schema["title"]}}],
                initial_data_source={"properties": properties},
            )
            notion_db_id = result["id"]
            db_ids[table_name] = notion_db_id

            # Store the DB ID in sync state
            conn.execute(
                """INSERT INTO notion_sync_state (table_name, last_notion_cursor)
                   VALUES (?, ?)
                   ON CONFLICT(table_name) DO UPDATE SET
                   last_notion_cursor = excluded.last_notion_cursor""",
                (table_name, notion_db_id),
            )
            conn.commit()
            logger.info("Created Notion database '%s' (id=%s)", schema["title"], notion_db_id)

        except Exception:
            logger.exception("Failed to create Notion database '%s'", table_name)

    conn.close()
    return db_ids


def get_database_id(table_name: str) -> str | None:
    """Get the Notion database ID for a given table."""
    conn = get_connection()
    row = conn.execute(
        "SELECT last_notion_cursor FROM notion_sync_state WHERE table_name = ?",
        (table_name,),
    ).fetchone()
    conn.close()
    return row["last_notion_cursor"] if row else None


def get_data_source_id(database_id: str) -> str | None:
    """Get the data_source_id for a Notion database.

    API v2025-09-03 requires querying via data_sources instead of databases.
    Each database has one primary data source whose ID we need for queries.
    """
    client = get_client()
    if not client:
        return None
    try:
        result = rate_limited_call(
            client.databases.retrieve, database_id=database_id,
        )
        sources = result.get("data_sources", [])
        return sources[0]["id"] if sources else None
    except Exception:
        logger.warning("Failed to get data_source_id for %s", database_id)
        return None
