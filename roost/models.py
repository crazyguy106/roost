"""Pydantic models and enums."""

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class EnergyLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EffortEstimate(str, Enum):
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"


class TaskType(str, Enum):
    TASK = "task"
    MILESTONE = "milestone"
    SUBTASK = "subtask"
    OBJECTIVE = "objective"
    KEY_RESULT = "key_result"


class DocStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    FINAL = "final"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: Priority = Priority.MEDIUM
    deadline: datetime | None = None
    project_id: int | None = None
    parent_task_id: int | None = None
    task_type: TaskType = TaskType.TASK
    sort_order: int = 0
    energy_level: EnergyLevel = EnergyLevel.MEDIUM
    context_note: str = ""
    effort_estimate: EffortEstimate = EffortEstimate.MODERATE
    someday: bool = False
    focus_date: str | None = None

    @field_validator("deadline", mode="before")
    @classmethod
    def validate_deadline(cls, v):
        if isinstance(v, str):
            if not v:
                return None
            try:
                return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    return datetime.strptime(v, "%Y-%m-%d")
                except ValueError:
                    raise ValueError("deadline must be in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format")
        return v


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    priority: Priority | None = None
    deadline: datetime | None = None
    project_id: int | None = None
    parent_task_id: int | None = None
    task_type: TaskType | None = None
    sort_order: int | None = None
    energy_level: EnergyLevel | None = None
    context_note: str | None = None
    effort_estimate: EffortEstimate | None = None
    someday: bool | None = None
    focus_date: str | None = None

    @field_validator("deadline", mode="before")
    @classmethod
    def validate_deadline(cls, v):
        if isinstance(v, str):
            if not v:
                return None
            try:
                return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    return datetime.strptime(v, "%Y-%m-%d")
                except ValueError:
                    raise ValueError("deadline must be in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format")
        return v


class Task(BaseModel):
    id: int
    title: str
    description: str
    status: TaskStatus
    priority: Priority
    deadline: datetime | None
    project_id: int | None
    project_name: str | None = None
    parent_task_id: int | None = None
    task_type: str = "task"
    sort_order: int = 0
    subtask_count: int = 0
    subtask_done: int = 0
    urgency_score: float = 0.0
    last_worked_at: str | None = None
    context_note: str = ""
    energy_level: str = "medium"
    effort_estimate: str = "moderate"
    someday: int = 0
    focus_date: str | None = None
    notion_page_id: str | None = None
    user_id: int | None = None
    created_at: str
    updated_at: str


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    category: str = ""
    parent_project_id: int | None = None
    project_type: str = "project"
    entity_id: int | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    status: ProjectStatus | None = None
    color: str | None = None
    pinned: bool | None = None
    parent_project_id: int | None = None
    project_type: str | None = None
    entity_id: int | None = None


class Project(BaseModel):
    id: int
    name: str
    description: str
    category: str = ""
    status: str = "active"
    color: str = ""
    pinned: int = 0
    project_type: str = "project"
    entity_id: int | None = None
    entity_name: str | None = None
    parent_project_id: int | None = None
    parent_project_name: str | None = None
    notion_page_id: str | None = None
    user_id: int | None = None
    created_at: str
    updated_at: str
    task_count: int = 0
    children_count: int = 0


class NoteCreate(BaseModel):
    title: str = ""
    content: str
    tag: str = ""
    project_id: int | None = None


class Note(BaseModel):
    id: int
    title: str = ""
    content: str
    tag: str
    project_id: int | None
    project_name: str | None = None
    notion_page_id: str | None = None
    user_id: int | None = None
    created_at: str

    @property
    def display_title(self) -> str:
        """Return title if set, otherwise first line of content truncated to 80 chars."""
        if self.title:
            return self.title
        first_line = self.content.split("\n", 1)[0].strip()
        return (first_line[:77] + "...") if len(first_line) > 80 else first_line


class CommandLogEntry(BaseModel):
    id: int
    source: str
    command: str
    output: str
    exit_code: int | None
    user_id: str | None
    created_at: str


class CurriculumDocCreate(BaseModel):
    module_id: str
    doc_type: str  # lesson_plan, lab_guide, assessment, outline
    title: str
    content: str = ""
    status: DocStatus = DocStatus.DRAFT
    file_path: str | None = None
    task_id: int | None = None
    framework: str = ""
    curriculum_id: int | None = None


class CurriculumDoc(BaseModel):
    id: int
    module_id: str
    doc_type: str
    title: str
    content: str
    status: str
    file_path: str | None
    task_id: int | None
    framework: str
    curriculum_id: int | None = None
    created_at: str
    updated_at: str


# Phase 5: Curriculum auto-detect models

