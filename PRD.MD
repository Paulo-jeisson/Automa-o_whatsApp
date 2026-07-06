Crie um sistema SaaS simples de automação de atendimento via WhatsApp para pequenos negócios, começando por estacionamentos.

Objetivo:
O sistema deve atender clientes automaticamente pelo WhatsApp, coletar informações básicas e avisar o dono do estacionamento pelo WhatsApp quando houver um novo atendimento.

Importante:
Não integrar com sistema interno do cliente. O sistema deve funcionar de forma independente, com painel próprio simples.

Tecnologias sugeridas:

* Backend: Python com Django
* Frontend: HTML, CSS, Bootstrap
* Banco: SQLite no desenvolvimento
* Integração WhatsApp: usar link wa.me inicialmente ou preparar estrutura para API externa como Z-API/Evolution API
* Painel administrativo simples

Funcionalidades principais:

1. Cadastro do negócio
   Criar modelo para Estacionamento com:

* nome do estacionamento
* nome do dono
* telefone WhatsApp do dono
* endereço
* horário de funcionamento
* mensagem inicial personalizada
* status ativo/inativo

2. Fluxo de atendimento
   Criar um fluxo simples para o cliente final:

* Saudação automática
* Perguntar o que deseja:

  1. Saber preço
  2. Ver disponibilidade de vaga
  3. Falar com atendente
  4. Informar entrada de veículo

3. Coleta de dados
   Quando o cliente escolher uma opção, coletar:

* nome do cliente
* telefone
* placa do veículo, quando fizer sentido
* tipo de veículo: carro, moto ou caminhonete
* mensagem/necessidade

4. Aviso para o dono
   Após finalizar o atendimento, gerar uma mensagem pronta para o WhatsApp do dono do estacionamento com os dados do cliente.

Exemplo da mensagem:
“Novo atendimento recebido:
Cliente: João
Telefone: 88999999999
Opção: Saber preço
Veículo: Carro
Placa: ABC-1234
Mensagem: Quer saber valor da diária.”

Criar botão no painel para enviar/abrir essa mensagem no WhatsApp do dono usando link wa.me.

5. Painel simples
   Criar um painel onde o dono veja:

* total de atendimentos
* atendimentos do dia
* últimos atendimentos recebidos
* status: novo, em andamento, finalizado
* opção escolhida pelo cliente
* botão “Avisar no WhatsApp”
* botão “Marcar como finalizado”

6. Tela de fluxo
   Criar uma tela visual simples mostrando o fluxo:
   Entrada do cliente → Menu automático → Coleta de dados → Aviso ao dono → Finalização.

7. Segurança básica

* Login para o dono acessar o painel
* Cada dono só vê seus próprios atendimentos
* Validações básicas nos formulários

8. Design
   Criar interface simples, limpa e responsiva, pensando em uso pelo celular.

9. Entrega esperada
   Entregar:

* models.py
* views.py
* urls.py
* templates
* forms.py se necessário
* painel funcionando
* fluxo de atendimento simulado via página web
* botão de aviso pelo WhatsApp do dono

Não criar integração complexa ainda. Primeiro criar MVP funcional, simples e vendável.


