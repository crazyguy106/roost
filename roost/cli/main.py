"""CLI tool: task add, task list, task done, task show, task rm."""

import argparse
import sys
from roost.models import TaskCreate, TaskUpdate, TaskStatus, Priority, ProjectCreate, NoteCreate
from roost import task_service


PRIORITY_COLORS = {
    "urgent": "\033[91m",  # red
    "high": "\033[93m",    # yellow
    "medium": "\033[0m",   # default
    "low": "\033[90m",     # gray
}
STATUS_ICONS = {
    "todo": "[ ]",
    "in_progress": "[~]",
    "done": "[x]",
    "blocked": "[!]",
}
RESET = "\033[0m"


def cmd_add(args):
    project_id = None
    if args.project:
        proj = task_service.get_project_by_name(args.project)
        if not proj:
            proj = task_service.create_project(ProjectCreate(name=args.project))
            print(f"Created project: {args.project}")
        project_id = proj.id

    deadline = args.deadline
    if deadline and " " not in deadline:
        deadline += " 00:00:00"

    task = task_service.create_task(TaskCreate(
        title=args.title,
        priority=args.priority or "medium",
        deadline=deadline,
        project_id=project_id,
    ))
    print(f"Created task #{task.id}: {task.title}")


def cmd_list(args):
    tasks = task_service.list_tasks(
        status=args.status,
        project=args.project,
        priority=args.priority,
    )
    if not tasks:
        print("No tasks found.")
        return

    for t in tasks:
        icon = STATUS_ICONS.get(t.status.value, "[ ]")
        color = PRIORITY_COLORS.get(t.priority.value, "")
        proj = f" [{t.project_name}]" if t.project_name else ""
        deadline = f" due:{t.deadline}" if t.deadline else ""
        prio = f" !{t.priority.value}" if t.priority.value != "medium" else ""
        print(f"{color}{icon} #{t.id} {t.title}{proj}{prio}{deadline}{RESET}")


def cmd_done(args):
    task = task_service.complete_task(args.id)
    if task:
        print(f"Completed task #{task.id}: {task.title}")
    else:
        print(f"Task #{args.id} not found.")
        sys.exit(1)


def cmd_show(args):
    task = task_service.get_task(args.id)
    if not task:
        print(f"Task #{args.id} not found.")
        sys.exit(1)

    proj = task.project_name or "none"
    print(f"Task #{task.id}")
    print(f"  Title:    {task.title}")
    print(f"  Status:   {task.status.value}")
    print(f"  Priority: {task.priority.value}")
    print(f"  Project:  {proj}")
    if task.deadline:
        print(f"  Deadline: {task.deadline}")
    if task.description:
        print(f"  Description: {task.description}")
    print(f"  Created:  {task.created_at}")
    print(f"  Updated:  {task.updated_at}")


def cmd_rm(args):
    if task_service.delete_task(args.id):
        print(f"Deleted task #{args.id}")
    else:
        print(f"Task #{args.id} not found.")
        sys.exit(1)


def cmd_edit(args):
    updates = {}
    if args.title:
        updates["title"] = args.title
    if args.status:
        updates["status"] = args.status
    if args.priority:
        updates["priority"] = args.priority
    if args.deadline:
        deadline = args.deadline
        if " " not in deadline:
            deadline += " 00:00:00"
        updates["deadline"] = deadline
    if args.description:
        updates["description"] = args.description

    if not updates:
        print("Nothing to update. Use flags like --title, --status, --priority.")
        sys.exit(1)

    task = task_service.update_task(args.id, TaskUpdate(**updates))
    if task:
        print(f"Updated task #{task.id}: {task.title}")
    else:
        print(f"Task #{args.id} not found.")
        sys.exit(1)


def cmd_note(args):
    tag = args.tag or ""
    content = " ".join(args.content)
    note = task_service.create_note(NoteCreate(content=content, tag=tag))
    tag_str = f" [{tag}]" if tag else ""
    print(f"Noted #{note.id}{tag_str}: {content[:80]}")


