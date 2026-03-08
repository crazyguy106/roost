"""SQLite database setup with WAL mode."""

import logging
import sqlite3
from pathlib import Path
from roost.config import DATABASE_PATH

logger = logging.getLogger("roost.database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo'
        CHECK (status IN ('todo', 'in_progress', 'done', 'blocked')),
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    deadline TEXT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tag TEXT DEFAULT '',
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS command_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    command TEXT NOT NULL,
    output TEXT DEFAULT '',
    exit_code INTEGER,
    user_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(deadline);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at);
CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at);
CREATE INDEX IF NOT EXISTS idx_notes_tag ON notes(tag);
CREATE INDEX IF NOT EXISTS idx_command_log_created ON command_log(created_at);
"""

# Phase 2 new tables (safe to run repeatedly via CREATE IF NOT EXISTS)
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS task_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    UNIQUE(task_id, depends_on_id)
);

CREATE TABLE IF NOT EXISTS curriculum_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft', 'review', 'final')),
    file_path TEXT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    framework TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS qualification_frameworks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    country TEXT DEFAULT '',
    file_path TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_curriculum_docs_module ON curriculum_docs(module_id);
CREATE INDEX IF NOT EXISTS idx_curriculum_docs_status ON curriculum_docs(status);
CREATE INDEX IF NOT EXISTS idx_task_deps_task ON task_dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_task_deps_depends ON task_dependencies(depends_on_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_energy ON tasks(energy_level);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category);
"""

# Phase 4: Sharing tables
SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    telegram_id INTEGER,
    role TEXT NOT NULL DEFAULT 'member'
        CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS project_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (role IN ('admin', 'editor', 'viewer')),
    UNIQUE(project_id, user_id)
);

CREATE TABLE IF NOT EXISTS share_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    label TEXT DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'all',
    scope_id INTEGER,
    permissions TEXT NOT NULL DEFAULT 'read',
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_share_links_token ON share_links(token);
CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_task_assignments_task ON task_assignments(task_id);
"""


# Phase 5: Curriculum auto-detect + Notion mirror
SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS curricula (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    total_hours INTEGER DEFAULT 0,
    source_type TEXT NOT NULL DEFAULT 'manual'
        CHECK (source_type IN ('manual', 'directory', 'project')),
    source_path TEXT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS curriculum_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    curriculum_id INTEGER NOT NULL REFERENCES curricula(id) ON DELETE CASCADE,
    module_id TEXT NOT NULL,
    phase INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    hours INTEGER DEFAULT 0,
    core_tsc TEXT DEFAULT '',
    topics TEXT DEFAULT '[]',
    signature_lab TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    UNIQUE(curriculum_id, module_id)
);

CREATE TABLE IF NOT EXISTS curriculum_phases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    curriculum_id INTEGER NOT NULL REFERENCES curricula(id) ON DELETE CASCADE,
    phase_number INTEGER NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(curriculum_id, phase_number)
);

CREATE TABLE IF NOT EXISTS notion_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    local_id INTEGER NOT NULL,
    notion_page_id TEXT,
    direction TEXT NOT NULL CHECK (direction IN ('push', 'pull')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'success', 'failed', 'conflict')),
    error_message TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notion_sync_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL UNIQUE,
    last_synced_at TEXT,
    last_notion_cursor TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_curricula_slug ON curricula(slug);
CREATE INDEX IF NOT EXISTS idx_curriculum_modules_curriculum ON curriculum_modules(curriculum_id);
CREATE INDEX IF NOT EXISTS idx_curriculum_phases_curriculum ON curriculum_phases(curriculum_id);
CREATE INDEX IF NOT EXISTS idx_notion_sync_log_table ON notion_sync_log(table_name, local_id);
"""


# Phase 6: Gmail OAuth tokens
SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS oauth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    access_token TEXT,
    refresh_token TEXT NOT NULL,
    token_type TEXT DEFAULT 'Bearer',
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider, scope)
);

CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens(provider);
"""


# Phase 7: Dropbox + Otter voice transcripts
SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS otter_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    dropbox_audio_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'done', 'failed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_otter_pending_status ON otter_pending(status);
"""


