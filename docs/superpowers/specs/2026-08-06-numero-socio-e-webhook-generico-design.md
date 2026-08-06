# Número de sócio no cadastro + webhook genérico

Data: 2026-08-06

## Objetivo

Duas mudanças relacionadas à coleta e ao envio dos dados da sondagem:

1. **Número de sócio** — passa a ser exigido no cadastro (4 dígitos) e a aparecer em
   todas as saídas de dados: CSV, Excel, webhook e busca do admin.
2. **Webhook genérico** — a integração deixa de se chamar "Pipefy" em todos os
   níveis (env, código, tabela, rota, docs), porque o destino passa a ser o n8n e
   o `PipefyService` nunca teve nada de específico do Pipefy: sempre foi um POST
   de JSON com Bearer token opcional.

## Decisões tomadas

| Questão | Decisão |
|---|---|
| Número de sócio é único? | Sim — constraint `UNIQUE`, mesma proteção que o CPF tem |
| Validar contra lista oficial de sócios? | Não — valida só formato (4 dígitos) e unicidade |
| Checagem em tempo real (endpoint de consulta)? | Não — só no envio |
| Banco já tem votos? | Não — a migration pode criar a coluna `NOT NULL` direto |
| Escopo da renomeação | Completo: env + código + tabela + rota + docs |

A decisão de **não** expor endpoint de consulta é de segurança: um número de 4
dígitos tem só 10.000 combinações. Um endpoint de "esse número já votou?" seria
enumerável por completo em 10 mil requisições, revelando exatamente quais sócios
participaram da sondagem. O CPF tem esse endpoint hoje (`/validate-cpf`), mas 11
dígitos com verificadores não são enumeráveis da mesma forma.

## Modelo de dados

Nova coluna em `Associado` (`app/models/__init__.py`), com a constraint única
**nomeada explicitamente** em `__table_args__`:

```python
class Associado(Base):
    __tablename__ = "associados"
    __table_args__ = (
        UniqueConstraint("numero_socio", name="uq_associados_numero_socio"),
    )

    numero_socio: Mapped[str] = mapped_column(String(4), nullable=False)
```

**Texto, não inteiro** — preserva zeros à esquerda. `0042` e `42` são números de
sócio diferentes, e um `Integer` colapsaria os dois.

**Constraint nomeada, não `unique=True` inline.** `Base.metadata` não tem
`naming_convention` (ver `app/database/base.py`), então um `unique=True` inline
sairia com nome automático do Postgres (`associados_numero_socio_key`) quando o
schema é criado por `Base.metadata.create_all` — que é justamente como os testes
criam o schema (`tests/conftest.py`, fixture `_schema`) — enquanto a migration
Alembic criaria `uq_associados_numero_socio`. Os dois ambientes ficariam com
nomes de constraint diferentes, e a lógica de mensagem de erro por constraint
(ver adiante) se comportaria de um jeito no teste e de outro em produção.
Nomear explicitamente elimina a divergência.

**Sem `index=True` separado.** Em Postgres a constraint `UNIQUE` já cria um índice
b-tree; um `create_index` adicional custaria escrita e disco sem ganho de leitura.
A coluna `cpf` vizinha tem essa redundância hoje (`UniqueConstraint` +
`ix_associados_cpf` em `001_initial_schema.py`), mas o problema não será propagado
para a coluna nova.

### Migration

Nova revisão `alembic/versions/003_add_numero_socio_and_rename_webhook.py`, com as
duas mudanças:

```python
op.add_column("associados", sa.Column("numero_socio", sa.String(4), nullable=False))
op.create_unique_constraint("uq_associados_numero_socio", "associados", ["numero_socio"])
op.rename_table("pipefy_logs", "webhook_logs")
```

**Pré-condição:** a tabela `associados` precisa estar vazia. Adicionar uma coluna
`NOT NULL` sem `server_default` falha se houver linhas. Isso é aceitável porque o
sistema ainda não coletou votos em produção; se isso mudar antes do deploy, a
migration precisa ser reescrita para criar a coluna nulável e fazer backfill.

O `downgrade()` reverte ambas: `rename_table` de volta e `drop_column`.

## Validação

Novo módulo `app/utils/socio.py`, seguindo o padrão de `app/utils/cpf.py`:

```python
import re

def normalize_numero_socio(valor: str) -> str:
    return re.sub(r"\D", "", valor)

def validate_numero_socio(valor: str) -> bool:
    return re.fullmatch(r"[0-9]{4}", valor) is not None
```

