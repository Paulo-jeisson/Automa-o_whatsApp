# Migração visual do WhatsApp para Evolution API

## Provider ativo e visível

A interface do IAATENDE 2.0 utiliza somente a sessão `WhatsAppSession`, isolada por empresa e operada pela Evolution API. Configurações mostra apenas estado, número conectado, nome da instância, última conexão e o link **Gerenciar WhatsApp** para o fluxo de QR Code.

## Elementos removidos da interface

- Phone Number ID e WABA ID;
- token, status e configuração manual da Meta;
- Embedded Signup;
- teste, reconexão e desconexão vinculados à Meta;
- seleção ou fallback visível de provider.

## Compatibilidade temporária

`WhatsAppIntegration`, migrações históricas, o cliente Cloud API, webhook legado e Embedded Signup permanecem no backend para preservar dados instalados e compatibilidade de integrações existentes. Eles não são apresentados nem usados pelo novo fluxo visual. A remoção definitiva exige migração de dados separada e confirmação de que nenhuma instalação ainda recebe eventos legados.

## Isolamento

Toda sessão, prompt, rascunho e versão é consultada a partir da empresa autenticada. IDs de versões também são validados junto com `profile__empresa`, impedindo acesso cruzado por manipulação de URL.