# Phase 8: Project Model V2 — hierarchy, contacts, RACI assignments, OKR
SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS role_definitions (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    entity_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    notion_page_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS project_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'I',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contact_id, project_id, role)
);

CREATE TABLE IF NOT EXISTS raci_task_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'R',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contact_id, task_id, role)
);

CREATE INDEX IF NOT EXISTS idx_project_assignments_project ON project_assignments(project_id);
CREATE INDEX IF NOT EXISTS idx_project_assignments_contact ON project_assignments(contact_id);
CREATE INDEX IF NOT EXISTS idx_raci_task_assignments_task ON raci_task_assignments(task_id);
CREATE INDEX IF NOT EXISTS idx_raci_task_assignments_contact ON raci_task_assignments(contact_id);
CREATE INDEX IF NOT EXISTS idx_projects_parent ON projects(parent_project_id);
CREATE INDEX IF NOT EXISTS idx_projects_type ON projects(project_type);
"""

_SEED_ROLES = [
    ("R", "Responsible", "Does the work", 1),
    ("A", "Accountable", "Owns the outcome, makes decisions", 2),
    ("C", "Consulted", "Gives input, expertise", 3),
    ("I", "Informed", "Kept in the loop", 4),
    ("V", "Vendor", "Provides service/product (paid)", 5),
    ("S", "Subcontractor", "Contracted to deliver specific work", 6),
]


def _seed_roles(conn: sqlite3.Connection) -> None:
    """Seed default role definitions (idempotent)."""
    for code, label, desc, order in _SEED_ROLES:
        try:
            conn.execute(
                "INSERT INTO role_definitions (code, label, description, sort_order) VALUES (?, ?, ?, ?)",
                (code, label, desc, order),
            )
        except sqlite3.IntegrityError:
            pass  # Already exists


# Phase 8b: Separate entities table, contact_entities M2M
SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contact_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    title TEXT DEFAULT '',
    is_primary INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contact_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status);
CREATE INDEX IF NOT EXISTS idx_contact_entities_contact ON contact_entities(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_entities_entity ON contact_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_projects_entity ON projects(entity_id);
"""


