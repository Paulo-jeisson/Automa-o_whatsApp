# ZapFluxo

Plataforma Django multiempresa para organização e automação de atendimentos pelo WhatsApp. O MVP preserva o simulador público e os avisos por `wa.me`, enquanto o webhook recebe eventos reais da WhatsApp Business Platform oficial.

## Instalação local

Requisitos: Python 3.12 ou superior e Django 6.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver
```

O comando local usa `app.settings_development` e SQLite. WSGI e ASGI usam
`app.settings_production`, que exige PostgreSQL e as variáveis `POSTGRES_*`.
O procedimento completo de servidor, Nginx, Gunicorn, backup e restauração está
em [`docs/DEPLOY.md`](docs/DEPLOY.md).

Endereços locais:

- Site: `http://127.0.0.1:8000/`
- Login: `http://127.0.0.1:8000/login/`
- Administração: `http://127.0.0.1:8000/admin/`
- Webhook: `http://127.0.0.1:8000/webhooks/whatsapp/`

## Variáveis de ambiente

O projeto carrega o arquivo `.env` local sem substituir variáveis já definidas pelo sistema operacional. O `.env` está ignorado pelo Git.

| Variável | Uso |
|---|---|
| `DJANGO_SECRET_KEY` | Chave secreta do Django; obrigatória com `DEBUG=False` |
| `DEBUG` | `True` somente em desenvolvimento |
| `ALLOWED_HOSTS` | Hosts separados por vírgula, sem protocolo |
| `CSRF_TRUSTED_ORIGINS` | Origens HTTPS separadas por vírgula |
| `PUBLIC_BASE_URL` | URL HTTPS pública, sem barra final |
| `META_VERIFY_TOKEN` | Token escolhido para verificação do webhook |
| `META_APP_SECRET` | App Secret usado na assinatura `X-Hub-Signature-256` |
| `META_APP_ID` | ID do aplicativo que executa o Embedded Signup |
| `META_EMBEDDED_SIGNUP_CONFIG_ID` | ID da configuração de login incorporado criada na Meta |
| `META_GRAPH_API_VERSION` | Versão da Graph API habilitada para o aplicativo |
| `META_ACCESS_TOKEN` | Token legado opcional, somente para integrações manuais antigas |
| `WHATSAPP_TOKEN_ENCRYPTION_KEY` | Chave Fernet exclusiva para criptografar tokens por empresa |
| `SQLITE_NAME` | Caminho opcional do SQLite |
| `POSTGRES_DB` | Nome do banco PostgreSQL de produção |
| `POSTGRES_USER` | Usuário exclusivo da aplicação |
| `POSTGRES_PASSWORD` | Senha obtida do ambiente/cofre |
| `POSTGRES_HOST` | Host do PostgreSQL |
| `POSTGRES_PORT` | Porta do PostgreSQL; padrão 5432 |
| `SESSION_COOKIE_AGE` | Expiração da sessão em segundos |
| `EMAIL_HOST` | Servidor SMTP para recuperação de senha |
| `EMAIL_HOST_USER` | Usuário SMTP |
| `EMAIL_HOST_PASSWORD` | Senha SMTP, somente pelo ambiente |
| `DEFAULT_FROM_EMAIL` | Remetente dos e-mails transacionais |
| `OPENAI_API_KEY` | Chave da OpenAI, exclusiva do backend |
| `AI_ENABLED` | Kill switch global da integração de IA |
| `AI_MODEL` | Modelo utilizado pelo adaptador; padrão `gpt-5.6` |
| `AI_TIMEOUT` | Timeout da chamada ao provedor em segundos |
| `AI_CONTEXT_MESSAGE_LIMIT` | Quantidade máxima de mensagens recentes no contexto |
| `AI_CONTEXT_SUMMARY_TRIGGER` | Total de mensagens que dispara a compactação |
| `AI_CONTEXT_SUMMARY_MAX_CHARS` | Tamanho máximo do resumo persistido |
| `ASAAS_ENVIRONMENT` | `sandbox` durante homologação; `production` somente após aprovação |
| `ASAAS_API_URL` | URL `/v3` correspondente ao ambiente Asaas |
| `ASAAS_API_KEY` | Chave Asaas exclusiva do backend |
| `ASAAS_WEBHOOK_TOKEN` | Token forte e exclusivo do webhook IAATENDE |
| `ASAAS_CHECKOUT_SUCCESS_URL` | Retorno HTTPS após o checkout; não confirma pagamento |
| `ASAAS_CHECKOUT_CANCEL_URL` | Retorno HTTPS de cancelamento |
| `ASAAS_CHECKOUT_EXPIRES_IN` | Validade do checkout em minutos (10–1440) |
| `SUBSCRIPTION_ENFORCEMENT_ENABLED` | Barreira global de assinatura; obrigatório `true` em produção |

