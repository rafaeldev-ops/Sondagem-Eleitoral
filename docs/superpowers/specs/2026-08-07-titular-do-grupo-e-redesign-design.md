# Declaração de titular do grupo + redesenho do fluxo público

Data: 2026-08-07

## Objetivo

Duas coisas pedidas juntas, que se tocam só no cabeçalho e na etapa 1:

1. Passar a perguntar, no cadastro, se o respondente é **titular do grupo
   familiar de sócios** ou dependente — e levar essa resposta até o webhook e
   as exportações.
2. Redesenhar a aparência do fluxo público: a barra de progresso vira **cinco
   bolinhas**, uma por etapa, que ganham um check ao serem confirmadas; e o
   cabeçalho ganha **"Sondagem 2026"** em tipografia serif acima do wordmark
   do clube.

## Decisões tomadas

| Questão | Decisão |
|---|---|
| O que "titular" significa | Sócio titular do grupo familiar, vs. dependente |
| Quem não é titular | Responde normalmente — a resposta é registrada, ninguém é bloqueado |
| Onde o campo aparece | Etapa 1 (Cadastro), logo abaixo do número de sócio |
| Tipografia do "Sondagem 2026" | Pilha serif de sistema (Georgia / Iowan / Palatino / serif) |
| Framework do redesenho | Continua Bootstrap 5.3; `style.css` vira camada de tema |

A opção de bloquear dependentes foi descartada explicitamente: mudaria a
regra de quem pode votar, o que é decisão da comissão e não desta branch.

Fontes externas (Google Fonts, self-host de `.woff2`) foram descartadas para
não abrir `font-src`/`style-src` na CSP nem versionar binário. A serif de
sistema entrega o contraste de estilo pedido sem custo de rede.

## Modelo de dados

`associados.titular BOOLEAN NULL` (migration `007`).

### Por que nullable, e não `NOT NULL DEFAULT false`

A coluna tem **três** estados significativos:

- `TRUE` — declarou que é titular
- `FALSE` — declarou que **não** é
- `NULL` — respondeu antes da pergunta existir

Um `server_default=false` na migration transformaria todo voto já coletado em
"não é titular" — uma resposta que ninguém deu, indistinguível de uma resposta
real na hora de contar titulares na planilha. `NULL` mantém a distinção.

A partir da 007 nenhuma linha nova nasce `NULL`: `CadastroRequest.titular` é
obrigatório, então o backend recusa (422) cadastro que não informe o campo.

Como a 006 e ao contrário da 004, a migration roda com a sondagem no ar: a
coluna é nullable e não há backfill.

## Fluxo do dado

```
etapa 1 (checkbox)
  -> POST /api/survey/register   { titular: bool }   CadastroRequest.titular
  -> sessão OTP no Redis          session["titular"]
  -> POST /api/survey/submit      (não reenvia o campo)
  -> Associado.titular
  -> WebhookPayload.titular  +  colunas "Titular" no CSV/Excel
```

O valor viaja pela sessão, como `nome`/`cpf`/`numero_socio`/`telefone` — o
submit não o reenvia, então não há como divergir do que foi declarado antes
do OTP.

`submit_vote` lê `session.get("titular")`, não `session["titular"]`: sessões
abertas antes do deploy não têm a chave, e um `KeyError` derrubaria o submit
de quem estava no meio do fluxo. Nesse caso o valor é `None` — que é a
resposta honesta, já que a pessoa não chegou a ver o checkbox.

### `WebhookPayload.titular` tem default, obrigatoriamente

`titular: bool = False`. O schema serve a dois sentidos: sai para o n8n e
**volta** do banco em `retry_pending_webhook`, que re-hidrata payloads
gravados em `webhook_logs`. Campo novo sem default levanta `ValidationError`
antes de `log.tentativas += 1`, e uma única linha antiga trava o worker de
retry para sempre. Já aconteceu duas vezes nesta base de código
(`numero_socio`, depois `departamentos`/`departamento_outros`).

O `False` na saída vem de `bool(associado.titular)`: o payload do n8n não usa
nulo em campo nenhum (mesma razão do `departamento_outros=""`). A distinção
entre "não é titular" e "não foi perguntado" fica preservada no banco e nas
exportações, que são a fonte de verdade da análise.

## Frontend

### Checkbox

Cartão com alvo de toque de campo inteiro (`.choice-card`), não checkbox de
16px. Texto: **"Sou titular do grupo"**, com a dica "Deixe desmarcado se você
é dependente no grupo familiar" — desmarcado é uma resposta, e a interface
precisa dizer isso, já que um checkbox sozinho não distingue "respondi que
não" de "não vi o campo".