def _migrate_v8_data(conn: sqlite3.Connection) -> None:
    """Migrate entity data from projects table to entities table.

    Steps:
    1. Copy project_type='entity' rows → entities table (preserve IDs)
    2. Add entity_id column to projects
    3. Set entity_id on initiatives that were children of entities
    4. Migrate contact entity_id → contact_entities M2M
    5. Migrate entity-level project_assignments → contact_entities
    6. Clear parent_project_id for initiatives that pointed to entities
    7. Delete entity rows from projects
    """
    # Check if migration already done (entities table has data)
    count = conn.execute("SELECT COUNT(*) as cnt FROM entities").fetchone()["cnt"]
    if count > 0:
        return  # Already migrated

    # Check if there are entity rows to migrate
    entity_rows = conn.execute(
        "SELECT * FROM projects WHERE project_type = 'entity'"
    ).fetchall()
    if not entity_rows:
        return  # Nothing to migrate

    logger.info("Migrating %d entities from projects → entities table", len(entity_rows))

    # 1. Copy entity rows to entities table (preserve IDs)
    for row in entity_rows:
        conn.execute(
            """INSERT INTO entities (id, name, description, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (row["id"], row["name"], row["description"] or "",
             row["status"] or "active",
             row["created_at"], row["updated_at"]),
        )

    # 2. Set entity_id on projects that were children of entity rows
    entity_ids = [r["id"] for r in entity_rows]
    for eid in entity_ids:
        conn.execute(
            "UPDATE projects SET entity_id = ?, parent_project_id = NULL WHERE parent_project_id = ?",
            (eid, eid),
        )

    # 3. Migrate contacts.entity_id → contact_entities
    contact_rows = conn.execute(
        "SELECT id, entity_id FROM contacts WHERE entity_id IS NOT NULL"
    ).fetchall()
    for cr in contact_rows:
        if cr["entity_id"] in entity_ids:
            try:
                conn.execute(
                    "INSERT INTO contact_entities (contact_id, entity_id, is_primary) VALUES (?, ?, 1)",
                    (cr["id"], cr["entity_id"]),
                )
            except sqlite3.IntegrityError:
                pass

    # 4. Migrate entity-level project_assignments → contact_entities
    for eid in entity_ids:
        asgn_rows = conn.execute(
            "SELECT * FROM project_assignments WHERE project_id = ?", (eid,)
        ).fetchall()
        for ar in asgn_rows:
            try:
                conn.execute(
                    "INSERT INTO contact_entities (contact_id, entity_id, title, is_primary) VALUES (?, ?, ?, 0)",
                    (ar["contact_id"], eid, ar["role"]),
                )
            except sqlite3.IntegrityError:
                # Already exists from step 3, update title
                conn.execute(
                    "UPDATE contact_entities SET title = ? WHERE contact_id = ? AND entity_id = ?",
                    (ar["role"], ar["contact_id"], eid),
                )
            # Remove the old project_assignment
            conn.execute("DELETE FROM project_assignments WHERE id = ?", (ar["id"],))

    # 5. Delete entity rows from projects
    for eid in entity_ids:
        conn.execute("DELETE FROM projects WHERE id = ?", (eid,))

    conn.commit()
    logger.info("Entity migration complete: %d entities moved, projects updated", len(entity_rows))


# Phase 9: Neurodivergent-friendly features — user settings + indexes
SCHEMA_V9 = """
CREATE TABLE IF NOT EXISTS user_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_focus ON tasks(focus_date);
CREATE INDEX IF NOT EXISTS idx_tasks_someday ON tasks(someday);
CREATE INDEX IF NOT EXISTS idx_tasks_effort ON tasks(effort_estimate);
"""


# Phase 10: Neurodivergent features Phase 2 — activity log, routines
SCHEMA_V10 = """
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_date ON activity_log(created_at);
CREATE INDEX IF NOT EXISTS idx_activity_task ON activity_log(task_id);

CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    time_of_day TEXT DEFAULT 'morning',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS routine_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id INTEGER NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS routine_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_item_id INTEGER NOT NULL REFERENCES routine_items(id) ON DELETE CASCADE,
    completed_date TEXT NOT NULL,
    UNIQUE(routine_item_id, completed_date)
);

CREATE INDEX IF NOT EXISTS idx_routine_items_routine ON routine_items(routine_id);
CREATE INDEX IF NOT EXISTS idx_routine_completions_date ON routine_completions(completed_date);
"""


# Phase 11: SSH / Docker / K8s remote server management
SCHEMA_V11 = """
CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    user TEXT NOT NULL DEFAULT 'root',
    key_path TEXT DEFAULT '',
    description TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    last_connected_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_servers_name ON servers(name);
CREATE INDEX IF NOT EXISTS idx_servers_active ON servers(is_active);
"""


# Phase 12: Contact communication history
SCHEMA_V12 = """
CREATE TABLE IF NOT EXISTS contact_communications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    comm_type TEXT NOT NULL,
    subject TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    external_ref TEXT DEFAULT '',
    external_type TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_contact_comms_contact ON contact_communications(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_comms_occurred ON contact_communications(occurred_at);
CREATE INDEX IF NOT EXISTS idx_contact_comms_extref ON contact_communications(external_ref);
"""


# Phase 13: Claude Code terminal sessions
SCHEMA_V13 = """
CREATE TABLE IF NOT EXISTS claude_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    tmux_session TEXT NOT NULL DEFAULT 'claude-dev',
    tmux_window INTEGER,
    project_dir TEXT NOT NULL DEFAULT '/home/dev/projects',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'closed')),
    last_connected_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_claude_sessions_status ON claude_sessions(status);
CREATE INDEX IF NOT EXISTS idx_claude_sessions_name ON claude_sessions(name);
"""


"""


# Phase 15: Scheduled emails — persistent queue for delayed sending
SCHEMA_V15 = """
CREATE TABLE IF NOT EXISTS scheduled_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'gmail'
        CHECK (provider IN ('gmail', 'microsoft')),
    to_addr TEXT NOT NULL,
    cc TEXT DEFAULT '',
    bcc TEXT DEFAULT '',
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    thread_id TEXT DEFAULT '',
    reply_to_id TEXT DEFAULT '',
    attachment_paths TEXT DEFAULT '[]',
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
    sent_at TEXT,
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scheduled_emails_status ON scheduled_emails(status);
CREATE INDEX IF NOT EXISTS idx_scheduled_emails_scheduled ON scheduled_emails(scheduled_at);
"""


