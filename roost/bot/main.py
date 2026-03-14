"""Telegram bot entry point — polling mode."""

import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from roost.config import TELEGRAM_BOT_TOKEN
from roost.bot.handlers import (
    cmd_start, cmd_help, cmd_tasks, cmd_add, cmd_done, cmd_show,
    cmd_note, cmd_notes, cmd_delnote,
    cmd_gem, cmd_gem_new, cmd_lesson, cmd_outline, cmd_doc,
    cmd_modules, cmd_module, cmd_labguide, cmd_assessment, cmd_concept, cmd_refine,
    cmd_template, cmd_progress,
    cmd_standards, cmd_status_overview, cmd_export,
    cmd_send, cmd_ls, handle_file_upload, handle_photo_upload,
    cmd_gdrive, cmd_git, cmd_commit, cmd_log,
    # Phase 1: Smart Triage
    cmd_today, cmd_urgent, cmd_resume, cmd_wip,
    # Phase 2: Calendar
    cmd_cal, cmd_calexport,
    # Phase 3: Context & Organization
    cmd_focus, cmd_lowenergy, cmd_parking,
    # Phase 9: Neurodivergent-friendly features
    cmd_daily, cmd_lowday, cmd_someday,
    cmd_shutdown, cmd_resumeday, cmd_move,
    # Phase 10: Neurodivergent Phase 2
    cmd_next, cmd_pick,
    cmd_timer, cmd_checkin,
    cmd_done_today, cmd_routine,
    cmd_break_task, cmd_spoons,
    # Phase 4: Sharing
    cmd_share, cmd_team,
    # Phase 8: Project Model V2 + Entities
    cmd_entities, cmd_entity, cmd_addentity,
    cmd_projects, cmd_project, cmd_addproject,
    cmd_contacts, cmd_contact, cmd_addcontact,
    cmd_assign, cmd_unassign,
    cmd_roles, cmd_addrole,
    # Phase 5: Curriculum auto-detect + Notion
    cmd_curricula, cmd_notion,
    # Context bundles
    cmd_briefing, cmd_pulse, cmd_prep,
    # Phase 6: Gmail + Calendar write
    cmd_gmail,
    # Phase 7: Dropbox/Otter + Voice status
    cmd_otter, cmd_voice,
    # Voice notes + Journal
    handle_voice, cmd_journal,
    # Inline keyboards
    cmd_settime, cmd_block, handle_callback,
    # Capture mode
    cmd_capture,
    # Email triage
    cmd_inbox,
    # Presentations & Meeting Notes
    cmd_deck, cmd_mnotes,
    # Tutorial
    cmd_tutorial, handle_tutorial_callback,
    # Agent + Skill Builder
    handle_agent_message, cmd_agent, cmd_skill,
)

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("roost.bot")


async def error_handler(update, context):
    """Global error handler — suppress harmless Telegram API errors, log the rest."""
    import telegram.error

    err = context.error
    # "Message is not modified" — user tapped same button twice, harmless
    if isinstance(err, telegram.error.BadRequest) and "not modified" in str(err).lower():
        return
    # "Query is too old" — user pressed a button from a stale message
    if isinstance(err, telegram.error.BadRequest) and "query is too old" in str(err).lower():
        return
    # Log everything else
    logger.exception("Unhandled error: %s", err, exc_info=context.error)


