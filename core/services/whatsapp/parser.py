import json
from dataclasses import dataclass

from .exceptions import InvalidWebhookPayload


@dataclass(frozen=True)
class NormalizedWebhookEvent:
    event_type: str
    phone_number_id: str = ''
    message_id: str = ''
    wa_id: str = ''
    contact_name: str = ''
    timestamp: str = ''
    message_type: str = ''
    text: str = ''
    status: str = ''
    error_code: str = ''


def decode_payload(raw_body):
    try:
        payload = json.loads(raw_body)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidWebhookPayload('JSON inválido.') from error
    if not isinstance(payload, dict):
        raise InvalidWebhookPayload('O payload deve ser um objeto JSON.')
    return payload


def parse_webhook_payload(payload):
    events = []
    entries = payload.get('entry')
    if not isinstance(entries, list):
        return [NormalizedWebhookEvent(event_type='unknown')]

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get('changes')
        if not isinstance(changes, list):
            continue
        for change in changes:
            events.extend(_parse_change(change))

    return events or [NormalizedWebhookEvent(event_type='unknown')]


def _parse_change(change):
    if not isinstance(change, dict):
        return [NormalizedWebhookEvent(event_type='unknown')]

    value = change.get('value')
    if not isinstance(value, dict):
        return [NormalizedWebhookEvent(event_type='unknown')]

    metadata = value.get('metadata')
    phone_number_id = metadata.get('phone_number_id', '') if isinstance(metadata, dict) else ''
    contacts = value.get('contacts')
    contact = contacts[0] if isinstance(contacts, list) and contacts and isinstance(contacts[0], dict) else {}
    profile = contact.get('profile') if isinstance(contact.get('profile'), dict) else {}

    events = []
    messages = value.get('messages')
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            text_data = message.get('text')
            text = text_data.get('body', '') if isinstance(text_data, dict) else ''
            events.append(NormalizedWebhookEvent(
                event_type='message',
                phone_number_id=phone_number_id,
                message_id=str(message.get('id', '')),
                wa_id=str(message.get('from') or contact.get('wa_id', '')),
                contact_name=str(profile.get('name', '')),
                timestamp=str(message.get('timestamp', '')),
                message_type=str(message.get('type', 'unknown')),
                text=str(text),
            ))

    statuses = value.get('statuses')
    if isinstance(statuses, list):
        for status_data in statuses:
            if not isinstance(status_data, dict):
                continue
            errors = status_data.get('errors')
            first_error = (
                errors[0]
                if isinstance(errors, list) and errors and isinstance(errors[0], dict)
                else {}
            )
            events.append(NormalizedWebhookEvent(
                event_type='status',
                phone_number_id=phone_number_id,
                message_id=str(status_data.get('id', '')),
                wa_id=str(status_data.get('recipient_id', '')),
                timestamp=str(status_data.get('timestamp', '')),
                status=str(status_data.get('status', 'unknown')),
                error_code=str(first_error.get('code', ''))[:32],
            ))

    if not events:
        events.append(NormalizedWebhookEvent(
            event_type='unknown',
            phone_number_id=phone_number_id,
        ))
    return events
