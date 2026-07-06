from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import AtendimentoSimuladoForm, EmpresaClienteForm, FluxoAtendimentoForm
from .models import Atendimento, EmpresaCliente, FluxoAtendimento


class DashboardAccessTests(TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_dashboard_uses_only_logged_user_company(self):
        User = get_user_model()
        owner = User.objects.create_user(username='dono', password='senha-segura')
        other = User.objects.create_user(username='outro', password='senha-segura')
        EmpresaCliente.objects.create(usuario=owner, nome='Estacionamento Central')
        EmpresaCliente.objects.create(usuario=other, nome='Clinica Norte')

        self.client.login(username='dono', password='senha-segura')
        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Estacionamento Central')
        self.assertNotContains(response, 'Clinica Norte')


class MinhaEmpresaTests(TestCase):
    def test_minha_empresa_requires_login(self):
        response = self.client.get(reverse('minha_empresa'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_logged_user_can_create_own_company(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        self.client.login(username='dono', password='senha-segura')

        response = self.client.post(reverse('minha_empresa'), {
            'nome': 'Estacionamento Central',
            'segmento': EmpresaCliente.SEGMENTO_ESTACIONAMENTO,
            'nome_dono': 'Paulo',
            'whatsapp_dono': '(88) 99999-9999',
            'endereco': 'Rua Principal, 100',
            'horario_funcionamento': 'Segunda a sabado, 8h as 18h',
            'mensagem_inicial': 'Ola, como podemos ajudar?',
            'ativa': 'on',
        })

        self.assertRedirects(response, reverse('minha_empresa'))
        empresa = EmpresaCliente.objects.get(usuario=user)
        self.assertEqual(empresa.nome, 'Estacionamento Central')
        self.assertEqual(empresa.whatsapp_dono, '88999999999')

    def test_logged_user_updates_only_own_company(self):
        User = get_user_model()
        owner = User.objects.create_user(username='dono', password='senha-segura')
        other = User.objects.create_user(username='outro', password='senha-segura')
        EmpresaCliente.objects.create(usuario=owner, nome='Empresa Antiga')
        EmpresaCliente.objects.create(usuario=other, nome='Empresa de Outro Usuario')
        self.client.login(username='dono', password='senha-segura')

        self.client.post(reverse('minha_empresa'), {
            'nome': 'Empresa Atualizada',
            'segmento': EmpresaCliente.SEGMENTO_COMERCIO,
            'nome_dono': 'Dono',
            'whatsapp_dono': '5588999999999',
            'endereco': '',
            'horario_funcionamento': '',
            'mensagem_inicial': '',
            'ativa': 'on',
        })

        self.assertEqual(EmpresaCliente.objects.get(usuario=owner).nome, 'Empresa Atualizada')
        self.assertEqual(EmpresaCliente.objects.get(usuario=other).nome, 'Empresa de Outro Usuario')

    def test_whatsapp_validation_rejects_short_number(self):
        form = EmpresaClienteForm(data={
            'nome': 'Estacionamento Central',
            'segmento': EmpresaCliente.SEGMENTO_ESTACIONAMENTO,
            'nome_dono': 'Paulo',
            'whatsapp_dono': '1234',
            'endereco': '',
            'horario_funcionamento': '',
            'mensagem_inicial': '',
            'ativa': 'on',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('whatsapp_dono', form.errors)


class FluxoAtendimentoTests(TestCase):
    def test_fluxo_requires_login(self):
        response = self.client.get(reverse('fluxo'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_fluxo_redirects_when_user_has_no_company(self):
        User = get_user_model()
        User.objects.create_user(username='dono', password='senha-segura')
        self.client.login(username='dono', password='senha-segura')

        response = self.client.get(reverse('fluxo'))

        self.assertRedirects(response, reverse('minha_empresa'))

    def test_fluxo_creates_default_options_by_company_segment(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(
            usuario=user,
            nome='Clinica Norte',
            segmento=EmpresaCliente.SEGMENTO_CLINICA,
        )
        self.client.login(username='dono', password='senha-segura')

        response = self.client.get(reverse('fluxo'))

        self.assertEqual(response.status_code, 200)
        fluxo = FluxoAtendimento.objects.get(empresa=empresa)
        self.assertIn('Agendar consulta', fluxo.opcoes)
        self.assertContains(response, 'Agendar consulta')

    def test_logged_user_can_update_own_flow_options(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(usuario=user, nome='Estacionamento Central')
        fluxo = FluxoAtendimento.objects.create(
            empresa=empresa,
            saudacao='Ola',
            pergunta_menu='Como ajudar?',
            pergunta_dados='Informe seus dados.',
            pergunta_finalizacao='Obrigado.',
            opcoes=['Saber preco', 'Falar com atendente'],
        )
        self.client.login(username='dono', password='senha-segura')

        response = self.client.post(reverse('fluxo'), {
            'saudacao': 'Ola, bem-vindo.',
            'pergunta_menu': 'Escolha uma opcao.',
            'pergunta_dados': 'Informe nome e telefone.',
            'pergunta_finalizacao': 'Atendimento registrado.',
            'opcoes_texto': 'Consultar mensalidade\nFalar com atendente\nVer horario',
        })

        self.assertRedirects(response, reverse('fluxo'))
        fluxo.refresh_from_db()
        self.assertEqual(fluxo.saudacao, 'Ola, bem-vindo.')
        self.assertEqual(fluxo.opcoes, ['Consultar mensalidade', 'Falar com atendente', 'Ver horario'])

    def test_fluxo_page_does_not_show_other_company_flow(self):
        User = get_user_model()
        owner = User.objects.create_user(username='dono', password='senha-segura')
        other = User.objects.create_user(username='outro', password='senha-segura')
        owner_empresa = EmpresaCliente.objects.create(usuario=owner, nome='Empresa do Dono')
        other_empresa = EmpresaCliente.objects.create(usuario=other, nome='Empresa de Outro')
        FluxoAtendimento.objects.create(
            empresa=owner_empresa,
            saudacao='Saudacao do dono',
            pergunta_menu='Menu do dono',
            pergunta_dados='Dados do dono',
            pergunta_finalizacao='Fim do dono',
            opcoes=['Opcao do dono', 'Atendente'],
        )
        FluxoAtendimento.objects.create(
            empresa=other_empresa,
            saudacao='Saudacao de outro',
            pergunta_menu='Menu de outro',
            pergunta_dados='Dados de outro',
            pergunta_finalizacao='Fim de outro',
            opcoes=['Opcao secreta', 'Atendente'],
        )
        self.client.login(username='dono', password='senha-segura')

        response = self.client.get(reverse('fluxo'))

        self.assertContains(response, 'Opcao do dono')
        self.assertNotContains(response, 'Opcao secreta')

    def test_fluxo_form_requires_at_least_two_options(self):
        form = FluxoAtendimentoForm(data={
            'saudacao': 'Ola',
            'pergunta_menu': 'Como ajudar?',
            'pergunta_dados': 'Informe seus dados.',
            'pergunta_finalizacao': 'Obrigado.',
            'opcoes_texto': 'Apenas uma',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('opcoes_texto', form.errors)


class AtendimentoPublicoTests(TestCase):
    def test_company_gets_public_slug_on_save(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(usuario=user, nome='Estacionamento Central')

        self.assertEqual(empresa.public_slug, 'estacionamento-central')

    def test_public_simulator_does_not_require_login_and_shows_flow_options(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(usuario=user, nome='Estacionamento Central')
        FluxoAtendimento.objects.create(
            empresa=empresa,
            saudacao='Ola do estacionamento',
            pergunta_menu='Escolha uma opcao.',
            pergunta_dados='Informe nome, telefone e necessidade.',
            pergunta_finalizacao='Obrigado.',
            opcoes=['Saber preco', 'Falar com atendente'],
        )

        response = self.client.get(reverse('atendimento_publico', kwargs={'public_slug': empresa.public_slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ola do estacionamento')
        self.assertContains(response, 'Saber preco')

    def test_public_simulator_saves_attendance(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(usuario=user, nome='Estacionamento Central')
        FluxoAtendimento.objects.create(
            empresa=empresa,
            saudacao='Ola',
            pergunta_menu='Escolha uma opcao.',
            pergunta_dados='Informe dados.',
            pergunta_finalizacao='Atendimento registrado.',
            opcoes=['Saber preco', 'Falar com atendente'],
        )

        response = self.client.post(reverse('atendimento_publico', kwargs={'public_slug': empresa.public_slug}), {
            'opcao_escolhida': 'Saber preco',
            'nome_cliente': 'Joao',
            'telefone_cliente': '(88) 99999-9999',
            'necessidade': 'Quer saber valor da diaria.',
            'observacao': 'Tem carro pequeno.',
        })

        self.assertEqual(response.status_code, 200)
        atendimento = Atendimento.objects.get(empresa=empresa)
        self.assertEqual(atendimento.nome_cliente, 'Joao')
        self.assertEqual(atendimento.telefone_cliente, '88999999999')
        self.assertEqual(atendimento.opcao_escolhida, 'Saber preco')
        self.assertEqual(atendimento.status, Atendimento.STATUS_NOVO)
        self.assertContains(response, 'Atendimento registrado')

    def test_public_simulator_rejects_option_outside_flow(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(usuario=user, nome='Estacionamento Central')
        FluxoAtendimento.objects.create(
            empresa=empresa,
            saudacao='Ola',
            pergunta_menu='Escolha uma opcao.',
            pergunta_dados='Informe dados.',
            pergunta_finalizacao='Obrigado.',
            opcoes=['Saber preco', 'Falar com atendente'],
        )

        response = self.client.post(reverse('atendimento_publico', kwargs={'public_slug': empresa.public_slug}), {
            'opcao_escolhida': 'Opcao falsa',
            'nome_cliente': 'Joao',
            'telefone_cliente': '88999999999',
            'necessidade': 'Teste',
            'observacao': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Atendimento.objects.exists())

    def test_inactive_company_public_link_returns_404(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(
            usuario=user,
            nome='Estacionamento Central',
            ativa=False,
        )

        response = self.client.get(reverse('atendimento_publico', kwargs={'public_slug': empresa.public_slug}))

        self.assertEqual(response.status_code, 404)

    def test_public_phone_validation_rejects_short_number(self):
        form = AtendimentoSimuladoForm(data={
            'opcao_escolhida': 'Saber preco',
            'nome_cliente': 'Joao',
            'telefone_cliente': '1234',
            'necessidade': 'Teste',
            'observacao': '',
        }, fluxo=type('FluxoFake', (), {'opcoes': ['Saber preco', 'Falar com atendente']})())

        self.assertFalse(form.is_valid())
        self.assertIn('telefone_cliente', form.errors)


class PainelAtendimentosTests(TestCase):
    def criar_empresa_com_usuario(self, username='dono', nome='Estacionamento Central'):
        User = get_user_model()
        user = User.objects.create_user(username=username, password='senha-segura')
        empresa = EmpresaCliente.objects.create(
            usuario=user,
            nome=nome,
            segmento=EmpresaCliente.SEGMENTO_ESTACIONAMENTO,
            whatsapp_dono='5588999999999',
        )
        return user, empresa

    def criar_atendimento(self, empresa, nome='Cliente', status=Atendimento.STATUS_NOVO):
        return Atendimento.objects.create(
            empresa=empresa,
            nome_cliente=nome,
            telefone_cliente='88999999999',
            opcao_escolhida='Saber preco',
            necessidade='Quer saber o valor.',
            observacao='',
            status=status,
        )

    def test_atendimentos_requires_login(self):
        response = self.client.get(reverse('atendimentos'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_atendimentos_redirects_when_user_has_no_company(self):
        User = get_user_model()
        User.objects.create_user(username='dono', password='senha-segura')
        self.client.login(username='dono', password='senha-segura')

        response = self.client.get(reverse('atendimentos'))

        self.assertRedirects(response, reverse('minha_empresa'))

    def test_atendimentos_list_shows_only_logged_user_company(self):
        user, empresa = self.criar_empresa_com_usuario()
        _other_user, other_empresa = self.criar_empresa_com_usuario('outro', 'Clinica Norte')
        self.criar_atendimento(empresa, nome='Cliente Dono')
        self.criar_atendimento(other_empresa, nome='Cliente Outro')
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.get(reverse('atendimentos'))

        self.assertContains(response, 'Cliente Dono')
        self.assertNotContains(response, 'Cliente Outro')

    def test_atendimentos_filters_by_status(self):
        user, empresa = self.criar_empresa_com_usuario()
        self.criar_atendimento(empresa, nome='Cliente Novo', status=Atendimento.STATUS_NOVO)
        self.criar_atendimento(empresa, nome='Cliente Finalizado', status=Atendimento.STATUS_FINALIZADO)
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.get(reverse('atendimentos'), {'status': Atendimento.STATUS_FINALIZADO})

        self.assertContains(response, 'Cliente Finalizado')
        self.assertNotContains(response, 'Cliente Novo')

    def test_atendimentos_filters_by_date(self):
        user, empresa = self.criar_empresa_com_usuario()
        antigo = self.criar_atendimento(empresa, nome='Cliente Antigo')
        hoje = self.criar_atendimento(empresa, nome='Cliente Hoje')
        Atendimento.objects.filter(pk=antigo.pk).update(criado_em=timezone.datetime(2026, 1, 1, tzinfo=timezone.get_current_timezone()))
        data_hoje = timezone.localdate(hoje.criado_em).isoformat()
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.get(reverse('atendimentos'), {'data': data_hoje})

        self.assertContains(response, 'Cliente Hoje')
        self.assertNotContains(response, 'Cliente Antigo')

    def test_atendimentos_filters_by_segment(self):
        user, empresa = self.criar_empresa_com_usuario()
        empresa.segmento = EmpresaCliente.SEGMENTO_ESTACIONAMENTO
        empresa.save()
        self.criar_atendimento(empresa, nome='Cliente Segmento')
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.get(reverse('atendimentos'), {'segmento': EmpresaCliente.SEGMENTO_CLINICA})

        self.assertNotContains(response, 'Cliente Segmento')
        self.assertContains(response, 'Nenhum atendimento encontrado')

    def test_status_update_changes_only_logged_user_attendance(self):
        user, empresa = self.criar_empresa_com_usuario()
        _other_user, other_empresa = self.criar_empresa_com_usuario('outro', 'Clinica Norte')
        atendimento = self.criar_atendimento(empresa, nome='Cliente Dono')
        atendimento_outro = self.criar_atendimento(other_empresa, nome='Cliente Outro')
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.post(reverse('atualizar_status_atendimento', args=[atendimento.id]), {
            'status': Atendimento.STATUS_EM_ANDAMENTO,
        })

        self.assertRedirects(response, reverse('atendimentos'))
        atendimento.refresh_from_db()
        atendimento_outro.refresh_from_db()
        self.assertEqual(atendimento.status, Atendimento.STATUS_EM_ANDAMENTO)
        self.assertEqual(atendimento_outro.status, Atendimento.STATUS_NOVO)

    def test_status_update_cannot_touch_other_company_attendance(self):
        user, _empresa = self.criar_empresa_com_usuario()
        _other_user, other_empresa = self.criar_empresa_com_usuario('outro', 'Clinica Norte')
        atendimento_outro = self.criar_atendimento(other_empresa, nome='Cliente Outro')
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.post(reverse('atualizar_status_atendimento', args=[atendimento_outro.id]), {
            'status': Atendimento.STATUS_FINALIZADO,
        })

        atendimento_outro.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(atendimento_outro.status, Atendimento.STATUS_NOVO)

    def test_whatsapp_notice_records_timestamp_and_redirects_to_ready_message(self):
        user, empresa = self.criar_empresa_com_usuario()
        atendimento = self.criar_atendimento(empresa, nome='Cliente Dono')
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.post(reverse('avisar_whatsapp_atendimento', args=[atendimento.id]))

        atendimento.refresh_from_db()
        self.assertIsNotNone(atendimento.avisado_em)
        self.assertEqual(response.status_code, 302)
        parsed_url = urlparse(response['Location'])
        query = parse_qs(parsed_url.query)
        self.assertEqual(parsed_url.netloc, 'wa.me')
        self.assertEqual(parsed_url.path, f'/{empresa.whatsapp_dono}')
        self.assertIn('Novo atendimento recebido:', query['text'][0])
        self.assertIn('Cliente: Cliente Dono', query['text'][0])
        self.assertIn('Opcao: Saber preco', query['text'][0])

    def test_whatsapp_notice_cannot_touch_other_company_attendance(self):
        user, _empresa = self.criar_empresa_com_usuario()
        _other_user, other_empresa = self.criar_empresa_com_usuario('outro', 'Clinica Norte')
        atendimento_outro = self.criar_atendimento(other_empresa, nome='Cliente Outro')
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.post(reverse('avisar_whatsapp_atendimento', args=[atendimento_outro.id]))

        atendimento_outro.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(atendimento_outro.avisado_em)

    def test_whatsapp_notice_requires_owner_phone(self):
        user, empresa = self.criar_empresa_com_usuario()
        empresa.whatsapp_dono = ''
        empresa.save(update_fields=['whatsapp_dono'])
        atendimento = self.criar_atendimento(empresa, nome='Cliente Dono')
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.post(reverse('avisar_whatsapp_atendimento', args=[atendimento.id]))

        atendimento.refresh_from_db()
        self.assertRedirects(response, reverse('atendimentos'))
        self.assertIsNone(atendimento.avisado_em)