# Phase 16: OKR system — cycles, objectives, key results, task links
SCHEMA_V16 = """
CREATE TABLE IF NOT EXISTS okr_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planning'
        CHECK (status IN ('planning', 'active', 'scoring', 'closed')),
    entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS okr_objectives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES okr_cycles(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    level TEXT NOT NULL DEFAULT 'personal'
        CHECK (level IN ('company', 'department', 'personal')),
    okr_type TEXT NOT NULL DEFAULT 'committed'
        CHECK (okr_type IN ('committed', 'aspirational')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'scored', 'cancelled')),
    parent_objective_id INTEGER REFERENCES okr_objectives(id) ON DELETE SET NULL,
    owner_contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    department TEXT DEFAULT '',
    score REAL,
    score_note TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS okr_key_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    objective_id INTEGER NOT NULL REFERENCES okr_objectives(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    metric_type TEXT NOT NULL DEFAULT 'number'
        CHECK (metric_type IN ('number', 'percentage', 'currency', 'milestone')),
    start_value REAL DEFAULT 0,
    target_value REAL DEFAULT 1,
    current_value REAL DEFAULT 0,
    unit TEXT DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'green'
        CHECK (confidence IN ('green', 'yellow', 'red')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'scored', 'cancelled')),
    score REAL,
    score_note TEXT DEFAULT '',
    owner_contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS okr_task_links (
    key_result_id INTEGER NOT NULL REFERENCES okr_key_results(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    UNIQUE(key_result_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_okr_cycles_status ON okr_cycles(status);
CREATE INDEX IF NOT EXISTS idx_okr_cycles_entity ON okr_cycles(entity_id);
CREATE INDEX IF NOT EXISTS idx_okr_objectives_cycle ON okr_objectives(cycle_id);
CREATE INDEX IF NOT EXISTS idx_okr_objectives_level ON okr_objectives(level);
CREATE INDEX IF NOT EXISTS idx_okr_objectives_status ON okr_objectives(status);
CREATE INDEX IF NOT EXISTS idx_okr_objectives_parent ON okr_objectives(parent_objective_id);
CREATE INDEX IF NOT EXISTS idx_okr_objectives_entity ON okr_objectives(entity_id);
CREATE INDEX IF NOT EXISTS idx_okr_key_results_objective ON okr_key_results(objective_id);
CREATE INDEX IF NOT EXISTS idx_okr_key_results_status ON okr_key_results(status);
CREATE INDEX IF NOT EXISTS idx_okr_task_links_kr ON okr_task_links(key_result_id);
CREATE INDEX IF NOT EXISTS idx_okr_task_links_task ON okr_task_links(task_id);
"""


# Phase 17: Otter.ai meeting summary ingest tracking
SCHEMA_V17 = """
CREATE TABLE IF NOT EXISTS otter_processed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_otter_processed_filename ON otter_processed(filename);
"""

SCHEMA_V18 = """
CREATE TABLE IF NOT EXISTS contact_identifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    value TEXT NOT NULL,
    label TEXT DEFAULT '',
    is_primary INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contact_id, type, value)
);

CREATE INDEX IF NOT EXISTS idx_contact_identifiers_contact ON contact_identifiers(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_identifiers_type_value ON contact_identifiers(type, value);
"""


def get_connection() -> sqlite3.Connection:
    """Get a new database connection with WAL mode and row factory."""
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def db_connection():
    """Context manager that yields a connection and auto-closes it.

    Usage:
        with db_connection() as conn:
            conn.execute(...)
    """
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        conn = get_connection()
        try:
            yield conn
        finally:
            conn.close()

    return _ctx()