def cmd_notes(args):
    notes = task_service.list_notes(tag=args.tag, limit=20)
    if not notes:
        print("No notes found.")
        return
    for n in notes:
        tag_str = f" [{n.tag}]" if n.tag else ""
        date = n.created_at.split(" ")[0]
        print(f"  #{n.id}{tag_str} {n.content[:80]}  ({date})")


def cmd_delnote(args):
    if task_service.delete_note(args.id):
        print(f"Deleted note #{args.id}")
    else:
        print(f"Note #{args.id} not found.")
        sys.exit(1)


def cmd_projects(args):
    projects = task_service.list_projects()
    if not projects:
        print("No projects.")
        return
    for p in projects:
        print(f"  #{p.id} {p.name} ({p.task_count} tasks)")


def main():
    parser = argparse.ArgumentParser(prog="task", description="VPS Task Manager")
    sub = parser.add_subparsers(dest="command")

    # add
    p_add = sub.add_parser("add", help="Add a new task")
    p_add.add_argument("title", help="Task title")
    p_add.add_argument("-p", "--priority", choices=["low", "medium", "high", "urgent"])
    p_add.add_argument("-D", "--deadline", help="Deadline (YYYY-MM-DD HH:MM:SS)")
    p_add.add_argument("--project", help="Project name (auto-created if new)")

    # list
    p_list = sub.add_parser("list", aliases=["ls"], help="List tasks")
    p_list.add_argument("--status", choices=["todo", "in_progress", "done", "blocked"])
    p_list.add_argument("--project", help="Filter by project name")
    p_list.add_argument("--priority", choices=["low", "medium", "high", "urgent"])

    # done
    p_done = sub.add_parser("done", help="Mark task as done")
    p_done.add_argument("id", type=int, help="Task ID")

    # show
    p_show = sub.add_parser("show", help="Show task details")
    p_show.add_argument("id", type=int, help="Task ID")

    # rm
    p_rm = sub.add_parser("rm", help="Delete a task")
    p_rm.add_argument("id", type=int, help="Task ID")

    # edit
    p_edit = sub.add_parser("edit", help="Edit a task")
    p_edit.add_argument("id", type=int, help="Task ID")
    p_edit.add_argument("--title", help="New title")
    p_edit.add_argument("--status", choices=["todo", "in_progress", "done", "blocked"])
    p_edit.add_argument("--priority", choices=["low", "medium", "high", "urgent"])
    p_edit.add_argument("-D", "--deadline", help="New deadline (YYYY-MM-DD HH:MM:SS)")
    p_edit.add_argument("--description", help="New description")

    # note
    p_note = sub.add_parser("note", help="Add a quick note")
    p_note.add_argument("content", nargs="+", help="Note content")
    p_note.add_argument("-t", "--tag", help="Tag for the note")

    # notes
    p_notes = sub.add_parser("notes", help="List recent notes")
    p_notes.add_argument("-t", "--tag", help="Filter by tag")

    # delnote
    p_delnote = sub.add_parser("delnote", help="Delete a note")
    p_delnote.add_argument("id", type=int, help="Note ID")

    # projects
    sub.add_parser("projects", help="List projects")

    # onboard
    sub.add_parser("onboard", help="Interactive setup wizard — generates .env and starts Roost")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Onboard runs separately (no DB imports needed)
    if args.command == "onboard":
        from roost.cli.onboard import run_onboard
        run_onboard()
        return

    handlers = {
        "add": cmd_add,
        "list": cmd_list,
        "ls": cmd_list,
        "done": cmd_done,
        "show": cmd_show,
        "rm": cmd_rm,
        "edit": cmd_edit,
        "note": cmd_note,
        "notes": cmd_notes,
        "delnote": cmd_delnote,
        "projects": cmd_projects,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