```xml
<prd>
  <project>
    <name>ZapFluxo</name>
    <description>
      SaaS simples de automação de atendimento via WhatsApp para negócios locais,
      sem integração direta com o sistema interno do cliente.
    </description>
    <target_segments>
      <segment>Clínicas</segment>
      <segment>Escritórios de advocacia</segment>
      <segment>Estacionamentos</segment>
      <segment>Contabilidades</segment>
      <segment>Assistências técnicas</segment>
      <segment>Pequenos comércios</segment>
    </target_segments>
  </project>

  <objective>
    Criar um MVP vendável onde o dono do negócio tenha um painel simples,
    visualize atendimentos recebidos, acompanhe o fluxo e receba aviso no WhatsApp.
  </objective>

  <rules>
    <rule>Não integrar com sistema interno do cliente no MVP.</rule>
    <rule>O sistema deve funcionar de forma independente.</rule>
    <rule>O painel deve ser simples, responsivo e fácil de usar pelo celular.</rule>
    <rule>O fluxo de atendimento deve ser personalizável por tipo de negócio.</rule>
    <rule>No MVP, usar link wa.me para aviso ao dono.</rule>
  </rules>

  <sprints>

    <sprint number="1" name="Base do SaaS e Autenticação">
      <goal>Criar a estrutura inicial do sistema com login e separação por empresa.</goal>
      <tasks>
        <task>Criar projeto Django.</task>
        <task>Criar app principal.</task>
        <task>Criar modelo EmpresaCliente.</task>
        <task>Criar login/logout.</task>
        <task>Criar dashboard inicial.</task>
        <task>Garantir que cada usuário veja apenas sua própria empresa.</task>
      </tasks>
      <deliverable>Sistema base com login e painel inicial funcionando.</deliverable>
    </sprint>

    <sprint number="2" name="Cadastro do Negócio">
      <goal>Permitir que o dono configure os dados básicos do negócio.</goal>
      <tasks>
        <task>Criar formulário de cadastro/edição da empresa.</task>
        <task>Campos: nome, segmento, dono, WhatsApp, endereço, horário e mensagem inicial.</task>
        <task>Criar tela Minha Empresa.</task>
        <task>Validar telefone do WhatsApp.</task>
      </tasks>
      <deliverable>Dono consegue configurar os dados do negócio.</deliverable>
    </sprint>

    <sprint number="3" name="Fluxo de Atendimento Personalizável">
      <goal>Criar fluxo simples de atendimento por segmento.</goal>
      <tasks>
        <task>Criar modelo FluxoAtendimento.</task>
        <task>Criar opções padrão por segmento.</task>
        <task>Permitir editar perguntas e opções.</task>
        <task>Criar tela visual mostrando o fluxo.</task>
      </tasks>
      <deliverable>Fluxo visual e editável no painel.</deliverable>
    </sprint>

    <sprint number="4" name="Simulador de Atendimento">
      <goal>Criar uma página que simule o atendimento do cliente final.</goal>
      <tasks>
        <task>Criar link público por empresa.</task>
        <task>Exibir saudação automática.</task>
        <task>Mostrar opções do fluxo.</task>
        <task>Coletar nome, telefone, necessidade e observação.</task>
        <task>Salvar atendimento no banco.</task>
      </tasks>
      <deliverable>Atendimento simulado funcionando sem WhatsApp real ainda.</deliverable>
    </sprint>

    <sprint number="5" name="Painel de Atendimentos">
      <goal>Permitir que o dono veja e acompanhe todos os atendimentos.</goal>
      <tasks>
        <task>Listar atendimentos recebidos.</task>
        <task>Criar status: novo, em andamento e finalizado.</task>
        <task>Criar filtros por data, status e segmento.</task>
        <task>Criar cards com total do dia e total geral.</task>
      </tasks>
      <deliverable>Painel operacional simples para o dono.</deliverable>
    </sprint>

    <sprint number="6" name="Aviso no WhatsApp do Dono">
      <goal>Enviar os dados do atendimento para o WhatsApp do dono usando wa.me.</goal>
      <tasks>
        <task>Gerar mensagem automática com dados do atendimento.</task>
        <task>Criar botão Avisar no WhatsApp.</task>
        <task>Abrir link wa.me com mensagem pronta.</task>
        <task>Registrar data/hora do aviso.</task>
      </tasks>
      <deliverable>Dono recebe aviso do atendimento via WhatsApp.</deliverable>
    </sprint>

    <sprint number="7" name="Templates por Segmento">
      <goal>Deixar o sistema vendável para vários tipos de negócio.</goal>
      <tasks>
        <task>Criar template para clínicas.</task>
        <task>Criar template para advocacia.</task>
        <task>Criar template para estacionamentos.</task>
        <task>Criar template para contabilidade.</task>
        <task>Permitir escolher segmento no cadastro.</task>
      </tasks>
      <deliverable>Sistema adaptável para diferentes clientes.</deliverable>
    </sprint>

    <sprint number="8" name="Landing Page Comercial">
      <goal>Criar página pública para vender o serviço.</goal>
      <tasks>
        <task>Criar home explicando o produto.</task>
        <task>Criar seção de benefícios.</task>
        <task>Criar seção de segmentos atendidos.</task>
        <task>Criar planos e preços.</task>
        <task>Criar botão Falar no WhatsApp.</task>
      </tasks>
      <deliverable>Site comercial pronto para captação de clientes.</deliverable>
    </sprint>

    <sprint number="9" name="Preparação para API WhatsApp">
      <goal>Preparar arquitetura para futura integração oficial.</goal>
      <tasks>
        <task>Criar camada services/whatsapp.py.</task>
        <task>Separar envio de mensagem da regra de negócio.</task>
        <task>Criar configuração para provider: wa.me, Z-API, Evolution API ou oficial.</task>
        <task>Manter wa.me como provider padrão no MVP.</task>
      </tasks>
      <deliverable>Sistema pronto para evoluir sem refatoração pesada.</deliverable>
    </sprint>

    <sprint number="10" name="Ajustes Finais e MVP Vendável">
      <goal>Polir o sistema para demonstração e venda.</goal>
      <tasks>
        <task>Melhorar layout mobile.</task>
        <task>Criar dados de demonstração.</task>
        <task>Adicionar mensagens amigáveis.</task>
        <task>Testar fluxo completo.</task>
        <task>Criar README com instruções.</task>
      </tasks>
      <deliverable>MVP pronto para apresentar para primeiros clientes.</deliverable>
    </sprint>

  </sprints>

  <mvp_scope>
    <included>
      <item>Login do dono</item>
      <item>Cadastro da empresa</item>
      <item>Fluxo visual de atendimento</item>
      <item>Simulador de atendimento</item>
      <item>Painel de atendimentos</item>
      <item>Aviso via WhatsApp usando wa.me</item>
      <item>Templates por segmento</item>
      <item>Landing page comercial</item>
    </included>
    <not_included>
      <item>Integração com sistema interno do cliente</item>
      <item>WhatsApp API oficial no primeiro MVP</item>
      <item>Chatbot com inteligência artificial avançada</item>
      <item>Cobrança automática recorrente</item>
    </not_included>
  </mvp_scope>
</prd>
```
