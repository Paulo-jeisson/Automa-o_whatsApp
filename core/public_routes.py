"""Catálogo único das páginas HTML públicas, usado por segurança e testes."""

PUBLIC_HTML_URL_NAMES = frozenset({
    'landing_page',
    'cadastro',
    'login',
    'password_reset',
    'password_reset_done',
    'password_reset_confirm',
    'password_reset_complete',
    'politica_privacidade',
    'termos_servico',
    'exclusao_dados',
    'atendimento_publico',
})

PUBLIC_TEMPLATE_NAMES = frozenset({
    'core/landing_page.html',
    'core/cadastro.html',
    'core/politica_privacidade.html',
    'core/termos_servico.html',
    'core/exclusao_dados.html',
    'core/atendimento_publico.html',
    'registration/login.html',
    'registration/password_reset_form.html',
    'registration/password_reset_done.html',
    'registration/password_reset_confirm.html',
    'registration/password_reset_complete.html',
})
