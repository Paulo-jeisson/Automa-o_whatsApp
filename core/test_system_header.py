from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from core.models import EmpresaCliente, WhatsAppSession


class SystemHeaderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('header-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Header')
        self.client.force_login(self.user)

    def test_header_qr_area_is_rendered_above_unchanged_menu(self):
        WhatsAppSession.objects.create(
            empresa=self.company, instance_name='header-instance', state='WAITING_QR',
            qr_code='data:image/png;base64,cXItY29kZQ==',
        )
        content = self.client.get(reverse('conversations_crm')).content.decode()
        self.assertIn('ATENDE', content)
        self.assertIn('CONECTAR WHATSAPP', content)
        self.assertIn('data:image/png;base64,cXItY29kZQ==', content)
        self.assertLess(content.index('system-header-shell'), content.index('system-toolbar'))

    def test_menu_tabs_keep_viewport_below_qr_panel(self):
        content = self.client.get(reverse('conversations_crm')).content.decode()
        self.assertIn('id="system-menu"', content)
        self.assertIn(f'href="{reverse("agenda")}"', content)
        self.assertIn(f'href="{reverse("ignored_numbers")}"', content)
        self.assertIn(f'href="{reverse("conversations_crm")}"', content)
        self.assertIn(f'href="{reverse("prompt_editor")}"', content)
        self.assertIn('id="system-page-content"', content)
        self.assertIn("IAATENDE-Menu", content)

    def test_authenticated_layout_loads_scoped_navigation_state_utility(self):
        content = self.client.get(reverse('conversations_crm')).content.decode()
        self.assertIn('data-authenticated="true"', content)
        self.assertIn(f'data-scroll-user="{self.user.pk}"', content)
        self.assertIn(f'data-scroll-tenant="{self.company.pk}"', content)
        self.assertIn('/static/core/js/navigation_state.js?v=2', content)

    def test_scroll_bootstrap_runs_in_head_and_only_hides_for_valid_pending_state(self):
        content = self.client.get(reverse('conversations_crm')).content.decode()
        head = content[:content.index('</head>')]
        self.assertIn("sessionStorage.getItem(key)", head)
        self.assertIn("sessionStorage.removeItem(key)", head)
        self.assertIn("state.path!==location.pathname", head)
        self.assertIn("age>2*60*1000", head)
        self.assertIn("classList.add('ia-scroll-restoring')", head)
        self.assertIn("DOMContentLoaded", head)
        self.assertIn("html.ia-scroll-restoring body{visibility:hidden}", head)
        self.assertNotIn('setTimeout(', head)
        self.assertLess(content.index('sessionStorage.getItem(key)'), content.index('<body'))

    def test_system_is_active_only_after_qr_connection_is_confirmed(self):
        session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='header-validation', state='CONNECTED',
        )
        page = self.client.get(reverse('conversations_crm'))
        self.assertContains(page, 'SISTEMA INATIVO')

        session.connected_at = timezone.now()
        session.save(update_fields=['connected_at'])
        page = self.client.get(reverse('conversations_crm'))
        self.assertContains(page, 'SISTEMA ATIVO')
        self.assertNotContains(page, 'system-live-badge inactive')

    def test_header_never_uses_another_company_session(self):
        other_user = get_user_model().objects.create_user('header-other', password='safe-password')
        other = EmpresaCliente.objects.create(usuario=other_user, nome='Outra')
        WhatsAppSession.objects.create(
            empresa=other, instance_name='secret-instance', state='WAITING_QR',
            qr_code='data:image/png;base64,c2VncmVkbw==',
        )
        page = self.client.get(reverse('conversations_crm'))
        self.assertNotContains(page, 'c2VncmVkbw==')

    def test_generate_qr_returns_to_current_screen(self):
        session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='header-connect', state='WAITING_QR',
            qr_code='data:image/png;base64,bm92by1xcg==',
        )
        current_screen = f"{reverse('conversations_crm')}#whatsapp-qr"
        with patch(
            'core.presentation.module_views.WhatsAppSessionService.connect',
            return_value=session,
        ):
            response = self.client.post(
                reverse('whatsapp_action', args=['connect']),
                {'next': current_screen},
            )
        self.assertRedirects(response, current_screen, fetch_redirect_response=False)

    def test_legacy_whatsapp_page_redirects_to_new_qr_panel(self):
        response = self.client.get(reverse('whatsapp_dashboard'))
        self.assertRedirects(
            response,
            f"{reverse('prompt_generator')}#whatsapp-qr",
            fetch_redirect_response=False,
        )