def _migrate_db(conn: sqlite3.Connection) -> None:
    """Safe ALTER TABLE migrations for new columns on tasks.

    Each ALTER is wrapped in try/except so it's idempotent — running
    on an already-migrated DB is a no-op.
    """
    migrations = [
        ("tasks", "parent_task_id", "INTEGER REFERENCES tasks(id) ON DELETE CASCADE"),
        ("tasks", "task_type", "TEXT DEFAULT 'task'"),
        ("tasks", "sort_order", "INTEGER DEFAULT 0"),
        # Phase 1: Smart triage
        ("tasks", "urgency_score", "REAL DEFAULT 0"),
        ("tasks", "last_worked_at", "TEXT"),
        ("tasks", "context_note", "TEXT DEFAULT ''"),
        ("tasks", "energy_level", "TEXT DEFAULT 'medium'"),
        # Phase 3: Project lifecycle
        ("projects", "category", "TEXT DEFAULT ''"),
        ("projects", "status", "TEXT DEFAULT 'active'"),
        ("projects", "color", "TEXT DEFAULT ''"),
        ("projects", "pinned", "INTEGER DEFAULT 0"),
        # Phase 4: Task assignment
        ("tasks", "assigned_to", "INTEGER"),
        # Phase 5: Curriculum auto-detect (projects exists in SCHEMA)
        ("projects", "project_type", "TEXT DEFAULT ''"),
        # Phase 5: Notion mirror (tasks, projects, notes exist in SCHEMA)
        ("tasks", "notion_page_id", "TEXT"),
        ("projects", "notion_page_id", "TEXT"),
        ("notes", "notion_page_id", "TEXT"),
        # Project hierarchy + model v2
        ("projects", "parent_project_id", "INTEGER REFERENCES projects(id) ON DELETE SET NULL"),
        ("projects", "project_type", "TEXT DEFAULT 'project'"),
        # Phase 8b: entities refactor
        ("projects", "entity_id", "INTEGER REFERENCES entities(id) ON DELETE SET NULL"),
        # Phase 9: Neurodivergent-friendly task features
        ("tasks", "focus_date", "TEXT"),
        ("tasks", "effort_estimate", "TEXT DEFAULT 'moderate'"),
        ("tasks", "someday", "INTEGER DEFAULT 0"),
        # Phase 12: Task-linked activity tracking
        ("activity_log", "tool_name", "TEXT DEFAULT ''"),
        ("activity_log", "artifact_type", "TEXT DEFAULT ''"),
        ("activity_log", "artifact_ref", "TEXT DEFAULT ''"),
    ]
    for table, column, col_def in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            logger.info("Migration: added %s.%s", table, column)
        except sqlite3.OperationalError:
            pass  # Column already exists


def _migrate_v2_tables(conn: sqlite3.Connection) -> None:
    """Migrations for tables created in SCHEMA_V2 (curriculum_docs, etc).

    Must run AFTER SCHEMA_V2 has created these tables.
    """
    migrations = [
        ("curriculum_docs", "curriculum_id", "INTEGER"),
        ("curriculum_docs", "notion_page_id", "TEXT"),
    ]
    for table, column, col_def in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            logger.info("Migration (V2 tables): added %s.%s", table, column)
        except sqlite3.OperationalError:
            pass  # Column already exists


def _migrate_v4_tables(conn: sqlite3.Connection) -> None:
    """Migrations for tables created in SCHEMA_V4 (curricula, curriculum_modules).

    Adds notion_page_id columns for Notion programme dashboard sync.
    Must run AFTER SCHEMA_V4 has created these tables.
    """
    migrations = [
        ("curricula", "notion_page_id", "TEXT"),
        ("curriculum_modules", "notion_page_id", "TEXT"),
    ]
    for table, column, col_def in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            logger.info("Migration (V4 tables): added %s.%s", table, column)
        except sqlite3.OperationalError:
            pass  # Column already exists


