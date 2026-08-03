from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.forms import RegistrationForm
from core.models import EmpresaCliente


class EmailLoginTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='proprietario', email='dono@example.com', password='senha-segura',
        )
        EmpresaCliente.objects.create(usuario=self.user, nome='Empresa do dono')

    def test_login_uses_email_password_and_robot_confirmation(self):
        response = self.client.post(reverse('login'), {
            'username': 'DONO@example.com', 'password': 'senha-segura', 'robot_check': 'on',
        })
        self.assertRedirects(response, reverse('prompt_generator'))

    def test_login_rejects_missing_robot_confirmation(self):
        response = self.client.post(reverse('login'), {
            'username': 'dono@example.com', 'password': 'senha-segura',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirme que você não é um robô.')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_registration_rejects_existing_email_case_insensitively(self):
        form = RegistrationForm(data={
            'username': 'outro', 'email': 'DONO@example.com', 'company_name': 'Outra empresa',
            'segment': 'comercio', 'password1': 'senha-forte-987', 'password2': 'senha-forte-987',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('Este e-mail já está em uso.', form.errors['email'])

    def test_login_page_keeps_only_password_recovery_link(self):
        response = self.client.get(reverse('login'))
        self.assertContains(response, 'E-mail')
        self.assertContains(response, 'Não sou um robô')
        self.assertContains(response, 'Esqueci minha senha')
        self.assertNotContains(response, 'Voltar para o site')

    def test_password_recovery_never_shows_internal_menu(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('password_reset'))
        self.assertNotContains(response, 'id="system-menu"')
        self.assertNotContains(response, 'system-header-shell')

    def test_admin_created_user_gets_company_automatically_and_enters_system(self):
        user = get_user_model().objects.create_user(
            username='admin-created', email='admin-created@example.com', password='senha-segura',
        )
        response = self.client.post(reverse('login'), {
            'username': user.email, 'password': 'senha-segura', 'robot_check': 'on',
        })
        self.assertRedirects(response, reverse('prompt_generator'))
        self.assertTrue(EmpresaCliente.objects.filter(usuario=user).exists())

    def test_prompt_route_without_company_creates_internal_company(self):
        user = get_user_model().objects.create_user(
            username='without-company', email='without-company@example.com', password='senha-segura',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('prompt_generator'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmpresaCliente.objects.filter(usuario=user).exists())
