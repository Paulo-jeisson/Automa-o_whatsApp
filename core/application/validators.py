from django.core.exceptions import ValidationError


def validate_instance_name(value):
    if not value or not value.replace('-', '').replace('_', '').isalnum():
        raise ValidationError('Identificador de sessão inválido.')


def required_text(value, label):
    value = (value or '').strip()
    if not value:
        raise ValidationError(f'{label} é obrigatório.')
    return value
