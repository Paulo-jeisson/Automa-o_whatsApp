# Filas e observabilidade

O webhook apenas persiste jobs. Execute o processamento exclusivamente com
`python manage.py process_jobs --queue whatsapp --limit 500`.

Em desenvolvimento, o SQLite usa `timeout=20`, `busy_timeout` e WAL para reduzir
contenção entre o Django e o worker. Essas medidas não tornam o SQLite adequado
para produção: **PostgreSQL continua obrigatório em produção**.

O webhook salva a mensagem e
cria um `AsyncJob` idempotente; o worker executa respostas automáticas fora da
requisição. Falhas usam backoff exponencial e, após o limite, ficam com status
`DEAD` para inspeção.

Instale e ative os timers:

```sh
sudo systemctl disable --now iaatende-worker.timer 2>/dev/null || true
sudo systemctl enable --now iaatende-worker.service
sudo systemctl enable --now iaatende-whatsapp-sync.timer
sudo systemctl enable --now iaatende-monitor.timer
```

Comandos manuais:

```sh
python manage.py process_jobs --queue whatsapp --limit 500
python manage.py check_operations
```

O monitor registra alertas persistentes para banco indisponível, fila atrasada,
dead-letter, rejeições consecutivas da Meta e falhas consecutivas de IA. Defina
`OPERATIONAL_ALERT_WEBHOOK` para entregar JSON a Slack, Teams ou ao serviço de
plantão; sem ele os alertas continuam no banco e nos logs.

As métricas operacionais registram eventos/latência do webhook e falhas de IA
com associação opcional à empresa. Retenção e agregação devem ser configuradas
de acordo com o volume do ambiente.
