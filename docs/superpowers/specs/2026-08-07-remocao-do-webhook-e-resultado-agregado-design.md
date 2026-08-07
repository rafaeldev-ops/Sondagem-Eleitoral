# Remoção do webhook (n8n) e exportação do resultado consolidado

Data: 2026-08-07

Supersede a parte de webhook das specs anteriores. As de
`numero-socio-e-webhook-generico` e `titular-do-grupo-e-redesign` seguem
válidas como registro histórico das decisões da época — só não descrevem
mais o código atual no que toca a `WebhookPayload` e `webhook_logs`.

## Por que remover

A pergunta que originou isto: valia montar o fluxo n8n para entregar os dados
ao contratante, tendo exportação CSV/Excel disponível?

Comparação campo a campo: o export é **superconjunto** do payload do webhook.

| WebhookPayload | Export CSV/Excel |
|---|---|
| nome, numero_socio, cpf, telefone, titular | idem, mais a coluna ID |
| candidatos, preferido | idem |
| departamentos, departamento_outros | Modalidades, Outros (descrição) |
| aceite_lgpd, data | LGPD, Data |

O webhook só se pagaria por entrega em tempo real, automação sem intervenção
humana, ou alimentação de outro sistema. Nenhuma das três se aplicava.

Enquanto isso ele custava: com `WEBHOOK_URL` vazia, `send_webhook` desistia
antes de tentar e **todo voto** gravava uma linha `failed` em `webhook_logs`,
que o worker de background reprocessava a cada 60s até `WEBHOOK_RETRY_MAX`.
Uma fila sem consumidor.

## O que saiu

Código: `app/integrations/webhook.py`, `WebhookPayload`, `WebhookLog`,
`WebhookLogRepository`, `SurveyService._enqueue_webhook`,
`SurveyService.retry_pending_webhook`, o worker de background em
`app/main.py`, a rota `POST /admin/webhook/retry` e o botão "Reprocessar
Webhook" do painel.

Configuração: `WEBHOOK_URL`, `WEBHOOK_TOKEN`, `WEBHOOK_RETRY_MAX`,
`WEBHOOK_RETRY_DELAY_SECONDS` — de `Settings`, `.env`, `.env.example` e
`tests/conftest.py`.

Documentação: `docs/WEBHOOK.md` apagado; referências limpas em `README.md`,
`docs/API.md`, `DEPLOY.md`, `INSTALACAO.md` e `PRODUCAO.md`.

### O que NÃO saiu, de propósito

As migrations `001`, `002`, `003` e `005` continuam citando `pipefy_logs` e
`webhook_logs`. Migration aplicada não se reescreve: elas descrevem o que o
banco fez naquele ponto da história, e editá-las quebraria qualquer base que
esteja numa revisão intermediária. A remoção é a `008`.

As specs e planos antigos em `docs/superpowers/` também ficam. São registro
datado de decisões tomadas; apagá-los perderia o *porquê* sem ganhar nada.

## Migration 008

`DROP TABLE webhook_logs`.

**Apaga dados.** As linhas guardavam o payload de cada voto em JSON — nome,
CPF, telefone, em quem votou. É duplicata: `associados`, `respostas`,
`preferencias` e `associado_departamentos` seguem intactas e as exportações
continuam completas. Nenhuma resposta de sócio se perde. Quem quiser o
histórico bruto deve fazer `pg_dump -t webhook_logs` **antes** do upgrade.

O `downgrade` recria tabela, FK e os dois índices (incluindo o parcial
`ix_webhook_logs_status_pending`, com o mesmo `WHERE status IN ('pending',
'failed')`), mas devolve a tabela vazia.

## Exportação do resultado consolidado

Duas rotas novas, ambas autenticadas:

- `GET /api/admin/export/resultados/csv`
- `GET /api/admin/export/resultados/excel`

Colunas: `Candidato`, `Apelido`, `Votos`, `% dos Respondentes`, `Ponto Focal`.
Ordenado do mais votado para o menos votado.

### Por que existe

O aviso de privacidade do formulário diz ao sócio que os dados são usados
"exclusivamente para validação de segurança e prevenção contra duplicidade".
Quem só precisa do **resultado** — quem lidera, quantos votos cada um teve —
não precisa de CPF nem telefone, e entregar o export nominal para fora do
clube vai além do que foi informado.

Não é substituto do export nominal: quem precisa do dado individual continua
tendo `export_csv`/`export_excel`. É uma segunda saída, com o painel deixando
explícito qual contém dado pessoal e qual não contém.

### Duas subconsultas correlacionadas, não dois JOIN

`CandidatoRepository.resultado_agregado()` conta votos e pontos focais com
subconsultas correlacionadas. Com dois `LEFT JOIN` no mesmo `SELECT`, cada
linha de `respostas` se multiplicaria por cada linha de `preferencias` do
mesmo candidato e os dois números sairiam inflados — silenciosamente, porque
o resultado continua parecendo plausível.

Candidato sem voto aparece com zero, em vez de sumir: sumir da lista e ter
zero voto são coisas diferentes para quem lê o arquivo.

Percentual tem guarda de divisão por zero — o relatório pode ser baixado
antes do primeiro voto.

## Testes

`tests/integration/test_resultado_agregado.py`:

- soma de votos e de pontos focais por candidato, com percentual
- ordenação por votos (não alfabética)
- candidato sem voto aparece com zero
- sondagem sem resposta nenhuma não estoura na divisão
- **o CSV não contém nome, CPF, telefone nem número de sócio** — a
  propriedade que justifica o arquivo existir
- as duas rotas exigem autenticação

Alterados:

- `test_admin_auth.py` — `TestRotasDeWebhookForamRemovidas` garante 404 nas
  duas grafias históricas da rota; se alguma voltar, a remoção foi revertida
  pela metade
- `test_admin_cookie_auth.py` — a cobertura de CSRF usava
  `POST /admin/webhook/retry` como POST representativo; passou a usar
  `POST /admin/candidatos`, que também é escrita autenticada
- `test_exportacoes.py` e `test_titular.py` — classes `TestPayloadDoWebhook`
  removidas

## Fora de escopo

- Anonimizar o export nominal existente
- Remover CPFs do banco após a sondagem (já consta como recomendação em
  `docs/PRODUCAO.md`)
- Ajustar o texto do aviso de privacidade do formulário — decisão de quem
  responde pelo clube, não desta branch
