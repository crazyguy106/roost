# Top-level keys per event (Chatwoot 4.14.1)

| file | event | top-level keys |
|---|---|---|
| `01_contact_created.json` | `contact_created` | `account, additional_attributes, avatar, blocked, custom_attributes, email, event, id, identifier, name, phone_number, thumbnail` |
| `02_conversation_created.json` | `conversation_created` | `additional_attributes, agent_last_seen_at, can_reply, channel, contact_inbox, contact_last_seen_at, created_at, custom_attributes, event, first_reply_created_at, id, inbox_id, labels, last_activity_at, messages, meta, priority, snoozed_until, status, timestamp, unread_count, updated_at, waiting_since` |
| `03_message_created_incoming.json` | `message_created` | `account, additional_attributes, content, content_attributes, content_type, conversation, created_at, event, id, inbox, message_type, private, sender, source_id` |
| `04_message_created_outgoing.json` | `message_created` | `account, additional_attributes, content, content_attributes, content_type, conversation, created_at, event, id, inbox, message_type, private, sender, source_id` |
| `05_contact_updated.json` | `contact_updated` | `account, additional_attributes, avatar, blocked, changed_attributes, custom_attributes, email, event, id, identifier, name, phone_number, thumbnail` |
| `06_conversation_updated.json` | `conversation_updated` | `additional_attributes, agent_last_seen_at, can_reply, changed_attributes, channel, contact_inbox, contact_last_seen_at, created_at, custom_attributes, event, first_reply_created_at, id, inbox_id, labels, last_activity_at, messages, meta, priority, snoozed_until, status, timestamp, unread_count, updated_at, waiting_since` |
| `07_conversation_status_changed.json` | `conversation_status_changed` | `additional_attributes, agent_last_seen_at, can_reply, changed_attributes, channel, contact_inbox, contact_last_seen_at, created_at, custom_attributes, event, first_reply_created_at, id, inbox_id, labels, last_activity_at, messages, meta, priority, snoozed_until, status, timestamp, unread_count, updated_at, waiting_since` |