class CurriculumCreate(BaseModel):
    slug: str
    name: str
    description: str = ""
    total_hours: int = 0
    source_type: str = "manual"
    source_path: str | None = None
    project_id: int | None = None


class Curriculum(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    total_hours: int
    source_type: str
    source_path: str | None
    project_id: int | None
    is_active: int = 1
    notion_page_id: str | None = None
    created_at: str
    updated_at: str


class CurriculumModule(BaseModel):
    id: int
    curriculum_id: int
    module_id: str
    phase: int
    title: str
    hours: int
    core_tsc: str
    topics: str  # JSON string
    signature_lab: str
    sort_order: int
    notion_page_id: str | None = None


# Phase 4: Sharing models

class User(BaseModel):
    id: int
    name: str
    email: str = ""
    telegram_id: int | None = None
    role: str = "member"
    created_at: str


class UserCreate(BaseModel):
    name: str
    email: str = ""
    telegram_id: int | None = None
    role: str = "member"


class ShareLink(BaseModel):
    id: int
    token: str
    label: str = ""
    scope: str = "all"
    scope_id: int | None = None
    permissions: str = "read"
    expires_at: str | None = None
    created_at: str


class ShareLinkCreate(BaseModel):
    label: str = ""
    scope: str = "all"
    scope_id: int | None = None
    permissions: str = "read"
    expires_at: str | None = None


# Phase 8: Project Model V2 — Entities, Roles, Contacts, Assignments

class ProjectType(str, Enum):
    PROJECT = "project"
    PROGRAMME = "programme"
    INITIATIVE = "initiative"


# ── Entities ─────────────────────────────────────────────────────────

class EntityCreate(BaseModel):
    name: str
    description: str = ""
    notes: str = ""


class Entity(BaseModel):
    id: int
    name: str
    description: str = ""
    status: str = "active"
    notes: str = ""
    created_at: str
    updated_at: str
    project_count: int = 0
    contact_count: int = 0


class EntityUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    notes: str | None = None


# ── Roles ────────────────────────────────────────────────────────────

class RoleDefinition(BaseModel):
    code: str
    label: str
    description: str = ""
    sort_order: int = 0
    is_active: int = 1


# ── Contacts ─────────────────────────────────────────────────────────

class ContactIdentifier(BaseModel):
    id: int
    contact_id: int
    type: str  # email, phone, microsoft, google, telegram, linkedin, whatsapp, notion
    value: str
    label: str = ""  # work, personal, mobile, office
    is_primary: int = 0
    created_at: str = ""


class ContactIdentifierCreate(BaseModel):
    contact_id: int
    type: str
    value: str
    label: str = ""
    is_primary: int = 0


class ContactCreate(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    notes: str = ""


class Contact(BaseModel):
    id: int
    name: str
    email: str = ""  # deprecated - use identifiers; kept for backward compat
    phone: str = ""  # deprecated - use identifiers; kept for backward compat
    notes: str = ""
    entity_id: int | None = None
    entity_name: str | None = None
    notion_page_id: str | None = None  # deprecated - use identifiers
    identifiers: list[ContactIdentifier] = []
    created_at: str
    updated_at: str


class ContactUpdate(BaseModel):
    name: str | None = None
    email: str | None = None  # deprecated - use identifier CRUD; still works
    phone: str | None = None  # deprecated - use identifier CRUD; still works
    notes: str | None = None


# ── Contact Communications ────────────────────────────────────────────

class CommunicationCreate(BaseModel):
    contact_id: int
    comm_type: str
    subject: str = ""
    detail: str = ""
    occurred_at: str = ""  # ISO datetime; empty = now
    external_ref: str = ""
    external_type: str = ""


class Communication(BaseModel):
    id: int
    contact_id: int
    contact_name: str | None = None
    comm_type: str
    subject: str = ""
    detail: str = ""
    occurred_at: str
    external_ref: str = ""
    external_type: str = ""
    created_at: str


# ── Contact-Entity affiliations (M2M) ───────────────────────────────

class ContactEntityCreate(BaseModel):
    contact_id: int
    entity_id: int
    title: str = ""
    is_primary: int = 0


class ContactEntity(BaseModel):
    id: int
    contact_id: int
    entity_id: int
    title: str = ""
    is_primary: int = 0
    contact_name: str | None = None
    entity_name: str | None = None
    created_at: str


class ProjectAssignmentCreate(BaseModel):
    contact_id: int
    project_id: int
    role: str = "I"
    notes: str = ""


class ProjectAssignment(BaseModel):
    id: int
    contact_id: int
    project_id: int
    role: str
    notes: str = ""
    contact_name: str | None = None
    entity_name: str | None = None
    role_label: str | None = None
    project_name: str | None = None
    created_at: str


class TaskAssignmentCreate(BaseModel):
    contact_id: int
    task_id: int
    role: str = "R"
    notes: str = ""


class TaskAssignment(BaseModel):
    id: int
    contact_id: int
    task_id: int
    role: str
    notes: str = ""
    contact_name: str | None = None
    entity_name: str | None = None
    role_label: str | None = None
    task_title: str | None = None
    created_at: str


# ── Servers (SSH/Docker/K8s remote management) ──────────────────────

class ServerCreate(BaseModel):
    name: str
    host: str
    port: int = 22
    user: str = "root"
    key_path: str = ""
    password: str = ""
    description: str = ""
    tags: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        import re
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$", v):
            raise ValueError(
                "Server name must start with alphanumeric, contain only "
                "alphanumerics/dots/hyphens/underscores, max 63 chars"
            )
        return v


class ServerUpdate(BaseModel):
    host: str | None = None
    port: int | None = None
    user: str | None = None
    key_path: str | None = None
    password: str | None = None
    description: str | None = None
    tags: str | None = None
    is_active: bool | None = None


class Server(BaseModel):
    id: int
    name: str
    host: str
    port: int = 22
    user: str = "root"
    key_path: str = ""
    password: str = ""
    description: str = ""
    tags: str = ""
    is_active: int = 1
    last_connected_at: str | None = None
    created_at: str
    updated_at: str


# ── Claude Sessions (terminal session management) ────────────────

# ── OKR System ────────────────────────────────────────────────────

class CycleStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    SCORING = "scoring"
    CLOSED = "closed"


class OkrLevel(str, Enum):
    COMPANY = "company"
    DEPARTMENT = "department"
    PERSONAL = "personal"


class OkrType(str, Enum):
    COMMITTED = "committed"
    ASPIRATIONAL = "aspirational"


class OkrObjectiveStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SCORED = "scored"
    CANCELLED = "cancelled"


class MetricType(str, Enum):
    NUMBER = "number"
    PERCENTAGE = "percentage"
    CURRENCY = "currency"
    MILESTONE = "milestone"


class Confidence(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class KrStatus(str, Enum):
    ACTIVE = "active"
    SCORED = "scored"
    CANCELLED = "cancelled"


class OkrCycleCreate(BaseModel):
    name: str
    start_date: str
    end_date: str
    entity_id: int | None = None
    notes: str = ""


class OkrCycleUpdate(BaseModel):
    name: str | None = None
    status: CycleStatus | None = None
    notes: str | None = None


class OkrCycle(BaseModel):
    id: int
    name: str
    start_date: str
    end_date: str
    status: str = "planning"
    entity_id: int | None = None
    entity_name: str | None = None
    notes: str = ""
    created_at: str
    updated_at: str


class OkrObjectiveCreate(BaseModel):
    cycle_id: int
    title: str
    description: str = ""
    level: OkrLevel = OkrLevel.PERSONAL
    okr_type: OkrType = OkrType.COMMITTED
    parent_objective_id: int | None = None
    owner_contact_id: int | None = None
    entity_id: int | None = None
    project_id: int | None = None
    department: str = ""


class OkrObjectiveUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: OkrObjectiveStatus | None = None
    okr_type: OkrType | None = None
    parent_objective_id: int | None = None
    owner_contact_id: int | None = None
    entity_id: int | None = None
    project_id: int | None = None
    department: str | None = None
    score: float | None = None
    score_note: str | None = None
    sort_order: int | None = None


class OkrKeyResultCreate(BaseModel):
    objective_id: int
    title: str
    description: str = ""
    metric_type: MetricType = MetricType.NUMBER
    start_value: float = 0
    target_value: float = 1
    current_value: float = 0
    unit: str = ""
    owner_contact_id: int | None = None


class OkrKeyResultUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    metric_type: MetricType | None = None
    start_value: float | None = None
    target_value: float | None = None
    current_value: float | None = None
    unit: str | None = None
    confidence: Confidence | None = None
    status: KrStatus | None = None
    score: float | None = None
    score_note: str | None = None
    owner_contact_id: int | None = None
    sort_order: int | None = None


class ClaudeSessionCreate(BaseModel):
    name: str
    project_dir: str = "/home/dev/projects"
    tmux_session: str = "claude-dev"


class ClaudeSessionUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    project_dir: str | None = None


class ClaudeSession(BaseModel):
    id: int
    name: str
    tmux_session: str = "claude-dev"
    tmux_window: int | None = None
    project_dir: str = "/home/dev/projects"
    status: str = "active"
    last_connected_at: str | None = None
    created_at: str
    updated_at: str
