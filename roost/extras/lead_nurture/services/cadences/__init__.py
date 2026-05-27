"""Lead-nurture cadences: ordered template sends with optional pre-approval.

A cadence is a list of steps. Each step picks a `response_templates` row by
name, applies variable interpolation, and sends through the chosen channel
(email / whatsapp / telegram). The nurture tick advances enrollments and
either schedules the send (pre-approved) or drops a Telegram approval draft.

Library YAML files ship under `library/`; user overrides live under
`data/cadences/`. Seeding is idempotent and never overwrites a customised
global default — same pattern as `roost.services.rpa_flows`.
"""

from roost.extras.lead_nurture.services.cadences.loader import (
    LIBRARY_DIR,
    USER_DIR,
    dump_yaml,
    export_from_db,
    import_to_db,
    list_library,
    load_yaml,
    seed_library,
)
from roost.extras.lead_nurture.services.cadences.store import (
    create_preapproval,
    delete_cadence,
    delete_preapproval,
    enroll_lead,
    exit_enrollments_by_contact,
    exit_enrollments_by_deal,
    get_cadence,
    handle_stage_change,
    pause_enrollments_by_deal,
    get_cadence_by_slug,
    get_enrollment,
    list_cadences,
    list_due_enrollments,
    list_enrollments,
    list_preapprovals,
    mark_inbound_for_contact,
    match_preapproval,
    pause_enrollment,
    resume_enrollment,
    set_cadence,
    update_enrollment,
)

__all__ = [
    "LIBRARY_DIR",
    "USER_DIR",
    "create_preapproval",
    "delete_cadence",
    "delete_preapproval",
    "dump_yaml",
    "enroll_lead",
    "exit_enrollments_by_contact",
    "exit_enrollments_by_deal",
    "export_from_db",
    "get_cadence",
    "handle_stage_change",
    "get_cadence_by_slug",
    "get_enrollment",
    "import_to_db",
    "list_cadences",
    "list_due_enrollments",
    "list_enrollments",
    "list_library",
    "list_preapprovals",
    "load_yaml",
    "mark_inbound_for_contact",
    "match_preapproval",
    "pause_enrollment",
    "pause_enrollments_by_deal",
    "resume_enrollment",
    "seed_library",
    "set_cadence",
    "update_enrollment",
]
