from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Atendimento, Contato, EmpresaCliente, Mensagem


class ConversationHistoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('history-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Histórico')
        self.contact = Contato.objects.create(empresa=self.company, whatsapp_id='5527999999999', nome='Cliente')
        self.attendance = Atendimento.objects.create(
            empresa=self.company, contato=self.contact, nome_cliente='Cliente', telefone_cliente='5527999999999',
            opcao_escolhida='Ajuda', necessidade='Caso',
        )
        Mensagem.objects.create(
            empresa=self.company, atendimento=self.attendance, contato=self.contact,
            external_message_id='history-1', direcao='entrada', tipo='text', texto='Preciso de atendimento',
        )
        self.client.force_login(self.user)

    def test_history_uses_reference_layout_and_real_messages(self):
        page = self.client.get(reverse('conversations_crm'))
        self.assertContains(page, 'HISTÓRICO DE CONVERSAS')
        self.assertContains(page, '5527999999999')
        self.assertContains(page, 'Preciso de atendimento')
        self.assertContains(page, 'EXPORTAR')
        self.assertContains(page, 'DELETAR')

    def test_export_is_tenant_scoped(self):
        response = self.client.get(reverse('conversation_export', args=[self.attendance.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('Preciso de atendimento', response.content.decode())

        other_user = get_user_model().objects.create_user('history-other', password='safe-password')
        EmpresaCliente.objects.create(usuario=other_user, nome='Outra')
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(reverse('conversation_export', args=[self.attendance.id])).status_code, 404)

    def test_delete_requires_post_and_cannot_cross_tenant(self):
        self.assertEqual(self.client.get(reverse('conversation_delete', args=[self.attendance.id])).status_code, 405)
        other_user = get_user_model().objects.create_user('delete-other', password='safe-password')
        EmpresaCliente.objects.create(usuario=other_user, nome='Outra')
        self.client.force_login(other_user)
        self.assertEqual(self.client.post(reverse('conversation_delete', args=[self.attendance.id])).status_code, 404)
        self.assertTrue(Atendimento.objects.filter(pk=self.attendance.id).exists())
        self.client.force_login(self.user)
        self.assertRedirects(self.client.post(reverse('conversation_delete', args=[self.attendance.id])), reverse('conversations_crm'))
        self.assertFalse(Atendimento.objects.filter(pk=self.attendance.id).exists())

