"""Bot command handlers package.

Re-exports all handler names for backward compatibility with main.py.
"""

# Help
from roost.bot.handlers.help import cmd_start, cmd_help

# Tasks & Notes & Audit log
from roost.bot.handlers.tasks import (
    cmd_tasks, cmd_add, cmd_done, cmd_show,
    cmd_note, cmd_notes, cmd_delnote,
    cmd_log,
)

# Smart Triage & Calendar
from roost.bot.handlers.triage import (
    cmd_today, cmd_urgent, cmd_resume, cmd_wip,
    cmd_lowenergy,
    cmd_cal, cmd_calexport,
    cmd_block,
    # Neurodivergent-friendly features (Phase 1)
    cmd_daily, cmd_lowday, cmd_someday,
    cmd_shutdown, cmd_resumeday,
    cmd_move,
    # Neurodivergent-friendly features (Phase 2)
    cmd_next, cmd_pick,
    cmd_timer, cmd_checkin,
    cmd_done_today, cmd_routine,
    cmd_break_task, cmd_spoons,
)

# Focus, Parking, Sharing, Team + Project Model V2
from roost.bot.handlers.projects import (
    cmd_focus, cmd_parking,
    cmd_share, cmd_team,
    cmd_entities, cmd_entity, cmd_addentity,
    cmd_projects, cmd_project, cmd_addproject,
    cmd_contacts, cmd_contact, cmd_addcontact,
    cmd_assign, cmd_unassign,
    cmd_roles, cmd_addrole,
)

# Gemini AI (includes refine, labguide, assessment — they route through _run_gemini)
from roost.bot.handlers.gemini import (
    cmd_gem, cmd_gem_new, cmd_lesson, cmd_outline, cmd_doc,
    cmd_refine, cmd_labguide, cmd_assessment,
)

# Files, Drive, Git
from roost.bot.handlers.files import (
    cmd_send, cmd_ls, handle_file_upload, handle_photo_upload,
    cmd_gdrive, cmd_git, cmd_commit,
)

# Curriculum — single-module operations (removed; stubs for backward compat)
try:
    from roost.bot.handlers.curriculum import (
        cmd_concept,
        cmd_template, cmd_progress,
        cmd_standards, cmd_status_overview, cmd_export,
    )
except ImportError:
    async def _curriculum_removed(update, context):
        await update.message.reply_text("Curriculum commands have been removed.")
    cmd_concept = cmd_template = cmd_progress = _curriculum_removed
    cmd_standards = cmd_status_overview = cmd_export = _curriculum_removed

# Curriculum — multi-programme browsing (removed; stubs for backward compat)
try:
    from roost.bot.handlers.curriculum_multi import (
        cmd_modules, cmd_module, cmd_curricula,
    )
except ImportError:
    cmd_modules = cmd_module = cmd_curricula = _curriculum_removed

# Integrations (Gmail, Notion, Otter, Voice command handlers)
from roost.bot.handlers.integrations import (
    cmd_gmail, cmd_notion, cmd_otter, cmd_voice,
)

# Tasks — inline time picker
from roost.bot.handlers.tasks import cmd_settime

# Callback query router
from roost.bot.handlers.callbacks import handle_callback

# Capture mode
from roost.bot.capture import cmd_capture

# Email triage
from roost.bot.handlers.email_triage import cmd_inbox

# Context bundles
from roost.bot.handlers.bundles import cmd_briefing, cmd_pulse, cmd_prep

# Voice note MessageHandler + Journal command
from roost.bot.handlers.voice_handler import handle_voice, cmd_journal

# Presentations & Meeting Notes
from roost.bot.handlers.presentations import cmd_deck, cmd_mnotes

# Tutorial
from roost.bot.handlers.tutorial import cmd_tutorial, handle_tutorial_callback

# Agent (CLI passthrough) + Skill Builder
from roost.bot.handlers.agent import handle_agent_message, cmd_agent
from roost.bot.handlers.skill_builder import cmd_skill

__all__ = [
    # Help
    "cmd_start", "cmd_help",
    # Tasks & Notes
    "cmd_tasks", "cmd_add", "cmd_done", "cmd_show",
    "cmd_note", "cmd_notes", "cmd_delnote", "cmd_log",
    # Triage & Calendar
    "cmd_today", "cmd_urgent", "cmd_resume", "cmd_wip", "cmd_lowenergy",
    "cmd_cal", "cmd_calexport", "cmd_block",
    # Neurodivergent-friendly features (Phase 1)
    "cmd_daily", "cmd_lowday", "cmd_someday", "cmd_shutdown", "cmd_resumeday", "cmd_move",
    # Neurodivergent-friendly features (Phase 2)
    "cmd_next", "cmd_pick",
    "cmd_timer", "cmd_checkin",
    "cmd_done_today", "cmd_routine",
    "cmd_break_task", "cmd_spoons",
    # Time picker + Callbacks
    "cmd_settime", "handle_callback",
    # Projects & Sharing
    "cmd_focus", "cmd_parking", "cmd_share", "cmd_team",
    # Entities + Project Model V2
    "cmd_entities", "cmd_entity", "cmd_addentity",
    "cmd_projects", "cmd_project", "cmd_addproject",
    "cmd_contacts", "cmd_contact", "cmd_addcontact",
    "cmd_assign", "cmd_unassign",
    "cmd_roles", "cmd_addrole",
    # Gemini
    "cmd_gem", "cmd_gem_new", "cmd_lesson", "cmd_outline", "cmd_doc",
    "cmd_refine", "cmd_labguide", "cmd_assessment",
    # Files
    "cmd_send", "cmd_ls", "handle_file_upload", "handle_photo_upload",
    "cmd_gdrive", "cmd_git", "cmd_commit",
    # Curriculum
    "cmd_modules", "cmd_module", "cmd_concept",
    "cmd_template", "cmd_progress",
    "cmd_standards", "cmd_status_overview", "cmd_export", "cmd_curricula",
    # Integrations
    "cmd_gmail", "cmd_notion", "cmd_otter", "cmd_voice", "handle_voice", "cmd_journal",
    # Presentations & Meeting Notes
    "cmd_deck", "cmd_mnotes",
    # Context bundles
    "cmd_briefing", "cmd_pulse", "cmd_prep",
    # Capture
    "cmd_capture",
    # Email triage
    "cmd_inbox",
    # Tutorial
    "cmd_tutorial", "handle_tutorial_callback",
    # Agent + Skill Builder
    "handle_agent_message", "cmd_agent", "cmd_skill",
]
