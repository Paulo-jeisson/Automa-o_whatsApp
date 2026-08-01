# Métricas e custos da IA

O dashboard `/metricas/ia/` agrega por empresa atendimentos, automação,
transferências, agendamentos, mensagens, chamadas, erros, tools, tokens,
latência e custo estimado.

Configure preços por milhão de tokens conforme o modelo contratado:

```env
AI_INPUT_COST_PER_MILLION=0
AI_OUTPUT_COST_PER_MILLION=0
```

Valores padrão são zero para impedir estimativas fictícias. Atualize-os quando
o modelo ou a tabela comercial mudar. O custo é estimativo e não substitui a
fatura do provedor.
