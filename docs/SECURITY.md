# Operação de segurança

## Segredos

Segredos devem existir somente no gerenciador de segredos ou no arquivo de
ambiente do servidor com permissão `0600`. Nunca os inclua em commits, tickets,
logs, screenshots ou mensagens.

## Rotação

### `DJANGO_SECRET_KEY`

Trocar a chave invalida sessões, tokens de recuperação de senha e outros valores
assinados pelo Django.

1. anuncie uma janela curta;
2. gere uma chave aleatória longa no cofre;
3. substitua `DJANGO_SECRET_KEY`;
4. reinicie todos os workers;
5. valide login, CSRF e `/health/`;
6. remova a chave anterior do ambiente.

### `WHATSAPP_TOKEN_ENCRYPTION_KEY`

Esta chave protege tokens persistidos e não pode ser simplesmente substituída:
os tokens existentes ficariam ilegíveis.

1. faça backup restaurável do banco;
2. desconecte/reconecte cada integração, ou implemente uma rotina de
   recriptografia que aceite chave antiga e nova;
3. confirme o teste da integração por empresa;
4. somente depois remova a chave antiga.

Até existir rotação com versionamento de chaves, trate perda dessa chave como
necessidade de reconectar todos os clientes.

### Tokens e segredo da Meta

1. gere/revogque a credencial no painel oficial da Meta;
2. atualize o cofre e reinicie os workers;
3. valide Embedded Signup, assinatura do webhook e envio controlado;
4. confirme que a credencial anterior foi revogada.

Alterar `META_APP_SECRET` também exige atualizar a validação da assinatura do
webhook. Tokens por empresa devem ser reconectados pelo Embedded Signup quando
revogados.

### PostgreSQL, SMTP e serviços externos

1. crie a nova credencial mantendo a anterior temporariamente;
2. atualize o ambiente;
3. reinicie e valide `/health/` ou o envio de e-mail;
4. revogue a credencial antiga;
5. registre a ação na ferramenta operacional, sem copiar o segredo.

## Logs e auditoria

Logs do WhatsApp registram IDs técnicos, status, códigos de erro e IDs internos.
Não devem registrar:

- tokens ou chaves;
- corpo completo do webhook;
- texto de conversas;
- senhas;
- dados de cartão;
- cabeçalhos de autorização.

Eventos administrativos ficam em `AuditEvent`. O IP é armazenado somente como
hash associado à chave da instalação. Os registros são somente leitura no
Django Admin.

## Sessões e abuso

Produção expira a sessão após uma hora de inatividade e renova o prazo durante
uso ativo. Login, webhook, formulário público e operações sensíveis possuem
limitação persistida no banco, compartilhada por todos os workers.

O Gunicorn deve permanecer acessível apenas pelo socket Unix atrás do Nginx.
Assim, o cabeçalho `X-Forwarded-For` usado em produção é controlado pelo proxy.

## Resposta a incidente

1. preserve evidências e horários;
2. limite o acesso afetado;
3. revogue credenciais comprometidas;
4. restaure serviço por credenciais novas;
5. confira auditoria e logs sanitizados;
6. avalie obrigações contratuais e LGPD;
7. documente causa, impacto e prevenção.