def _migrate_multi_tenant_user_id(conn: sqlite3.Connection) -> None:
    """Phase 18b: Add user_id to personal data tables for multi-tenancy.

    Tables: tasks, notes, activity_log, user_settings.
    Existing rows assigned to the owner user.
    """
    owner_row = conn.execute(
        "SELECT id FROM users WHERE role = 'owner' ORDER BY id LIMIT 1"
    ).fetchone()
    owner_id = owner_row["id"] if owner_row else None

    # Add user_id column to personal tables (idempotent)
    personal_tables = ["tasks", "notes", "activity_log"]
    for table in personal_tables:
        cols = [info[1] for info in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "user_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)")
            if owner_id:
                conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (owner_id,))
            logger.info("Migration: added %s.user_id (backfilled to user %s)", table, owner_id)

    # Add user_id to projects (owner column)
    proj_cols = [info[1] for info in conn.execute("PRAGMA table_info(projects)").fetchall()]
    if "user_id" not in proj_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN user_id INTEGER REFERENCES users(id)")
        if owner_id:
            conn.execute("UPDATE projects SET user_id = ? WHERE user_id IS NULL", (owner_id,))
        logger.info("Migration: added projects.user_id (backfilled to user %s)", owner_id)

    # Rebuild user_settings with (user_id, key) composite primary key
    settings_cols = [info[1] for info in conn.execute("PRAGMA table_info(user_settings)").fetchall()]
    if "user_id" not in settings_cols:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings_v2 (
                user_id INTEGER NOT NULL REFERENCES users(id),
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, key)
            )
        """)
        if owner_id:
            conn.execute("""
                INSERT INTO user_settings_v2 (user_id, key, value, updated_at)
                SELECT ?, key, value, updated_at FROM user_settings
            """, (owner_id,))
        conn.execute("DROP TABLE user_settings")
        conn.execute("ALTER TABLE user_settings_v2 RENAME TO user_settings")
        logger.info("Migration: rebuilt user_settings with (user_id, key) PK")

    # Create indexes for user_id filtering
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id)",
    ]:
        conn.execute(idx_sql)

    conn.commit()


def _migrate_oauth_tokens_user_id(conn: sqlite3.Connection) -> None:
    """Add user_id to oauth_tokens and rebuild with new unique constraint.

    Phase 18: Multi-tenancy. Tokens keyed by (user_id, provider, scope)
    instead of (provider, scope). Existing tokens assigned to owner user.
    """
    cols = [info[1] for info in conn.execute("PRAGMA table_info(oauth_tokens)").fetchall()]
    if "user_id" in cols:
        return  # Already migrated

    logger.info("Migrating oauth_tokens: adding user_id column")

    # Find the owner user to assign existing tokens to
    owner_row = conn.execute(
        "SELECT id FROM users WHERE role = 'owner' ORDER BY id LIMIT 1"
    ).fetchone()
    owner_id = owner_row["id"] if owner_row else None

    if not owner_id:
        # No users yet - just add the column, tokens will be orphaned until
        # a user is created and re-auths
        logger.warning("No owner user found; adding user_id column without backfill")

    # Rebuild the table with user_id and new unique constraint
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            provider TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            access_token TEXT,
            refresh_token TEXT NOT NULL,
            token_type TEXT DEFAULT 'Bearer',
            expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, provider, scope)
        )
    """)

    # Copy existing tokens, assigning to owner
    conn.execute("""
        INSERT INTO oauth_tokens_v2 (id, user_id, provider, scope, access_token,
            refresh_token, token_type, expires_at, created_at, updated_at)
        SELECT id, ?, provider, scope, access_token,
            refresh_token, token_type, expires_at, created_at, updated_at
        FROM oauth_tokens
    """, (owner_id,))

    conn.execute("DROP TABLE oauth_tokens")
    conn.execute("ALTER TABLE oauth_tokens_v2 RENAME TO oauth_tokens")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens(provider)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oauth_tokens_user ON oauth_tokens(user_id)")
    conn.commit()
    logger.info("oauth_tokens migrated: user_id added, %s assigned as owner",
                owner_id or "(none)")


