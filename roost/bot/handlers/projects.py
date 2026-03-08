"""Focus, parking, sharing, team, and project model V2 command handlers."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.models import (
    ProjectCreate, ProjectUpdate,
    ContactCreate, ContactUpdate,
    ProjectAssignmentCreate, TaskAssignmentCreate,
    EntityCreate, ContactEntityCreate,
)
from roost import task_service
from roost.bot.handlers.common import _format_task_obj

logger = logging.getLogger(__name__)


# ── Focus & Parking ──────────────────────────────────────────────────

@authorized
async def cmd_focus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Focus on a single project's tasks, or list active projects."""
    if not context.args:
        # List active projects
        projects = task_service.list_projects(status="active")
        if not projects:
            await update.message.reply_text("No active projects. Create one first.")
            return

        lines = ["*Active Projects:*\n"]
        for p in projects:
            pin = "📌 " if p.pinned else ""
            cat = f" [{p.category}]" if p.category else ""
            lines.append(f"{pin}*{p.name}*{cat} — {p.task_count} tasks")

        lines.append("\nUse /focus ProjectName to see tasks.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    project_name = " ".join(context.args)
    project = task_service.get_project_by_name(project_name)
    if not project:
        await update.message.reply_text(f"Project '{project_name}' not found.")
        return

    tasks = task_service.list_tasks(project=project_name, order_by="urgency")
    active = [t for t in tasks if t.status.value != "done"]

    lines = [f"*{project.name}*"]
    if project.category:
        lines[0] += f" [{project.category}]"
    lines.append(f"Status: {project.status} | {len(active)} active tasks\n")

    if not active:
        lines.append("All tasks done!")
    else:
        for t in active[:20]:
            lines.append(_format_task_obj(t, show_context=True))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_parking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause/resume a project to reduce mental load."""
    if not context.args:
        # Show paused projects
        paused = task_service.list_projects(status="paused")
        active = task_service.list_projects(status="active")

        lines = ["*Project Parking Lot*\n"]
        if paused:
            lines.append(f"*Paused ({len(paused)}):*")
            for p in paused:
                lines.append(f"  ⏸ {p.name} ({p.task_count} tasks)")

        lines.append(f"\n*Active ({len(active)}):*")
        for p in active[:10]:
            lines.append(f"  ▶ {p.name} ({p.task_count} tasks)")

        lines.append("\nUsage: /parking ProjectName to toggle pause/resume")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    project_name = " ".join(context.args)
    project = task_service.get_project_by_name(project_name)
    if not project:
        await update.message.reply_text(f"Project '{project_name}' not found.")
        return

    # Toggle pause/active
    new_status = "active" if project.status == "paused" else "paused"
    task_service.update_project(project.id, ProjectUpdate(status=new_status))

    if new_status == "paused":
        await update.message.reply_text(
            f"⏸ Parked *{project.name}* — its tasks won't appear in /today or /urgent.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"▶ Resumed *{project.name}* — tasks are back in rotation.",
            parse_mode="Markdown",
        )


# ── Sharing ──────────────────────────────────────────────────────────

@authorized
async def cmd_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create/list share links."""
    from roost.sharing_service import create_share_link, list_share_links, revoke_share_link
    from roost.models import ShareLinkCreate

    if context.args and context.args[0].lower() == "revoke":
        if len(context.args) > 1 and context.args[1].isdigit():
            if revoke_share_link(int(context.args[1])):
                await update.message.reply_text("Share link revoked.")
            else:
                await update.message.reply_text("Link not found.")
        else:
            await update.message.reply_text("Usage: /share revoke ID")
        return

    if context.args and context.args[0].lower() != "list":
        # Create a share link for a project
        project_name = " ".join(context.args)
        project = task_service.get_project_by_name(project_name)
        if not project:
            await update.message.reply_text(f"Project '{project_name}' not found.")
            return

        link = create_share_link(ShareLinkCreate(
            label=f"Share: {project.name}",
            scope="project",
            scope_id=project.id,
        ))
        await update.message.reply_text(
            f"Share link for *{project.name}*:\n"
            f"`/shared/{link.token}`\n\n"
            f"Anyone with this link can view (read-only).\n"
            f"Revoke with: /share revoke {link.id}",
            parse_mode="Markdown",
        )
        return

    # List existing share links
    links = list_share_links()
    if not links:
        await update.message.reply_text(
            "No share links. Create one:\n/share ProjectName"
        )
        return

    lines = ["*Active Share Links:*\n"]
    for link in links[:10]:
        lines.append(f"#{link.id} {link.label or 'Dashboard'}")
        lines.append(f"  Token: `{link.token[:12]}...`")
        if link.expires_at:
            lines.append(f"  Expires: {link.expires_at}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Team ─────────────────────────────────────────────────────────────

@authorized
async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage team members."""
    from roost.sharing_service import (
        create_user, list_users, delete_user,
    )
    from roost.models import UserCreate

    if not context.args:
        users = list_users()
        if not users:
            await update.message.reply_text(
                "No team members.\n"
                "Add: /team add Name [telegram_id]\n"
                "Remove: /team remove ID"
            )
            return

        lines = ["*Team Members:*\n"]
        for u in users:
            tg = f" (TG: {u.telegram_id})" if u.telegram_id else ""
            lines.append(f"#{u.id} {u.name} — {u.role}{tg}")

        lines.append("\n/team add Name [telegram_id]")
        lines.append("/team remove ID")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    subcmd = context.args[0].lower()

    if subcmd == "add":
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /team add Name [telegram_id]")
            return

        name = context.args[1]
        telegram_id = None
        if len(context.args) > 2 and context.args[2].isdigit():
            telegram_id = int(context.args[2])

        user = create_user(UserCreate(name=name, telegram_id=telegram_id))
        if user:
            await update.message.reply_text(f"Added team member #{user.id}: {user.name}")
        else:
            await update.message.reply_text("Failed to create user.")

    elif subcmd == "remove":
        if len(context.args) < 2 or not context.args[1].isdigit():
            await update.message.reply_text("Usage: /team remove ID")
            return

        if delete_user(int(context.args[1])):
            await update.message.reply_text(f"Removed user #{context.args[1]}")
        else:
            await update.message.reply_text("User not found.")

    else:
        await update.message.reply_text("Usage: /team [add|remove]")


# ── Entities ────────────────────────────────────────────────────────

@authorized
async def cmd_entities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all entities (companies/organisations)."""
    entities = task_service.list_entities()
    if not entities:
        await update.message.reply_text(
            "No entities yet. Create one: /addentity CompanyName"
        )
        return

    lines = ["*Entities*\n"]
    for e in entities:
        parts = []
        if e.project_count:
            parts.append(f"{e.project_count} projects")
        if e.contact_count:
            parts.append(f"{e.contact_count} people")
        info = f" ({', '.join(parts)})" if parts else ""
        lines.append(f"\U0001f3e2 *{e.name}*{info}")
        if e.description:
            lines.append(f"  _{e.description}_")

    lines.append(f"\n_Total: {len(entities)} entities_")
    lines.append("/entity NAME — detail view")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_entity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show entity detail: projects + people."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /entity NAME or /entity ID\n"
            "See all: /entities"
        )
        return

    text = " ".join(context.args)
    entity = None
    if text.isdigit():
        entity = task_service.get_entity(int(text))
    if not entity:
        entity = task_service.get_entity_by_name(text)
    if not entity:
        await update.message.reply_text(f"Entity '{text}' not found.")
        return

    tree = task_service.get_entity_tree(entity.id)
    projects = tree["projects"]
    people = tree["people"]

    lines = [f"\U0001f3e2 *{entity.name}*"]
    lines.append(f"Status: {entity.status}")
    if entity.description:
        lines.append(f"_{entity.description}_")
    if entity.notes:
        lines.append(f"Notes: {entity.notes}")
    lines.append("")

    # Projects under this entity
    type_icons = {
        "programme": "\U0001f4c1", "initiative": "\U0001f680", "project": "\U0001f4cb",
    }
    if projects:
        lines.append(f"*Projects ({len(projects)}):*")
        for p in projects:
            icon = type_icons.get(p.project_type, "\U0001f4cb")
            info = f" — {p.task_count} tasks" if p.task_count else ""
            lines.append(f"  {icon} {p.name}{info}")
        lines.append("")

    # People affiliated
    if people:
        lines.append(f"*People ({len(people)}):*")
        for ce in people:
            title = f" — {ce.title}" if ce.title else ""
            primary = " (primary)" if ce.is_primary else ""
            lines.append(f"  \U0001f464 {ce.contact_name}{title}{primary}")
        lines.append("")

    if not projects and not people:
        lines.append("_No projects or people yet._")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_addentity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a new entity (company/organisation).

    /addentity CompanyName
    /addentity CompanyName -- description text
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /addentity Name [-- description]"
        )
        return

    raw = " ".join(context.args)
    description = ""
    if " -- " in raw:
        name, description = raw.split(" -- ", 1)
    else:
        name = raw

    if not name.strip():
        await update.message.reply_text("Please provide an entity name.")
        return

    entity = task_service.create_entity(EntityCreate(
        name=name.strip(), description=description.strip(),
    ))

    await update.message.reply_text(
        f"\U0001f3e2 Created entity *{entity.name}* (#{entity.id})",
        parse_mode="Markdown",
    )