`re.fullmatch(r"[0-9]{4}", ...)` em vez de `str.isdigit()`: `isdigit()` retorna
`True` para dígitos unicode (`"٤".isdigit()` e `"²".isdigit()` são ambos `True`),
o que deixaria entrar no banco um "número de sócio" que não corresponde a nenhum
número real. O código existente usa `isdigit()` na validação do OTP
(`app/schemas/__init__.py`), mas esse padrão não será copiado aqui.

Em `CadastroRequest`:

```python
numero_socio: str

@field_validator("numero_socio")
@classmethod
def validate_numero_socio_field(cls, value: str) -> str:
    numero = normalize_numero_socio(value)
    if not validate_numero_socio(numero):
        raise ValueError("Número de sócio deve ter exatamente 4 dígitos")
    return numero
```

## Fluxo

O campo acompanha o fluxo já existente de cadastro:

```
POST /api/survey/register  (CadastroRequest com numero_socio)
        │
        ├── check CPF disponível        ──┐  ambos ANTES de enviar OTP
        ├── check numero_socio disponível ─┘
        │
        ├── cria sessão no Redis (numero_socio incluído no dict)
        └── envia OTP

POST /api/survey/verify-otp  → marca sessão como verified

POST /api/survey/submit
        └── lê numero_socio da sessão → grava em Associado
```

### Checagem de duplicidade em dois pontos

1. **Em `register_and_send_otp`**, junto com o `check_cpf_available` que já existe.
   Barrar aqui evita gastar um SMS num cadastro que vai ser rejeitado no final.
   Novo método `check_numero_socio_available` em `SurveyService`, e novo
   `get_by_numero_socio` em `AssociadoRepository`.

2. **Em `submit_vote`**, pela constraint do banco. A checagem do passo 1 é
   otimista — não há lock entre o SELECT e o INSERT, e entre os dois ainda passa
   todo o tempo do fluxo de OTP. A constraint é quem realmente garante unicidade.

### Mensagem de erro correta por constraint

Hoje `submit_vote` trata `IntegrityError` com uma mensagem fixa:

```python
except IntegrityError:
    await self.db.rollback()
    return False, "Este CPF já participou da sondagem"
```

Com duas constraints únicas na mesma tabela, essa mensagem passa a estar errada
metade das vezes. O tratamento passa a inspecionar qual constraint foi violada
via `str(exc.orig)` e devolver "Este número de sócio já participou da sondagem"
quando a violação for da constraint de número de sócio, mantendo a mensagem de
CPF no outro caso.

A detecção casa o **nome da coluna** (`"numero_socio" in str(exc.orig)`), não o
nome exato da constraint. O texto do asyncpg traz as duas informações — o nome
da constraint e uma linha `DETAIL: Key (numero_socio)=(...) already exists` — e
casar pela coluna continua funcionando mesmo que a constraint venha a ser
renomeada. A constraint nomeada explicitamente no modelo (ver acima) já garante
que teste e produção concordem; isso é redundância barata em cima disso.

**Esse texto não pode ir para log.** `str(exc.orig)` inclui o valor que violou a
constraint — no caso do CPF, o CPF completo em texto puro. O projeto trata isso
com cuidado em outros pontos (`mask_cpf` existe exatamente para isso). O texto
serve só para o `if`; não entra em `logger.*`.

## Frontend

`templates/index.html`: novo campo entre CPF e telefone.

```html
<label for="numeroSocio" class="form-label">Número de Sócio * (4 dígitos)</label>
<input type="text" class="form-control form-control-lg" id="numeroSocio"
       inputmode="numeric" required maxlength="4" autocomplete="off"
       placeholder="0000">
```

`static/js/app.js`: passa `numero_socio` no corpo do POST de `/register`, e
filtra não-dígitos no `input` (mesmo padrão dos listeners de CPF e telefone).

Sem validação remota em tempo real — a rejeição por duplicidade volta do
`/register`, que já é limitado a 10 req/min (`RATE_LIMIT_REGISTER`).

## Saídas

O número de sócio precisa aparecer nos quatro caminhos de saída:

| Saída | Onde | Mudança |
|---|---|---|
| CSV | `ExportService.export_csv` | coluna `Nº Sócio` após `ID` |
| Excel | `ExportService.export_excel` | mesma coluna, mesma posição |
| Webhook | `WebhookPayload` | campo `numero_socio` no JSON |
| Busca admin | `GET /api/admin/search` | campo no JSON + card em `admin.js` |

O `AssociadoResponse` em `app/schemas/__init__.py` também ganha o campo, para
manter o schema coerente com o que a rota devolve.

Payload final enviado ao n8n:

```json
{
  "nome": "...",
  "numero_socio": "0042",
  "cpf": "123.456.789-00",
  "telefone": "...",
  "candidatos": ["..."],
  "preferido": "...",
  "aceite_lgpd": true,
  "data": "2026-08-06T..."
}
```

## Renomeação Pipefy → Webhook

| Antes | Depois |
|---|---|
| `app/integrations/pipefy.py` | `app/integrations/webhook.py` |
| `PipefyService` | `WebhookService` |
| `PipefyPayload` | `WebhookPayload` |
| `PipefyLog` | `WebhookLog` |
| tabela `pipefy_logs` | tabela `webhook_logs` |
| `PipefyLogRepository` | `WebhookLogRepository` |
| `SurveyService._enqueue_pipefy` | `_enqueue_webhook` |
| `SurveyService.retry_pending_pipefy` | `retry_pending_webhook` |
| `POST /api/admin/pipefy/retry` | `POST /api/admin/webhook/retry` |
| `PIPEFY_WEBHOOK_URL` | `WEBHOOK_URL` |
| `PIPEFY_API_TOKEN` | `WEBHOOK_TOKEN` |
| `PIPEFY_RETRY_MAX` | `WEBHOOK_RETRY_MAX` |
| `PIPEFY_RETRY_DELAY_SECONDS` | `WEBHOOK_RETRY_DELAY_SECONDS` |
| `docs/PIPEFY.md` | `docs/WEBHOOK.md` |

Referências a atualizar fora do código: `README.md` (link para o doc),
`docs/INSTALACAO.md` (linha de troubleshooting), `docs/PRODUCAO.md` (checklist e
tabela de variáveis).

`docs/PRODUCAO.md` cita hoje uma variável `PIPEFY_PIPE_ID` que não existe em
`app/core/config.py` — erro de documentação, será removido em vez de renomeado.

O novo `docs/WEBHOOK.md` documenta a configuração com n8n:

- node **Webhook** (POST) e a URL gerada indo em `WEBHOOK_URL`;
- **Header Auth** ativado no n8n com o mesmo valor de `WEBHOOK_TOKEN`, já que o
  código envia `Authorization: Bearer <token>` — sem isso o endpoint fica aberto
  recebendo nome, CPF e telefone de quem descobrir a URL;
- **Respond Immediately** no node de Webhook, porque o cliente HTTP do backend
  tem timeout de 30s e o voto do usuário fica esperando se o workflow demorar;
- workflow secundário com trigger **Schedule** chamando
  `POST /api/admin/webhook/retry` periodicamente.

Esse último ponto cobre uma lacuna que existe hoje independente do n8n: quando um
envio falha, ele fica `status=failed` na tabela e só é reprocessado se um admin
clicar manualmente na rota de retry — não há job automático.

## Testes

**Unitários** (`tests/unit/test_socio.py`, novo):

- aceita `0001`, `9999`, `0042` (zeros à esquerda preservados);
- rejeita 3 dígitos, 5 dígitos, vazio, letras;
- rejeita dígitos unicode (`٤٢٣١`, `²²²²`) — é a razão de não usar `isdigit()`;
- `normalize_numero_socio` remove máscara/espaços.

**Integração:**

- `/register` com número de sócio já usado devolve 400 com a mensagem certa;
- fluxo completo até `/submit` e o CSV exportado contém o número de sócio;
- `submit` com CPF duplicado continua devolvendo a mensagem de CPF (garante que
  a distinção de constraint funciona nos dois sentidos).

**Nova fixture em `tests/conftest.py`:** `numero_socio`, análoga à `valid_cpf` que
já existe — gera números de 4 dígitos distintos dentro da mesma run, para que os
testes não colidam entre si na constraint `UNIQUE`.

**Testes existentes a atualizar** — todos passam a enviar `numero_socio` no
`/register`, usando a fixture acima:

- `tests/integration/test_otp_flow.py`
- `tests/integration/test_submit.py`
- `tests/security/test_rate_limits.py`
- `tests/security/test_otp_storage_regression.py`
- `tests/load/locustfile.py`

`tests/conftest.py` também define `PIPEFY_API_TOKEN` e `PIPEFY_WEBHOOK_URL` no
bloco de `os.environ.setdefault` do topo — as duas passam a ser `WEBHOOK_TOKEN` e
`WEBHOOK_URL`. Sem isso os testes sobem com a configuração antiga e o
`Settings()` renomeado ignora os valores.

## Fora de escopo

- Busca do admin por número de sócio (só CPF hoje) — não foi pedido.
- Validação contra o quadro social oficial.
- Endpoint público de consulta de número de sócio.
- Corrigir o `index=True` redundante da coluna `cpf`.