def _migrate_multi_tenant_phase2(conn: sqlite3.Connection) -> None:
    """Phase 18c: Add user_id to routines, scheduled_emails, servers, claude_sessions.

    Routines table rebuilt for UNIQUE(user_id, name) constraint.
    Other tables get simple ALTER TABLE ADD COLUMN.
    """
    owner_row = conn.execute(
        "SELECT id FROM users WHERE role = 'owner' ORDER BY id LIMIT 1"
    ).fetchone()
    owner_id = owner_row["id"] if owner_row else None

    # -- Routines: rebuild for UNIQUE(user_id, name) --
    routine_cols = [info[1] for info in conn.execute("PRAGMA table_info(routines)").fetchall()]
    if "user_id" not in routine_cols:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS routines_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                time_of_day TEXT DEFAULT 'morning',
                user_id INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, name)
            )
        """)
        if owner_id:
            conn.execute("""
                INSERT INTO routines_v2 (id, name, time_of_day, user_id, created_at)
                SELECT id, name, time_of_day, ?, created_at FROM routines
            """, (owner_id,))
        else:
            conn.execute("""
                INSERT INTO routines_v2 (id, name, time_of_day, created_at)
                SELECT id, name, time_of_day, created_at FROM routines
            """)
        conn.execute("DROP TABLE routines")
        conn.execute("ALTER TABLE routines_v2 RENAME TO routines")
        logger.info("Migration: rebuilt routines with (user_id, name) UNIQUE")

    # -- Simple ALTER TABLE for other personal tables --
    simple_tables = ["scheduled_emails", "servers", "claude_sessions"]
    for table in simple_tables:
        try:
            cols = [info[1] for info in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        except Exception:
            continue  # Table might not exist yet
        if "user_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)")
            if owner_id:
                conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (owner_id,))
            logger.info("Migration: added %s.user_id (backfilled to user %s)", table, owner_id)

    # Indexes
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_routines_user ON routines(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_emails_user ON scheduled_emails(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_servers_user ON servers(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_claude_sessions_user ON claude_sessions(user_id)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    conn.commit()


def _migrate_notes_title(conn: sqlite3.Connection) -> None:
    """Phase 19: Add title column to notes table."""
    cols = [info[1] for info in conn.execute("PRAGMA table_info(notes)").fetchall()]
    if "title" not in cols:
        conn.execute("ALTER TABLE notes ADD COLUMN title TEXT DEFAULT ''")
        conn.commit()
        logger.info("Migration: added notes.title")


def _migrate_contact_identifiers(conn: sqlite3.Connection) -> None:
    """Phase 20: Populate contact_identifiers from existing email/phone columns."""
    # Check if migration already ran (any rows exist)
    count = conn.execute("SELECT COUNT(*) FROM contact_identifiers").fetchone()[0]
    if count > 0:
        return  # Already migrated

    contacts = conn.execute(
        "SELECT id, email, phone, notion_page_id FROM contacts"
    ).fetchall()

    migrated = 0
    for c in contacts:
        if c["email"] and c["email"].strip():
            conn.execute(
                "INSERT OR IGNORE INTO contact_identifiers (contact_id, type, value, label, is_primary) "
                "VALUES (?, 'email', ?, 'work', 1)",
                (c["id"], c["email"].strip()),
            )
            migrated += 1
        if c["phone"] and c["phone"].strip():
            conn.execute(
                "INSERT OR IGNORE INTO contact_identifiers (contact_id, type, value, label, is_primary) "
                "VALUES (?, 'phone', ?, '', 1)",
                (c["id"], c["phone"].strip()),
            )
            migrated += 1
        if c["notion_page_id"] and c["notion_page_id"].strip():
            conn.execute(
                "INSERT OR IGNORE INTO contact_identifiers (contact_id, type, value, label, is_primary) "
                "VALUES (?, 'notion', ?, '', 1)",
                (c["id"], c["notion_page_id"].strip()),
            )
            migrated += 1

    if migrated:
        conn.commit()
        logger.info("Migration: populated %d contact identifiers from legacy columns", migrated)


def _migrate_oauth_tokens_account(conn: sqlite3.Connection) -> None:
    """Phase 21: Add account column to oauth_tokens for multi-account Google OAuth.

    Allows multiple Google accounts (e.g. work + personal) by keying tokens
    on (user_id, provider, scope, account) instead of (user_id, provider, scope).
    """
    cols = [info[1] for info in conn.execute("PRAGMA table_info(oauth_tokens)").fetchall()]
    if "account" in cols:
        return  # Already migrated

    logger.info("Migrating oauth_tokens: adding account column for multi-account support")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens_v3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            provider TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            account TEXT NOT NULL DEFAULT '',
            access_token TEXT,
            refresh_token TEXT NOT NULL,
            token_type TEXT DEFAULT 'Bearer',
            expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, provider, scope, account)
        )
    """)

    conn.execute("""
        INSERT INTO oauth_tokens_v3 (id, user_id, provider, scope, account,
            access_token, refresh_token, token_type, expires_at, created_at, updated_at)
        SELECT id, user_id, provider, scope, '',
            access_token, refresh_token, token_type, expires_at, created_at, updated_at
        FROM oauth_tokens
    """)

    conn.execute("DROP TABLE oauth_tokens")
    conn.execute("ALTER TABLE oauth_tokens_v3 RENAME TO oauth_tokens")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens(provider)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oauth_tokens_user ON oauth_tokens(user_id)")
    conn.commit()
    logger.info("oauth_tokens migrated: account column added")


