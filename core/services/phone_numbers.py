import re


WHATSAPP_SUFFIXES = ('@s.whatsapp.net', '@c.us')


def normalize_phone_number(value):
    """Return the canonical digits-only representation used across WhatsApp flows."""
    text = str(value or '').strip().lower()
    for suffix in WHATSAPP_SUFFIXES:
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break
    return re.sub(r'\D', '', text)[:32]


def brazilian_phone_variants(value):
    """Comparable BR variants with/without country code and mobile ninth digit."""
    normalized = normalize_phone_number(value)
    if not normalized:
        return set()
    variants = {normalized}
    national = normalized[2:] if normalized.startswith('55') and len(normalized) in {12, 13} else normalized
    if len(national) in {10, 11}:
        variants.update({national, f'55{national}'})
        ddd, subscriber = national[:2], national[2:]
        if len(subscriber) == 8 and subscriber.startswith(('6', '7', '8', '9')):
            with_ninth = f'{ddd}9{subscriber}'
            variants.update({with_ninth, f'55{with_ninth}'})
        elif len(subscriber) == 9 and subscriber.startswith('9'):
            without_ninth = f'{ddd}{subscriber[1:]}'
            variants.update({without_ninth, f'55{without_ninth}'})
    return variants