Os tokens obtidos pelo Embedded Signup são criptografados por empresa e nunca são
renderizados no navegador ou no admin.

## Onboarding oficial multiempresa

No painel da Meta, crie uma configuração do **WhatsApp Embedded Signup** para o
aplicativo, habilite `whatsapp_business_management` e
`whatsapp_business_messaging`, e cadastre o domínio HTTPS definitivo do ZapFluxo.
Preencha `META_APP_ID`, `META_APP_SECRET` e
`META_EMBEDDED_SIGNUP_CONFIG_ID` no ambiente do servidor.

Gere a chave de criptografia uma única vez e guarde-a no gerenciador de segredos
da hospedagem:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Depois de executar `python manage.py migrate`, cada cliente acessa
**Configurações → Conectar WhatsApp**. A autorização abre na janela oficial da
Meta, valida se o número pertence à WABA escolhida e assina os webhooks. O
Phone Number ID e o token criptografado ficam ligados apenas à empresa do usuário
autenticado. O token global não é usado por conexões novas.

Para comercialização, use domínio próprio estável e HTTPS, `DEBUG=False`, banco
gerenciado, backups, chave de criptografia fora do código e processo de revisão e
verificação exigido pela Meta. O ngrok é adequado somente para desenvolvimento.

## Configuração WhatsApp Cloud API — Desenvolvimento

### 1. Prepare o `.env`

Copie `.env.example` para `.env` e substitua todos os valores fictícios. Gere valores próprios para `DJANGO_SECRET_KEY` e `META_VERIFY_TOKEN`; não reutilize o App Secret como Verify Token.

Exemplo de configuração para um túnel:

```env
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost,DOMINIO_ATUAL.ngrok-free.app
CSRF_TRUSTED_ORIGINS=https://DOMINIO_ATUAL.ngrok-free.app
PUBLIC_BASE_URL=https://DOMINIO_ATUAL.ngrok-free.app
```

Quando o ngrok mudar, atualize essas três variáveis e reinicie o Django. Nenhum domínio ngrok fica hardcoded no projeto.

### 2. Inicie o Django

```powershell
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py runserver
```

Mantenha esse terminal aberto: os registros seguros do webhook aparecerão nele, sem token nem texto completo da conversa.

### 3. Inicie o ngrok

Em outro terminal:

```powershell
ngrok http 8000
```

Copie a URL HTTPS exibida e atualize o `.env` conforme o passo 1. Reinicie o Django após a alteração.

### 4. Configure o callback na Meta

Na configuração de Webhooks do aplicativo que contém o produto WhatsApp, use:

```text
https://DOMINIO_ATUAL.ngrok-free.app/webhooks/whatsapp/
```

No campo de token de verificação, informe exatamente o mesmo valor definido em `META_VERIFY_TOKEN`. O token é escolhido por você e serve para a verificação GET; ele não é o Access Token nem o App Secret.

Depois da validação, assine o campo de eventos de mensagens da conta WhatsApp utilizada no teste. Os nomes e a organização das telas podem mudar na Meta; utilize a área de configuração de Webhooks do produto WhatsApp do seu aplicativo.

### 5. Localize os identificadores

Na área de configuração/API do produto WhatsApp do aplicativo, copie:

- Phone Number ID do número de teste ou número empresarial;
- WhatsApp Business Account ID;
- token temporário de desenvolvimento, somente se for usar **Testar integração**.

Use a versão da Graph API que aparece habilitada para o aplicativo em `META_GRAPH_API_VERSION`.

### 6. Cadastre a integração da empresa

1. Crie um superusuário, se necessário:

   ```powershell
   python manage.py createsuperuser
   ```

2. Acesse `/admin/`.
3. Abra **Integrações do WhatsApp**.
4. Adicione uma integração.
5. Escolha a empresa correta.
6. Informe Phone Number ID e WhatsApp Business Account ID.
7. Marque a integração como ativa.

Cada empresa aceita uma integração e cada Phone Number ID identifica uma única empresa. Nenhum token é solicitado ou exibido no Admin.

### 7. Confira a verificação GET

A própria Meta executa a verificação ao salvar o callback. Para um diagnóstico manual:

```powershell
$token = "MESMO_VALOR_DE_META_VERIFY_TOKEN"
$url = "https://DOMINIO_ATUAL.ngrok-free.app/webhooks/whatsapp/?hub.mode=subscribe&hub.verify_token=$token&hub.challenge=12345"
Invoke-WebRequest $url
```