O destaque do cartão marcado vem de `.selected` aplicada pelo `app.js`, com
`:has(input:checked)` só como reforço: WebView de Android antigo, comum no
público do clube, não implementa `:has()`.

### Bolinhas das etapas

`<ol class="step-dots">` com cinco `<li data-step="N">`. Ficam **dentro do
cartão branco** (`.flow-shell`), não no cabeçalho vermelho: no cabeçalho, a
bolinha preenchida some contra o fundo e só o contorno restava para indicar
estado. Três estados, todos por classe:

- **futura** — círculo cinza-claro, número apagado
- **atual** (`.is-current`) — a que está esperando ser confirmada: preenchida
  de vermelho, número em branco, halo em volta
- **confirmada** (`.is-done`) — preenchida de vermelho, com check

O check é desenhado com borda rotacionada, não com o glifo `✓`: o caractere
varia demais entre as fontes de sistema de Android e iOS para cair
centralizado num círculo de 44px.

O conector fica em `::before` de cada bolinha (menos a primeira), então a
linha "preenche" junto com o avanço sem elemento próprio no HTML.

`renderStepDots(n)` roda em `goToStep()` e uma vez na carga. Na tela de
agradecimento (`n = 6`) as cinco ficam confirmadas e nenhuma é a atual.

### CSP

O estado das bolinhas é **classe**, nunca `element.style`. A barra anterior
ajustava `style.width`, o que escapava da CSP por ser CSSOM — classe remove a
dependência dessa distinção. Nenhum host novo entra na política.

`test_templates_nao_tem_atributo_style` varre o HTML renderizado atrás do
atributo de estilo embutido, **inclusive dentro de comentários** — que também
são servidos ao navegador. Comentário de template não pode citar o atributo
literalmente.

### Cabeçalho

```
   ⛊ SONDAGEM 2026     <- sobrancelha: pequena, tracking largo, peso leve
   SEMPRE TRICOLOR     <- wordmark: grande, peso 800, tracking negativo
```

A primeira versão invertia isso — "Sondagem 2026" grande em serif, o clube
pequeno embaixo — e os dois títulos competiam sem que nenhum mandasse. O
contraste entre sobrancelha discreta e wordmark pesado é o que dá o ar de
marca; a hierarquia importa mais aqui do que a escolha da família tipográfica.

`{{ app_name }}` continua vindo do contexto, agora no `<h1>`. O ícone de
escudo é SVG inline — nenhum host novo na CSP e nada a baixar antes da
primeira pintura.

### Candidatos

O quadro cinza escrito "FOTO" virou avatar redondo com as iniciais do nome,
em seis cores fixas escolhidas por `id % 6` — o mesmo candidato tem sempre a
mesma cor, então ela serve de referência para achar um nome numa lista longa.
A cor é **classe** (`.c0`…`.c5`), nunca atributo de estilo montado no
`innerHTML`: a CSP bloqueia atributo de estilo venha ele do servidor ou do JS.

### Tema

`style.css` reescrito como camada sobre o Bootstrap: tokens de cor/raio/
tipografia em `:root`, e só o que precisa de identidade própria é
redefinido. Grid, utilitários, validação (`.is-invalid`/`.valid-feedback`) e
o toast ficam intocados — é o que permite o `app.js` seguir sem reescrita.

## Saídas

CSV e Excel ganham a coluna **"Titular"**, entre "Telefone" e "Candidatos",
via `_rotulo_titular()`: `"Sim"` / `"Não"` / `""` (vazio para `NULL`).

Três estados e não dois, pelo mesmo motivo da coluna ser nullable: quem for
contar titulares precisa separar "declarou que não é" de "nunca foi
perguntado".

## Testes

`tests/integration/test_titular.py`:

- cadastro sem o campo → 422
- `true` e `false` chegam ao banco como `True`/`False` (nunca `None`)
- payload do webhook carrega `titular`, e a chave está sempre presente
- CSV: `"Sim"`, `"Não"`, e `""` para linha com `titular IS NULL`
- o checkbox está no template

Extensões em arquivos existentes:

- `test_exportacoes.py` — a regressão de desserialização histórica passa a
  cobrir `titular`, incluindo a forma "já tem modalidades, ainda não tem
  titular", que é a que está sendo gravada em produção agora
- `test_paginas_html.py` — cinco bolinhas presentes; "Sondagem 2026" acima do
  wordmark (ordem no HTML)
- 15 chamadas a `/api/survey/register` na suíte passaram a enviar `titular`

## Fora de escopo

- Bloquear dependentes de responder
- Conferir a declaração contra o cadastro do clube (é auto-declaração)
- Redesenho de `/admin` — usa `admin.css`, arquivo separado, intocado
- Regerar `docs/screenshots-preview/*.png`, que agora mostram o visual antigo