def init_db() -> None:
    """Initialize the database schema and run migrations."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    _migrate_db(conn)
    conn.executescript(SCHEMA_V2)
    _migrate_v2_tables(conn)
    conn.executescript(SCHEMA_V3)
    conn.executescript(SCHEMA_V4)
    _migrate_v4_tables(conn)
    conn.executescript(SCHEMA_V5)
    conn.executescript(SCHEMA_V6)
    conn.executescript(SCHEMA_V7)
    _seed_roles(conn)
    conn.executescript(SCHEMA_V8)
    _migrate_v8_data(conn)
    conn.executescript(SCHEMA_V9)
    conn.executescript(SCHEMA_V10)
    conn.executescript(SCHEMA_V11)
    conn.executescript(SCHEMA_V12)
    conn.executescript(SCHEMA_V13)
    conn.executescript(SCHEMA_V14)
    conn.executescript(SCHEMA_V15)
    conn.executescript(SCHEMA_V16)
    conn.executescript(SCHEMA_V17)
    # V11 migration: add password column to servers
    try:
        conn.execute("ALTER TABLE servers ADD COLUMN password TEXT DEFAULT ''")
        logger.info("Migration: added servers.password")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Drop legacy entity_id from contacts (replaced by contact_entities M2M)
    cols = [info[1] for info in conn.execute("PRAGMA table_info(contacts)").fetchall()]
    if "entity_id" in cols:
        conn.execute("DROP INDEX IF EXISTS idx_contacts_entity")
        conn.execute("ALTER TABLE contacts DROP COLUMN entity_id")
        logger.info("Dropped legacy contacts.entity_id column")

    # Seed sort_order for active tasks that still have 0
    unordered = conn.execute(
        """SELECT COUNT(*) as cnt FROM tasks
           WHERE status != 'done' AND (someday = 0 OR someday IS NULL)
                 AND sort_order = 0"""
    ).fetchone()["cnt"]
    if unordered > 0:
        rows = conn.execute(
            """SELECT id FROM tasks
               WHERE status != 'done' AND (someday = 0 OR someday IS NULL)
               ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2 ELSE 3 END,
                        urgency_score DESC, created_at DESC"""
        ).fetchall()
        for i, row in enumerate(rows, 1):
            conn.execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (i, row["id"]))
        logger.info("Seeded sort_order for %d active tasks", len(rows))

    conn.commit()

    # Phase 18: Multi-tenancy - add user_id to oauth_tokens
    # Must run after SCHEMA_V3 (users table) and SCHEMA_V5 (oauth_tokens table)
    _migrate_oauth_tokens_user_id(conn)

    # Phase 18b: Multi-tenancy - add user_id to personal data tables
    _migrate_multi_tenant_user_id(conn)

    # Phase 18c: Multi-tenancy - routines, scheduled_emails, servers, sessions
    _migrate_multi_tenant_phase2(conn)

    # Phase 19: Add title column to notes
    _migrate_notes_title(conn)

    # Phase 20: Contact identifiers (flexible multi-value contact info)
    conn.executescript(SCHEMA_V18)
    _migrate_contact_identifiers(conn)

    # Phase 21: Multi-account Google OAuth - add account column to oauth_tokens
    _migrate_oauth_tokens_account(conn)

    conn.close()


# Initialize on import
init_db()


