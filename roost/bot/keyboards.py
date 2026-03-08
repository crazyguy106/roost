"""Central inline keyboard builder for the Telegram bot.

All callback data follows the pattern: prefix:arg1:arg2:...
and stays within Telegram's 64-byte limit.

Patterns:
  help:<category>         — Help menu navigation
  tasks:<filter>:<page>   — Task list pagination
  task:<id>:<action>      — Task detail actions
  settime:<id>:<step>:... — Time picker wizard
  role:p:<pid>:<cid>:<code> — Role picker for project assignment
  role:t:<tid>:<cid>:<code> — Role picker for task assignment
  noop                    — Display-only (no action)
"""

import calendar as cal_mod
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ── Help menu ────────────────────────────────────────────────────────

HELP_CATEGORIES = [
    ("triage", "Smart Triage"),
    ("tasks", "Tasks"),
    ("calendar", "Calendar"),
    ("focus", "Projects"),
    ("people", "People"),
    ("notes", "Notes"),
    ("currai", "Curriculum AI"),
    ("currkb", "Curriculum KB"),
    ("presentations", "Presentations"),
    ("capture", "Capture"),
    ("email", "Email"),
    ("integrations", "Integrations"),
    ("files", "Files & Git"),
]


def help_menu_keyboard() -> InlineKeyboardMarkup:
    """9 category buttons arranged in rows of 2."""
    buttons = [
        InlineKeyboardButton(label, callback_data=f"help:{key}")
        for key, label in HELP_CATEGORIES
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def help_back_keyboard() -> InlineKeyboardMarkup:
    """Single 'Back to menu' button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u2b05 Back to menu", callback_data="help:menu")]
    ])


# ── Task list ────────────────────────────────────────────────────────

TASK_FILTERS = [
    ("all", "All"),
    ("todo", "Todo"),
    ("wip", "In Progress"),
    ("done", "Done"),
]


def task_list_keyboard(
    current_filter: str,
    page: int,
    total_pages: int,
    task_ids: list[int],
    task_positions: list[int] | None = None,
) -> InlineKeyboardMarkup:
    """Filter row + per-task view buttons + prev/next pagination.

    task_positions: optional list of display numbers (sort_order) matching
    task_ids. When provided, buttons show the position number instead of
    the raw ID so they match the list display.
    """
    rows = []

    # Filter row — mark active filter with >
    filter_row = []
    for key, label in TASK_FILTERS:
        display = f">{label}" if key == current_filter else label
        filter_row.append(
            InlineKeyboardButton(display, callback_data=f"tasks:{key}:1")
        )
    rows.append(filter_row)

    # Task buttons — 2 per row, label matches displayed number
    task_buttons = []
    for i, tid in enumerate(task_ids):
        if task_positions and i < len(task_positions) and task_positions[i]:
            label = f"{task_positions[i]}."
        else:
            label = f"#{tid}"
        task_buttons.append(
            InlineKeyboardButton(label, callback_data=f"task:{tid}:view")
        )
    for i in range(0, len(task_buttons), 2):
        rows.append(task_buttons[i:i + 2])

    # Pagination row
    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton("\u25c0 Prev", callback_data=f"tasks:{current_filter}:{page - 1}")
        )
    nav_row.append(
        InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop")
    )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Next \u25b6", callback_data=f"tasks:{current_filter}:{page + 1}")
        )
    rows.append(nav_row)

    return InlineKeyboardMarkup(rows)


# ── Task detail ──────────────────────────────────────────────────────

def task_detail_keyboard(task_id: int, status: str) -> InlineKeyboardMarkup:
    """Action buttons for a single task view."""
    rows = []

    # Row 1: status actions
    action_row = []
    if status != "done":
        action_row.append(
            InlineKeyboardButton("\u2705 Done", callback_data=f"task:{task_id}:done")
        )
    else:
        action_row.append(
            InlineKeyboardButton("\u21a9 Reopen", callback_data=f"task:{task_id}:reopen")
        )
    action_row.append(
        InlineKeyboardButton("\u23f0 Set Time", callback_data=f"task:{task_id}:settime")
    )
    rows.append(action_row)

    # Row 2: info actions
    rows.append([
        InlineKeyboardButton("\u270f Edit", callback_data=f"task:{task_id}:edit"),
        InlineKeyboardButton("\ud83d\udccb Subtasks", callback_data=f"task:{task_id}:subtasks"),
    ])

    # Row 3: back to list
    rows.append([
        InlineKeyboardButton("\u2b05 Back to list", callback_data="tasks:all:1"),
    ])

    return InlineKeyboardMarkup(rows)


# ── Edit sub-menu ────────────────────────────────────────────────────

def task_edit_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Sub-menu for editing task properties."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Priority", callback_data=f"task:{task_id}:eprio"),
            InlineKeyboardButton("Status", callback_data=f"task:{task_id}:estat"),
        ],
        [
            InlineKeyboardButton("Energy", callback_data=f"task:{task_id}:eenrg"),
            InlineKeyboardButton("Effort", callback_data=f"task:{task_id}:eefrt"),
        ],
        [
            InlineKeyboardButton("\u2b05 Back", callback_data=f"task:{task_id}:view"),
        ],
    ])


def priority_picker_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Pick a priority level."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Low", callback_data=f"task:{task_id}:sp:low"),
            InlineKeyboardButton("Medium", callback_data=f"task:{task_id}:sp:med"),
        ],
        [
            InlineKeyboardButton("High", callback_data=f"task:{task_id}:sp:high"),
            InlineKeyboardButton("Urgent", callback_data=f"task:{task_id}:sp:urg"),
        ],
        [InlineKeyboardButton("\u2b05 Back", callback_data=f"task:{task_id}:edit")],
    ])


def status_picker_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Pick a status."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Todo", callback_data=f"task:{task_id}:ss:todo"),
            InlineKeyboardButton("In Progress", callback_data=f"task:{task_id}:ss:wip"),
        ],
        [
            InlineKeyboardButton("Blocked", callback_data=f"task:{task_id}:ss:blk"),
            InlineKeyboardButton("Done", callback_data=f"task:{task_id}:ss:done"),
        ],
        [InlineKeyboardButton("\u2b05 Back", callback_data=f"task:{task_id}:edit")],
    ])


def energy_picker_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Pick an energy level."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Low", callback_data=f"task:{task_id}:se:low"),
            InlineKeyboardButton("Medium", callback_data=f"task:{task_id}:se:med"),
            InlineKeyboardButton("High", callback_data=f"task:{task_id}:se:high"),
        ],
        [InlineKeyboardButton("\u2b05 Back", callback_data=f"task:{task_id}:edit")],
    ])


# ── Time picker ──────────────────────────────────────────────────────

def time_picker_date_keyboard(
    task_id: int, year: int, month: int,
) -> InlineKeyboardMarkup:
    """Month calendar grid with tappable day buttons."""
    rows = []

    # Month/year header with nav arrows
    rows.append([
        InlineKeyboardButton("\u25c0", callback_data=f"settime:{task_id}:nav:{year}:{month - 1}"),
        InlineKeyboardButton(
            f"{cal_mod.month_name[month]} {year}", callback_data="noop"
        ),
        InlineKeyboardButton("\u25b6", callback_data=f"settime:{task_id}:nav:{year}:{month + 1}"),
    ])

    # Day-of-week headers
    rows.append([
        InlineKeyboardButton(d, callback_data="noop")
        for d in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    ])

    # Day grid
    matrix = cal_mod.monthcalendar(year, month)
    for week in matrix:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="noop"))
            else:
                row.append(InlineKeyboardButton(
                    str(day),
                    callback_data=f"settime:{task_id}:day:{year}:{month}:{day}",
                ))
        rows.append(row)

    # Cancel
    rows.append([
        InlineKeyboardButton("Cancel", callback_data=f"task:{task_id}:view"),
    ])

    return InlineKeyboardMarkup(rows)


def time_picker_hour_keyboard(
    task_id: int, date_str: str,
) -> InlineKeyboardMarkup:
    """Common time slots from 08:00–20:00 plus 'All day'."""
    rows = []

    # Time slot buttons — 3 per row
    slots = [
        ("08:00", "08"), ("09:00", "09"), ("10:00", "10"),
        ("11:00", "11"), ("12:00", "12"), ("13:00", "13"),
        ("14:00", "14"), ("15:00", "15"), ("16:00", "16"),
        ("17:00", "17"), ("18:00", "18"), ("19:00", "19"),
        ("20:00", "20"),
    ]
    slot_buttons = [
        InlineKeyboardButton(label, callback_data=f"settime:{task_id}:hour:{date_str}:{val}")
        for label, val in slots
    ]
    for i in range(0, len(slot_buttons), 3):
        rows.append(slot_buttons[i:i + 3])

    # All day option
    rows.append([
        InlineKeyboardButton("\ud83d\udcc5 All day", callback_data=f"settime:{task_id}:hour:{date_str}:allday"),
    ])

    # Cancel
    rows.append([
        InlineKeyboardButton("\u2b05 Back (pick date)", callback_data=f"task:{task_id}:settime"),
    ])

    return InlineKeyboardMarkup(rows)


# ── Focus keyboard ─────────────────────────────────────────────────

def focus_keyboard(task_ids: list[int]) -> InlineKeyboardMarkup:
    """Buttons for focused tasks: view + remove from focus."""
    rows = []
    for tid in task_ids:
        rows.append([
            InlineKeyboardButton(f"#{tid}", callback_data=f"task:{tid}:view"),
            InlineKeyboardButton("Remove", callback_data=f"daily:rm:{tid}"),
        ])
    rows.append([InlineKeyboardButton("Clear all", callback_data="daily:clear")])
    return InlineKeyboardMarkup(rows)


# ── Effort picker ──────────────────────────────────────────────────

def effort_picker_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Pick an effort estimate level."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Light", callback_data=f"task:{task_id}:sef:light"),
            InlineKeyboardButton("Moderate", callback_data=f"task:{task_id}:sef:mod"),
            InlineKeyboardButton("Heavy", callback_data=f"task:{task_id}:sef:heavy"),
        ],
        [InlineKeyboardButton("\u2b05 Back", callback_data=f"task:{task_id}:edit")],
    ])


# ── Shutdown confirm ───────────────────────────────────────────────

def shutdown_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm shutdown dialog."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, pause day", callback_data="shutdown:confirm"),
            InlineKeyboardButton("Cancel", callback_data="shutdown:cancel"),
        ],
    ])


# ── Role picker ─────────────────────────────────────────────────────

def role_picker_keyboard(
    assign_type: str,
    target_id: int,
    contact_id: int,
    roles: list | None = None,
) -> InlineKeyboardMarkup:
    """Inline role picker for assignments.

    assign_type: 'p' for project, 't' for task
    Callback data: role:<type>:<target_id>:<contact_id>:<code>
    """
    if roles is None:
        from roost import task_service
        roles = task_service.list_roles()

    buttons = [
        InlineKeyboardButton(
            f"{r.code} {r.label}",
            callback_data=f"role:{assign_type}:{target_id}:{contact_id}:{r.code}",
        )
        for r in roles
    ]
    # 2 per row
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("Cancel", callback_data="noop")])
    return InlineKeyboardMarkup(rows)
