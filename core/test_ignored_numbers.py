from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Atendimento, Contato, EmpresaCliente, IgnoredPhoneNumber, Mensagem
from core.services.whatsapp.outbound import send_automatic_reply


class IgnoredPhoneNumberTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('pass-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Pass')
        self.client.force_login(self.user)

    def test_add_normalizes_and_lists_phone(self):
        response = self.client.post(reverse('ignored_numbers'), {
            'phone_number': '+55 (27) 99999-9999', 'name': 'Paulo',
        })
        self.assertRedirects(response, reverse('ignored_numbers'))
        number = IgnoredPhoneNumber.objects.get(empresa=self.company)
        self.assertEqual(number.phone_number, '5527999999999')
        page = self.client.get(reverse('ignored_numbers'))
        self.assertContains(page, 'NÚMEROS PASS')
        self.assertContains(page, '5527999999999')

    @patch('core.services.whatsapp.outbound.AIConversationService.reply')
    def test_pass_number_never_calls_ai_or_sends_automatic_reply(self, ai_reply):
        IgnoredPhoneNumber.objects.create(empresa=self.company, phone_number='5527999999999')
        contact = Contato.objects.create(empresa=self.company, whatsapp_id='5527999999999', nome='Cliente')
        attendance = Atendimento.objects.create(
            empresa=self.company, contato=contact, nome_cliente='Cliente', telefone_cliente='27999999999',
            opcao_escolhida='Ajuda', necessidade='Teste',
        )
        inbound = Mensagem.objects.create(
            empresa=self.company, atendimento=attendance, contato=contact,
            external_message_id='pass-inbound-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Olá',
        )
        self.assertIsNone(send_automatic_reply(inbound))
        ai_reply.assert_not_called()

    def test_other_company_cannot_see_or_delete_number(self):
        number = IgnoredPhoneNumber.objects.create(empresa=self.company, phone_number='5527999999999', name='Segredo A')
        other_user = get_user_model().objects.create_user('pass-other', password='safe-password')
        EmpresaCliente.objects.create(usuario=other_user, nome='Outra Empresa')
        self.client.force_login(other_user)
        self.assertNotContains(self.client.get(reverse('ignored_numbers')), 'Segredo A')
        self.assertEqual(self.client.post(reverse('ignored_number_delete', args=[number.id])).status_code, 404)
        self.assertTrue(IgnoredPhoneNumber.objects.filter(pk=number.id).exists())