def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN not set in .env")
        return

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Tasks
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("show", cmd_show))

    # Notes + Journal
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("notes", cmd_notes))
    app.add_handler(CommandHandler("delnote", cmd_delnote))
    app.add_handler(CommandHandler("journal", cmd_journal))

    # Curriculum AI (Gemini)
    app.add_handler(CommandHandler("gem", cmd_gem))           # continue session
    app.add_handler(CommandHandler("new", cmd_gem_new))       # fresh session
    app.add_handler(CommandHandler("lesson", cmd_lesson))     # lesson plan
    app.add_handler(CommandHandler("outline", cmd_outline))   # course outline
    app.add_handler(CommandHandler("doc", cmd_doc))           # general docs

    # Curriculum Knowledge
    app.add_handler(CommandHandler("modules", cmd_modules))     # list all modules
    app.add_handler(CommandHandler("module", cmd_module))       # module overview
    app.add_handler(CommandHandler("labguide", cmd_labguide))   # generate lab guide
    app.add_handler(CommandHandler("assessment", cmd_assessment))  # assessment rubric
    app.add_handler(CommandHandler("refine", cmd_refine))       # iterate on output

    # Templates & Progress
    app.add_handler(CommandHandler("template", cmd_template))   # module task tree
    app.add_handler(CommandHandler("progress", cmd_progress))   # progress bar

    # Standards & Export
    app.add_handler(CommandHandler("standards", cmd_standards))  # frameworks list
    app.add_handler(CommandHandler("status", cmd_status_overview))  # module status
    app.add_handler(CommandHandler("export", cmd_export))        # export module docs

    # Phase 1: Smart Triage
    app.add_handler(CommandHandler("today", cmd_today))          # morning briefing
    app.add_handler(CommandHandler("urgent", cmd_urgent))        # top N urgent
    app.add_handler(CommandHandler("resume", cmd_resume))        # what was I doing?
    app.add_handler(CommandHandler("wip", cmd_wip))              # mark work in progress

    # Phase 2: Calendar + time management
    app.add_handler(CommandHandler("cal", cmd_cal))              # today's calendar
    app.add_handler(CommandHandler("calexport", cmd_calexport))  # export .ics
    app.add_handler(CommandHandler("settime", cmd_settime))      # set task deadline
    app.add_handler(CommandHandler("block", cmd_block))          # block calendar slot

    # Phase 3: Context & Organization
    app.add_handler(CommandHandler("focus", cmd_focus))          # project focus
    app.add_handler(CommandHandler("lowenergy", cmd_lowenergy))  # low energy tasks
    app.add_handler(CommandHandler("parking", cmd_parking))      # pause/resume project

    # Phase 9: Neurodivergent-friendly features
    app.add_handler(CommandHandler("daily", cmd_daily))            # daily focus (3 tasks)
    app.add_handler(CommandHandler("lowday", cmd_lowday))          # low energy day mode
    app.add_handler(CommandHandler("someday", cmd_someday))        # shelve/unshelve tasks
    app.add_handler(CommandHandler("shutdown", cmd_shutdown))      # pause all work
    app.add_handler(CommandHandler("resumeday", cmd_resumeday))    # resume from shutdown
    app.add_handler(CommandHandler("move", cmd_move))                # reorder tasks

    # Phase 10: Neurodivergent Phase 2
    app.add_handler(CommandHandler("next", cmd_next))                # just one thing
    app.add_handler(CommandHandler("pick", cmd_pick))                # smart picker
    app.add_handler(CommandHandler("timer", cmd_timer))              # pomodoro timer
    app.add_handler(CommandHandler("checkin", cmd_checkin))           # transition prompts
    app.add_handler(CommandHandler("routine", cmd_routine))           # morning/evening checklists
    app.add_handler(CommandHandler("spoons", cmd_spoons))             # spoon budget
    app.add_handler(CommandHandler("break", cmd_break_task))            # task decomposition

    # Phase 4: Sharing
    app.add_handler(CommandHandler("share", cmd_share))          # share links
    app.add_handler(CommandHandler("team", cmd_team))            # team management

    # Phase 8: Entities + Project Model V2
    app.add_handler(CommandHandler("entities", cmd_entities))        # list entities
    app.add_handler(CommandHandler("entity", cmd_entity))            # entity detail
    app.add_handler(CommandHandler("addentity", cmd_addentity))      # create entity
    app.add_handler(CommandHandler("projects", cmd_projects))        # project list
    app.add_handler(CommandHandler("project", cmd_project))          # project detail
    app.add_handler(CommandHandler("addproject", cmd_addproject))  # create project/entity
    app.add_handler(CommandHandler("contacts", cmd_contacts))      # list contacts
    app.add_handler(CommandHandler("contact", cmd_contact))        # contact detail
    app.add_handler(CommandHandler("addcontact", cmd_addcontact))  # create contact
    app.add_handler(CommandHandler("assign", cmd_assign))          # assign + role picker
    app.add_handler(CommandHandler("unassign", cmd_unassign))      # remove assignment
    app.add_handler(CommandHandler("roles", cmd_roles))            # list roles
    app.add_handler(CommandHandler("addrole", cmd_addrole))        # add role definition

    # Phase 5: Curriculum auto-detect + Notion
    app.add_handler(CommandHandler("curricula", cmd_curricula))  # list/scan curricula
    app.add_handler(CommandHandler("notion", cmd_notion))        # Notion sync status

    # Phase 6: Gmail + Calendar write
    app.add_handler(CommandHandler("gmail", cmd_gmail))           # Gmail status/actions

    # Phase 7: Dropbox/Otter + Voice status
    app.add_handler(CommandHandler("otter", cmd_otter))           # Dropbox/Otter status
    app.add_handler(CommandHandler("voice", cmd_voice))           # Voice/whisper status

    # Capture mode
    app.add_handler(CommandHandler("capture", cmd_capture))

    # Email triage
    app.add_handler(CommandHandler("inbox", cmd_inbox))

    # Context bundles
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("pulse", cmd_pulse))
    app.add_handler(CommandHandler("prep", cmd_prep))

    # Presentations & Meeting Notes
    app.add_handler(CommandHandler("deck", cmd_deck))
    app.add_handler(CommandHandler("mnotes", cmd_mnotes))

    # Tutorial
    app.add_handler(CommandHandler("tutorial", cmd_tutorial))

    # Agent + Skill Builder
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("skill", cmd_skill))

    # Capture message handler (group -1: runs before all group-0 handlers)
    from roost.bot.capture import handle_capture_message
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_capture_message), group=-1)

    # Email triage message handler (group -1: intercepts replies/AI prompts)
    from roost.bot.handlers.email_triage import handle_triage_message
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_triage_message), group=-1)

    # Agent free-text handler (group 0: catches messages not consumed by capture/triage)
    # Skill revision intercept is handled inside handle_agent_message
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_agent_message), group=0)

    # Inline keyboard callbacks (before MessageHandlers)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Voice notes (must be before generic file handler)
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    # Files & Photos
    app.add_handler(CommandHandler("send", cmd_send))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_upload))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))

    # Drive + Git + Log
    app.add_handler(CommandHandler("gdrive", cmd_gdrive))
    app.add_handler(CommandHandler("git", cmd_git))
    app.add_handler(CommandHandler("commit", cmd_commit))
    app.add_handler(CommandHandler("log", cmd_log))

    # Initialize event notifier for bidirectional sync
    from roost.bot.notifier import init_notifier
    init_notifier(app)

    # Initialize Notion subscriber (if enabled)
    try:
        from roost.config import NOTION_SYNC_ENABLED
        if NOTION_SYNC_ENABLED:
            from roost.notion.subscriber import init_subscriber
            from roost.notion.databases import ensure_databases
            ensure_databases()
            init_subscriber()
            logger.info("Notion subscriber initialized")
    except ImportError:
        pass
    except Exception:
        logger.exception("Failed to initialize Notion subscriber")

    # Initialize Gmail subscriber (if enabled)
    try:
        from roost.config import GMAIL_ENABLED
        if GMAIL_ENABLED:
            from roost.gmail.subscriber import init_subscriber as gmail_init
            gmail_init()
            logger.info("Gmail subscriber initialized")
    except ImportError:
        pass
    except Exception:
        logger.exception("Failed to initialize Gmail subscriber")

    # Seed all curricula on first startup (if enabled)
    from roost.config import CURRICULUM_ENABLED
    if CURRICULUM_ENABLED:
        try:
            from roost.curriculum_scanner import seed_all_curricula
            seed_all_curricula()
        except Exception:
            logger.exception("Failed to seed curricula")

    # Initialize scheduler (morning digest, deadline reminders, urgency recalc)
    from roost.bot.scheduler import init_scheduler
    init_scheduler(app)

    app.add_error_handler(error_handler)

    logger.info("Bot starting (polling) — 67 commands + agent handler + callbacks + voice handler registered...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