Uma configuração válida retorna `12345`. Token incorreto retorna HTTP 403.

### 8. Teste a configuração sem enviar mensagem

Abra **Configurações** no painel e use **Testar integração**. A ação consulta os metadados do Phone Number ID na Graph API e não envia mensagem para clientes.

### 9. Envie uma mensagem de teste

Use um celular permitido como destinatário/remetente de teste no ambiente configurado pela Meta e envie uma mensagem ao número de teste do WhatsApp.

O fluxo esperado é:

```text
WhatsApp → Meta Cloud API → ngrok → /webhooks/whatsapp/
→ assinatura validada → Phone Number ID → EmpresaCliente
```

O sistema identifica a empresa pelo Phone Number ID, cria ou reutiliza o contato e o atendimento e persiste a mensagem recebida.
Quando a mensagem recebida é um texto novo, existe fluxo configurado e a automação do atendimento está ativa, o sistema envia a saudação e o menu desse fluxo pela Cloud API. A resposta aceita pela Meta é persistida como mensagem de saída.

### 10. Confirme o recebimento

Há duas formas:

- No terminal do Django, procure `whatsapp.message.received`, `company_id`, `phone_number_id`, `message_id` e `type`.
- Em **Configurações**, confira se **Última comunicação** foi atualizada.
- No Django Admin, consulte **Contatos**, **Atendimentos** e **Mensagens**.

O conteúdo completo da mensagem não é escrito nos logs.

### Teste manual de saída

O comando abaixo envia uma mensagem real somente para o contato já associado ao atendimento informado. Ele exige uma mensagem inbound recebida nas últimas 24 horas e confirmação explícita:

```powershell
python manage.py whatsapp_send_test --atendimento ID_DO_ATENDIMENTO --confirm
```

Texto controlado opcional:

```powershell
python manage.py whatsapp_send_test --atendimento ID_DO_ATENDIMENTO --mensagem "Teste controlado do ZapFluxo." --confirm
```

O destinatário e o Phone Number ID não podem ser informados pelo terminal: ambos são obtidos das relações multiempresa do atendimento. O comando não mostra o Access Token.

## Mensagens e status

Mensagens outbound começam como `accepted` após a Meta retornar um ID. Eventos posteriores atualizam o registro para:

```text
sent → delivered → read
```

O status não regride. Falhas são registradas como `failed`, armazenando somente o código técnico sanitizado. A tela administrativa permite consultar direção, tipo, status e horário.

### Erros comuns

- **403 na verificação GET:** `META_VERIFY_TOKEN` diferente do informado na Meta.
- **403 no POST:** `META_APP_SECRET` incorreto ou assinatura ausente.
- **503 no POST:** `META_APP_SECRET` não foi configurado.
- **`integration.not_found`:** Phone Number ID recebido não está cadastrado ou a integração está inativa.
- **Host inválido:** domínio do túnel ausente em `ALLOWED_HOSTS`.
- **Falha de origem/CSRF no painel:** URL HTTPS ausente em `CSRF_TRUSTED_ORIGINS`.
- **Teste da integração falhou:** confira `META_ACCESS_TOKEN`, Phone Number ID e versão da Graph API.
- **Ngrok mudou:** atualize `PUBLIC_BASE_URL`, `ALLOWED_HOSTS` e `CSRF_TRUSTED_ORIGINS`, depois reinicie o servidor.

## Dados de demonstração

```powershell
python manage.py seed_demo
```

Credenciais padrão: usuário `demo`, senha `demo12345`. O comando é idempotente.

## Testes

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

O endpoint `GET /health/` verifica a aplicação e executa uma consulta mínima no
banco. Em produção, uma resposta saudável deve informar
`database_engine: postgresql`.

As regras de segredos, rotação, logs, auditoria e resposta a incidentes estão em
[`docs/SECURITY.md`](docs/SECURITY.md).

A fundação da integração com IA está descrita em
[`docs/AI_ARCHITECTURE.md`](docs/AI_ARCHITECTURE.md). Nesta etapa ela não está
ligada automaticamente ao webhook.

## Limites atuais

- O webhook valida, identifica a empresa e persiste mensagens recebidas.
- Mensagens duplicadas são descartadas pelo ID externo fornecido pela Meta.
- Tipos não textuais são registrados sem download do arquivo.
- A resposta automática atual é somente a saudação e o menu inicial do fluxo.
- Ainda não existe motor conversacional por etapas.
- O envio manual legado por `wa.me` permanece disponível durante a transição.
- APIs não oficiais não fazem parte da arquitetura.
