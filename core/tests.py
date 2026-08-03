from io import StringIO
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import AtendimentoSimuladoForm, EmpresaClienteForm, FluxoAtendimentoForm
from .models import Atendimento, EmpresaCliente, FluxoAtendimento, dados_padrao_fluxo
from .services.whatsapp import (
    OfficialApiProvider,
    WhatsAppProviderError,
    build_attendance_message,
    get_provider,
    notify_attendance,
)


class LandingPageTests(TestCase):
    def test_landing_page_is_public_and_contains_commercial_sections(self):
        response = self.client.get(reverse('landing_page'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Organize seus atendimentos do WhatsApp')
        self.assertContains(response, 'Como funciona')
        self.assertContains(response, 'Segmentos')
        self.assertContains(response, 'Benefícios')
        self.assertContains(response, 'Planos simples')

    def test_landing_page_has_login_link(self):
        response = self.client.get(reverse('landing_page'))

        self.assertContains(response, reverse('login'))

    def test_legal_pages_are_public_and_interlinked(self):
        for name in ('politica_privacidade', 'termos_servico', 'exclusao_dados'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'paulojeissoncostac@gmail.com')


class MvpFinalizationTests(TestCase):
    def test_seed_demo_is_idempotent(self):
        output = StringIO()

        call_command('seed_demo', stdout=output)
        call_command('seed_demo', stdout=output)

        company = EmpresaCliente.objects.get(usuario__username='demo')
        self.assertEqual(company.atendimentos.count(), 6)
        self.assertTrue(FluxoAtendimento.objects.filter(empresa=company).exists())
        self.assertIn('Demonstração pronta', output.getvalue())

    def test_complete_customer_flow_reaches_owner_panel(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono-final', password='senha-segura')
        company = EmpresaCliente.objects.create(
            usuario=user,
            nome='Negócio Final',
            whatsapp_dono='5511999999999',
        )
        flow = FluxoAtendimento.objects.create(
            empresa=company,
            **dados_padrao_fluxo(company),
        )

        public_response = self.client.post(
            reverse('atendimento_publico', args=[company.public_slug]),
            {
                'opcao_escolhida': flow.opcoes[0],
                'nome_cliente': 'Cliente Ponta a Ponta',
                'telefone_cliente': '11988887777',
                'necessidade': 'Precisa de atendimento.',
                'observacao': '',
            },
        )
        self.assertEqual(public_response.status_code, 200)

        self.client.login(username='dono-final', password='senha-segura')
        dashboard_response = self.client.get(reverse('dashboard'))
        attendance_response = self.client.get(reverse('atendimentos'))

        self.assertRedirects(dashboard_response, reverse('prompt_generator'))
        self.assertContains(attendance_response, 'Cliente Ponta a Ponta')
        self.assertContains(attendance_response, 'Avisar no WhatsApp')


class WhatsAppServiceTests(TestCase):
    def create_attendance(self):
        User = get_user_model()
        user = User.objects.create_user(username='servico', password='senha-segura')
        company = EmpresaCliente.objects.create(
            usuario=user,
            nome='Estacionamento Central',
            whatsapp_dono='(88) 99999-9999',
        )
        return Atendimento.objects.create(
            empresa=company,
            nome_cliente='João',
            telefone_cliente='88988887777',
            opcao_escolhida='Saber preço',
            necessidade='Quer saber a diária.',
            observacao='Carro pequeno.',
        )

    def test_default_provider_builds_encoded_wame_url(self):
        attendance = self.create_attendance()

        result = notify_attendance(attendance)
        parsed_url = urlparse(result.redirect_url)
        message = parse_qs(parsed_url.query)['text'][0]

        self.assertEqual(result.provider, 'wa.me')
        self.assertEqual(parsed_url.netloc, 'wa.me')
        self.assertEqual(parsed_url.path, '/88999999999')
        self.assertIn('Cliente: João', message)
        self.assertIn('Opção: Saber preço', message)

    def test_attendance_message_contains_optional_observation(self):
        message = build_attendance_message(self.create_attendance())

        self.assertIn('Observação: Carro pequeno.', message)

    @override_settings(WHATSAPP_PROVIDER='official')
    def test_future_provider_is_selectable_and_fails_safely(self):
        self.assertIsInstance(get_provider(), OfficialApiProvider)

        with self.assertRaisesMessage(WhatsAppProviderError, 'fase posterior'):
            notify_attendance(self.create_attendance())

    @override_settings(WHATSAPP_PROVIDER='desconhecido')
    def test_invalid_provider_has_clear_configuration_error(self):
        with self.assertRaisesMessage(WhatsAppProviderError, 'Provider "desconhecido" inválido'):
            get_provider()


class DashboardAccessTests(TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_legacy_dashboard_redirects_to_prompt_generator(self):
        User = get_user_model()
        owner = User.objects.create_user(username='dono', password='senha-segura')
        other = User.objects.create_user(username='outro', password='senha-segura')
        EmpresaCliente.objects.create(usuario=owner, nome='Estacionamento Central')
        EmpresaCliente.objects.create(usuario=other, nome='Clinica Norte')

        self.client.login(username='dono', password='senha-segura')
        response = self.client.get(reverse('dashboard'))

        self.assertRedirects(response, reverse('prompt_generator'))

    @override_settings(PUBLIC_BASE_URL='https://public.example')
    def test_login_uses_the_new_menu_entry_screen(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono-ngrok', email='dono@example.com', password='senha-segura')
        company = EmpresaCliente.objects.create(usuario=user, nome='Empresa Ngrok')
        self.client.login(username='dono-ngrok', password='senha-segura')

        self.client.logout()
        response = self.client.post(
            reverse('login'),
            {'username': 'dono@example.com', 'password': 'senha-segura', 'robot_check': 'on'},
        )
        self.assertRedirects(response, reverse('prompt_generator'))


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


class ConfiguracoesContaTests(TestCase):
    def test_settings_require_login(self):
        response = self.client.get(reverse('configuracoes'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_legacy_settings_redirects_to_password_screen(self):
        User = get_user_model()
        user = User.objects.create_user(username='conta', password='senha-segura')
        self.client.login(username='conta', password='senha-segura')

        response = self.client.get(reverse('configuracoes'))
        self.assertRedirects(response, reverse('trocar_senha'))

    def test_password_change_keeps_user_logged_in(self):
        User = get_user_model()
        user = User.objects.create_user(username='conta', password='senha-antiga')
        self.client.login(username='conta', password='senha-antiga')

        response = self.client.post(reverse('trocar_senha'), {
            'old_password': 'senha-antiga',
            'new_password1': 'senha-nova-segura',
            'new_password2': 'senha-nova-segura',
        })

        self.assertRedirects(response, reverse('trocar_senha'))
        user.refresh_from_db()
        self.assertTrue(user.check_password('senha-nova-segura'))
        self.assertEqual(self.client.get(reverse('trocar_senha')).status_code, 200)

    def test_mismatched_passwords_are_rejected(self):
        User = get_user_model()
        user = User.objects.create_user(username='conta', password='senha-antiga')
        self.client.login(username='conta', password='senha-antiga')

        response = self.client.post(reverse('trocar_senha'), {
            'old_password': 'senha-antiga',
            'new_password1': 'senha-nova-segura',
            'new_password2': 'senha-diferente',
        })

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('senha-antiga'))


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

    def test_flow_page_shows_templates_for_commercial_segments(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        EmpresaCliente.objects.create(usuario=user, nome='Empresa Modelo')
        self.client.login(username='dono', password='senha-segura')

        response = self.client.get(reverse('fluxo'))

        self.assertContains(response, 'Clínica')
        self.assertContains(response, 'Advocacia')
        self.assertContains(response, 'Estacionamento')
        self.assertContains(response, 'Contabilidade')

    def test_owner_can_apply_segment_template_to_own_flow(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(
            usuario=user,
            nome='Empresa Modelo',
            segmento=EmpresaCliente.SEGMENTO_ESTACIONAMENTO,
        )
        fluxo = FluxoAtendimento.objects.create(
            empresa=empresa,
            saudacao='Fluxo antigo',
            pergunta_menu='Pergunta antiga',
            pergunta_dados='Dados antigos',
            pergunta_finalizacao='Fim antigo',
            opcoes=['Opcao antiga', 'Outra opcao'],
        )
        self.client.login(username='dono', password='senha-segura')

        response = self.client.post(reverse('aplicar_template_fluxo'), {
            'segmento': EmpresaCliente.SEGMENTO_CLINICA,
        })

        self.assertRedirects(response, reverse('fluxo'))
        empresa.refresh_from_db()
        fluxo.refresh_from_db()
        self.assertEqual(empresa.segmento, EmpresaCliente.SEGMENTO_CLINICA)
        self.assertIn('Agendar consulta', fluxo.opcoes)
        self.assertIn(empresa.nome, fluxo.saudacao)

    def test_invalid_template_does_not_change_flow(self):
        User = get_user_model()
        user = User.objects.create_user(username='dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(usuario=user, nome='Empresa Modelo')
        fluxo = FluxoAtendimento.objects.create(
            empresa=empresa,
            saudacao='Fluxo preservado',
            pergunta_menu='Menu',
            pergunta_dados='Dados',
            pergunta_finalizacao='Fim',
            opcoes=['Opcao A', 'Opcao B'],
        )
        self.client.login(username='dono', password='senha-segura')

        response = self.client.post(reverse('aplicar_template_fluxo'), {
            'segmento': 'segmento-invalido',
        })

        self.assertRedirects(response, reverse('fluxo'))
        fluxo.refresh_from_db()
        self.assertEqual(fluxo.saudacao, 'Fluxo preservado')


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
        self.assertIn('Opção: Saber preco', query['text'][0])

        panel_response = self.client.get(reverse('atendimentos'))
        self.assertContains(panel_response, 'Avisado')
        self.assertContains(panel_response, 'Avisar novamente')

    def test_whatsapp_notice_rejects_unsafe_return_url_when_phone_is_missing(self):
        user, empresa = self.criar_empresa_com_usuario()
        empresa.whatsapp_dono = ''
        empresa.save(update_fields=['whatsapp_dono'])
        atendimento = self.criar_atendimento(empresa)
        self.client.login(username=user.username, password='senha-segura')

        response = self.client.post(
            reverse('avisar_whatsapp_atendimento', args=[atendimento.id]),
            {'next': 'https://site-malicioso.example/'},
        )

        self.assertRedirects(response, reverse('atendimentos'))

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