# ── Project Model V2: Tree View ─────────────────────────────────────

def _build_tree_lines(parent_id: int | None, indent: int = 0) -> list[str]:
    """Recursively build tree lines for project hierarchy."""
    children = task_service.list_child_projects(parent_id) if parent_id else task_service.list_projects(top_level_only=True)
    lines = []
    type_icons = {
        "programme": "\U0001f4c1",    # 📁
        "initiative": "\U0001f680",   # 🚀
        "project": "\U0001f4cb",      # 📋
    }
    for p in children:
        icon = type_icons.get(p.project_type, "\U0001f4cb")
        prefix = "  " * indent + ("\u251c\u2500 " if indent > 0 else "")
        info_parts = []
        if p.task_count:
            info_parts.append(f"{p.task_count} tasks")
        if p.children_count:
            info_parts.append(f"{p.children_count} sub")
        info = f" ({', '.join(info_parts)})" if info_parts else ""
        lines.append(f"{prefix}{icon} *{p.name}*{info}")

        # Recurse (max 3 levels to avoid message overflow)
        if p.children_count > 0 and indent < 3:
            lines.extend(_build_tree_lines(p.id, indent + 1))
    return lines


@authorized
async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List projects, grouped by entity. Filters: /projects programmes|initiatives."""
    type_filter = context.args[0].lower() if context.args else None

    if type_filter in ("programme", "programmes"):
        projects = task_service.list_projects(project_type="programme")
        title = "Programmes"
    elif type_filter in ("initiative", "initiatives"):
        projects = task_service.list_projects(project_type="initiative")
        title = "Initiatives"
    elif type_filter == "tree":
        # Pure hierarchy view
        lines = ["*Project Tree*\n"]
        tree = _build_tree_lines(None)
        if not tree:
            lines.append("No projects yet. Create one with /addproject")
        else:
            lines.extend(tree)
        lines.append(f"\n_Total: {len(task_service.list_projects())} projects_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    else:
        # Default: group by entity
        entities = task_service.list_entities()
        all_projects = task_service.list_projects()

        lines = ["*Projects*\n"]

        for entity in entities:
            ep = [p for p in all_projects if p.entity_id == entity.id]
            if ep:
                lines.append(f"\U0001f3e2 *{entity.name}*")
                for p in ep:
                    icon = {"programme": "\U0001f4c1", "initiative": "\U0001f680"}.get(p.project_type, "\U0001f4cb")
                    info = f" — {p.task_count} tasks" if p.task_count else ""
                    lines.append(f"  {icon} {p.name}{info}")
                lines.append("")

        # Unaffiliated projects
        orphans = [p for p in all_projects if p.entity_id is None]
        if orphans:
            lines.append("*Unaffiliated*")
            for p in orphans:
                icon = {"programme": "\U0001f4c1", "initiative": "\U0001f680"}.get(p.project_type, "\U0001f4cb")
                info = f" — {p.task_count} tasks" if p.task_count else ""
                lines.append(f"  {icon} {p.name}{info}")
            lines.append("")

        if not all_projects:
            lines.append("No projects yet. Create one with /addproject")

        lines.append(f"_Total: {len(all_projects)} projects_")
        lines.append("/projects tree — hierarchy view")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if not projects:
        await update.message.reply_text(f"No {title.lower()} found.")
        return

    lines = [f"*{title}*\n"]
    for p in projects:
        entity_info = f" ({p.entity_name})" if p.entity_name else ""
        lines.append(f"#{p.id} *{p.name}*{entity_info}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show project detail + assignments + children."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /project NAME or /project ID\n"
            "See all: /projects"
        )
        return

    text = " ".join(context.args)
    project = None
    if text.isdigit():
        project = task_service.get_project(int(text))
    if not project:
        project = task_service.get_project_by_name(text)
    if not project:
        await update.message.reply_text(f"Project '{text}' not found.")
        return

    type_icons = {
        "programme": "\U0001f4c1",
        "initiative": "\U0001f680", "project": "\U0001f4cb",
    }
    icon = type_icons.get(project.project_type, "\U0001f4cb")

    lines = [f"{icon} *{project.name}*"]
    lines.append(f"Type: {project.project_type} | Status: {project.status}")
    if project.description:
        lines.append(f"_{project.description}_")
    if project.entity_name:
        lines.append(f"Entity: \U0001f3e2 {project.entity_name}")
    if project.parent_project_name:
        lines.append(f"Parent: {project.parent_project_name}")
    if project.category:
        lines.append(f"Category: {project.category}")
    lines.append("")

    # Children
    children = task_service.list_child_projects(project.id)
    if children:
        lines.append(f"*Children ({len(children)}):*")
        for c in children:
            ci = type_icons.get(c.project_type, "\U0001f4cb")
            lines.append(f"  {ci} {c.name}")
        lines.append("")

    # Assignments
    assignments = task_service.list_project_assignments(project_id=project.id)
    if assignments:
        lines.append(f"*People ({len(assignments)}):*")
        for a in assignments:
            entity = f" ({a.entity_name})" if a.entity_name else ""
            lines.append(f"  {a.role} {a.role_label or ''} — {a.contact_name}{entity}")
        lines.append("")

    # Tasks summary
    tasks = task_service.list_tasks(project=project.name)
    active = [t for t in tasks if t.status.value != "done"]
    done = len(tasks) - len(active)
    if tasks:
        lines.append(f"*Tasks:* {len(active)} active, {done} done")

    lines.append(f"\n/assign {project.name} PERSON — add assignment")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_addproject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a project/programme/initiative.

    /addproject NAME
    /addproject NAME --type programme
    /addproject NAME --entity EntityName
    /addproject NAME --parent ParentName
    /addproject NAME --type initiative --entity EntityName
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /addproject NAME [--type programme|initiative|project] "
            "[--entity NAME] [--parent NAME]"
        )
        return

    args = list(context.args)
    project_type = "project"
    parent_name = None
    entity_name = None

    i = 0
    name_parts = []
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            project_type = args[i + 1].lower()
            i += 2
        elif args[i] == "--parent" and i + 1 < len(args):
            i += 1
            parent_parts = []
            while i < len(args) and not args[i].startswith("--"):
                parent_parts.append(args[i])
                i += 1
            parent_name = " ".join(parent_parts)
        elif args[i] == "--entity" and i + 1 < len(args):
            i += 1
            entity_parts = []
            while i < len(args) and not args[i].startswith("--"):
                entity_parts.append(args[i])
                i += 1
            entity_name = " ".join(entity_parts)
        else:
            name_parts.append(args[i])
            i += 1

    name = " ".join(name_parts)
    if not name:
        await update.message.reply_text("Please provide a project name.")
        return

    parent_id = None
    if parent_name:
        parent = task_service.get_project_by_name(parent_name)
        if not parent:
            await update.message.reply_text(f"Parent project '{parent_name}' not found.")
            return
        parent_id = parent.id

    eid = None
    if entity_name:
        entity = task_service.get_entity_by_name(entity_name)
        if not entity:
            await update.message.reply_text(f"Entity '{entity_name}' not found.")
            return
        eid = entity.id

    project = task_service.create_project(ProjectCreate(
        name=name,
        project_type=project_type,
        parent_project_id=parent_id,
        entity_id=eid,
    ))

    type_icons = {
        "programme": "\U0001f4c1",
        "initiative": "\U0001f680", "project": "\U0001f4cb",
    }
    icon = type_icons.get(project_type, "\U0001f4cb")
    extra = []
    if parent_name:
        extra.append(f"under {parent_name}")
    if entity_name:
        extra.append(f"entity: {entity_name}")
    suffix = f" ({', '.join(extra)})" if extra else ""
    await update.message.reply_text(
        f"{icon} Created *{project.name}* (#{project.id}, {project_type}){suffix}",
        parse_mode="Markdown",
    )


# ── Contacts ─────────────────────────────────────────────────────────

@authorized
async def cmd_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List contacts, optionally filtered by entity."""
    entity_filter = " ".join(context.args) if context.args else None

    entity_id = None
    if entity_filter:
        entity = task_service.get_entity_by_name(entity_filter)
        if entity:
            entity_id = entity.id

    contacts = task_service.list_contacts(entity_id=entity_id)
    if not contacts:
        msg = "No contacts yet. Add one: /addcontact Name --entity EntityName"
        if entity_filter:
            msg = f"No contacts for '{entity_filter}'."
        await update.message.reply_text(msg)
        return

    # Group by primary entity
    by_entity: dict[str, list] = {}
    for c in contacts:
        key = c.entity_name or "Independent"
        by_entity.setdefault(key, []).append(c)

    lines = ["*Contacts*\n"]
    for ename, econtacts in sorted(by_entity.items()):
        lines.append(f"\U0001f3e2 *{ename}*")
        for c in econtacts:
            extra = f" — {c.email}" if c.email else ""
            lines.append(f"  #{c.id} {c.name}{extra}")
        lines.append("")

    lines.append(f"_Total: {len(contacts)} contacts_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show contact detail + entity affiliations + assignments."""
    if not context.args:
        await update.message.reply_text("Usage: /contact NAME or /contact ID")
        return

    text = " ".join(context.args)
    contact = None
    if text.isdigit():
        contact = task_service.get_contact(int(text))
    if not contact:
        contact = task_service.get_contact_by_name(text)
    if not contact:
        await update.message.reply_text(f"Contact '{text}' not found.")
        return

    lines = [f"\U0001f464 *{contact.name}*"]
    if contact.email:
        lines.append(f"Email: {contact.email}")
    if contact.phone:
        lines.append(f"Phone: {contact.phone}")
    if contact.notes:
        lines.append(f"Notes: _{contact.notes}_")
    lines.append("")

    # Entity affiliations
    affiliations = task_service.list_contact_entities(contact_id=contact.id)
    if affiliations:
        lines.append(f"*Entities ({len(affiliations)}):*")
        for ce in affiliations:
            title = f" — {ce.title}" if ce.title else ""
            primary = " (primary)" if ce.is_primary else ""
            lines.append(f"  \U0001f3e2 {ce.entity_name}{title}{primary}")
        lines.append("")
    else:
        lines.append("_No entity affiliations._\n")

    # All assignments
    all_asgn = task_service.list_assignments_by_contact(contact.id)

    proj_asgn = all_asgn["project_assignments"]
    if proj_asgn:
        lines.append(f"*Project Assignments ({len(proj_asgn)}):*")
        for a in proj_asgn:
            lines.append(f"  {a.role} ({a.role_label or '?'}) — {a.project_name}")
        lines.append("")

    task_asgn = all_asgn["task_assignments"]
    if task_asgn:
        lines.append(f"*Task Assignments ({len(task_asgn)}):*")
        for a in task_asgn:
            lines.append(f"  {a.role} ({a.role_label or '?'}) — {a.task_title}")
        lines.append("")

    if not proj_asgn and not task_asgn:
        lines.append("_No project/task assignments yet._")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_addcontact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a new contact, optionally linked to an entity.

    /addcontact Name
    /addcontact Name --entity EntityName
    /addcontact Name --email email@example.com --entity EntityName
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /addcontact Name [--entity EntityName] [--email email]"
        )
        return

    args = list(context.args)
    entity_name = None
    email = ""
    name_parts = []

    i = 0
    while i < len(args):
        if args[i] == "--entity" and i + 1 < len(args):
            i += 1
            entity_parts = []
            while i < len(args) and not args[i].startswith("--"):
                entity_parts.append(args[i])
                i += 1
            entity_name = " ".join(entity_parts)
        elif args[i] == "--email" and i + 1 < len(args):
            email = args[i + 1]
            i += 2
        else:
            name_parts.append(args[i])
            i += 1

    name = " ".join(name_parts)
    if not name:
        await update.message.reply_text("Please provide a contact name.")
        return

    # Create the contact (no entity_id on contact itself)
    contact = task_service.create_contact(ContactCreate(
        name=name, email=email,
    ))

    # Link to entity via contact_entities if specified
    entity_info = ""
    if entity_name:
        entity = task_service.get_entity_by_name(entity_name)
        if entity:
            task_service.add_contact_entity(ContactEntityCreate(
                contact_id=contact.id, entity_id=entity.id, is_primary=1,
            ))
            entity_info = f" (\U0001f3e2 {entity.name})"
        else:
            entity_info = f" (entity '{entity_name}' not found)"

    await update.message.reply_text(
        f"\U0001f464 Created contact *{contact.name}*{entity_info} (#{contact.id})",
        parse_mode="Markdown",
    )


