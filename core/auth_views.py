from django.contrib.auth.views import LoginView
from django.contrib.auth.views import PasswordResetView
from django.conf import settings
from urllib.parse import urlsplit

from core.access import ensure_company_for_user
from core.auth_forms import EmailAuthenticationForm, SingleEmailPasswordResetForm


class EmailLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = EmailAuthenticationForm

    def get_success_url(self):
        ensure_company_for_user(self.request.user)
        return super().get_success_url()


class IAAtendePasswordResetView(PasswordResetView):
    template_name = 'registration/password_reset_form.html'
    form_class = SingleEmailPasswordResetForm
    email_template_name = 'registration/password_reset_email.txt'
    html_email_template_name = 'registration/password_reset_email.html'
    subject_template_name = 'registration/password_reset_subject.txt'

    def form_valid(self, form):
        use_request_domain = getattr(settings, 'PASSWORD_RESET_USE_REQUEST_DOMAIN', settings.DEBUG)
        public_url = '' if use_request_domain else settings.PUBLIC_BASE_URL
        parsed = urlsplit(public_url) if public_url else None
        options = {
            'domain_override': parsed.netloc if parsed else None,
            'use_https': parsed.scheme == 'https' if parsed else self.request.is_secure(),
            'token_generator': self.token_generator,
            'from_email': self.from_email,
            'email_template_name': self.email_template_name,
            'subject_template_name': self.subject_template_name,
            'request': self.request,
            'html_email_template_name': self.html_email_template_name,
            'extra_email_context': self.extra_email_context,
        }
        form.save(**options)
        return super(PasswordResetView, self).form_valid(form)
