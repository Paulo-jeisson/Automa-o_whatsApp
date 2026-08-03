from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.core.exceptions import ValidationError


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label='E-mail',
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True}),
    )
    robot_check = forms.BooleanField(
        label='Não sou um robô', required=True,
        error_messages={'required': 'Confirme que você não é um robô.'},
    )

    error_messages = {
        'invalid_login': 'E-mail ou senha inválidos.',
        'inactive': 'Esta conta está inativa.',
    }

    def clean(self):
        email = self.cleaned_data.get('username', '').strip().lower()
        password = self.cleaned_data.get('password')
        self.cleaned_data['username'] = email
        if email and password and self.cleaned_data.get('robot_check'):
            matches = list(get_user_model().objects.filter(email__iexact=email).only('username')[:2])
            authentication_name = matches[0].username if len(matches) == 1 else email
            self.user_cache = authenticate(self.request, username=authentication_name, password=password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class SingleEmailPasswordResetForm(PasswordResetForm):
    """Mantém resposta neutra e evita e-mails duplicados para dados legados."""

    def get_users(self, email):
        users = super().get_users(email)
        first = next(iter(users), None)
        if first is not None:
            yield first