# ── Assignments ──────────────────────────────────────────────────────

@authorized
async def cmd_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Assign a contact to a project — shows role picker.

    /assign ProjectName PersonName
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /assign PROJECT PERSON\n"
            "Example: /assign \"CyFi Safe Game\" Myk"
        )
        return

    # Parse: last word is person name, everything before is project name
    # Or use quotes: /assign "Project Name" "Person Name"
    raw = " ".join(context.args)

    # Simple split: try last word as person, rest as project
    # If that fails, try first word as project, rest as person
    parts = context.args
    person_name = parts[-1]
    project_name = " ".join(parts[:-1])

    project = task_service.get_project_by_name(project_name)
    if not project:
        # Try: first word is project, rest is person
        project_name = parts[0]
        person_name = " ".join(parts[1:])
        project = task_service.get_project_by_name(project_name)

    if not project:
        await update.message.reply_text(
            f"Project not found. Try exact name.\n"
            f"Available: /projects"
        )
        return

    contact = task_service.get_contact_by_name(person_name)
    if not contact:
        await update.message.reply_text(
            f"Contact '{person_name}' not found.\n"
            f"Add them first: /addcontact {person_name}"
        )
        return

    # Show role picker
    from roost.bot.keyboards import role_picker_keyboard
    kb = role_picker_keyboard("p", project.id, contact.id)
    await update.message.reply_text(
        f"Assign *{contact.name}* to *{project.name}*\nPick a role:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


@authorized
async def cmd_unassign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a project assignment by ID.

    /unassign ASSIGNMENT_ID
    """
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /unassign ASSIGNMENT_ID\n"
            "See assignments: /project ProjectName"
        )
        return

    assignment_id = int(context.args[0])
    asgn = task_service.get_project_assignment(assignment_id)
    if not asgn:
        await update.message.reply_text("Assignment not found.")
        return

    task_service.delete_project_assignment(assignment_id)
    await update.message.reply_text(
        f"Removed: {asgn.contact_name} ({asgn.role}) from {asgn.project_name}",
    )


# ── Role picker callback ────────────────────────────────────────────

async def handle_role_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle role:type:target_id:contact_id:code callbacks."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    # role:p:42:7:R or role:t:42:7:R
    if len(parts) != 5:
        await query.edit_message_text("Invalid role callback.")
        return

    _, assign_type, target_id_s, contact_id_s, role_code = parts
    target_id = int(target_id_s)
    contact_id = int(contact_id_s)

    try:
        if assign_type == "p":
            asgn = task_service.create_project_assignment(ProjectAssignmentCreate(
                contact_id=contact_id, project_id=target_id, role=role_code,
            ))
            contact = task_service.get_contact(contact_id)
            project = task_service.get_project(target_id)
            await query.edit_message_text(
                f"\u2705 Assigned *{contact.name}* to *{project.name}* as "
                f"*{asgn.role}* ({asgn.role_label or role_code})",
                parse_mode="Markdown",
            )
        elif assign_type == "t":
            asgn = task_service.create_task_assignment(TaskAssignmentCreate(
                contact_id=contact_id, task_id=target_id, role=role_code,
            ))
            contact = task_service.get_contact(contact_id)
            task = task_service.get_task(target_id)
            await query.edit_message_text(
                f"\u2705 Assigned *{contact.name}* to task *{task.title}* as "
                f"*{asgn.role}* ({asgn.role_label or role_code})",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text("Unknown assignment type.")
    except Exception as e:
        logger.exception("Assignment failed")
        msg = str(e)
        if "UNIQUE" in msg:
            await query.edit_message_text("This assignment already exists.")
        else:
            await query.edit_message_text(f"Failed: {msg}")


# ── Roles ────────────────────────────────────────────────────────────

@authorized
async def cmd_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all role definitions."""
    roles = task_service.list_roles(active_only=False)
    if not roles:
        await update.message.reply_text("No roles defined.")
        return

    lines = ["*Role Definitions*\n"]
    for r in roles:
        active = "" if r.is_active else " (inactive)"
        lines.append(f"*{r.code}* — {r.label}{active}")
        if r.description:
            lines.append(f"  _{r.description}_")
    lines.append("\nAdd: /addrole CODE Label Description")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_addrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new role definition.

    /addrole D Driver Makes key decisions
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /addrole CODE Label [Description]\n"
            "Example: /addrole D Driver Makes key decisions"
        )
        return

    code = context.args[0].upper()
    label = context.args[1]
    description = " ".join(context.args[2:]) if len(context.args) > 2 else ""

    # Check if exists
    existing = task_service.get_role(code)
    if existing:
        await update.message.reply_text(
            f"Role '{code}' already exists: {existing.label}. "
            f"It will be updated."
        )
        role = task_service.update_role(code, label=label, description=description)
    else:
        role = task_service.create_role(code, label, description)

    await update.message.reply_text(
        f"\u2705 Role *{role.code}* — {role.label}\n_{role.description}_",
        parse_mode="Markdown",
    )
