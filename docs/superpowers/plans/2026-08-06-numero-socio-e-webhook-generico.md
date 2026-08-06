# Número de sócio + webhook genérico — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exigir número de sócio (4 dígitos, único) no cadastro da sondagem e fazê-lo aparecer nas quatro saídas de dados, e renomear a integração "Pipefy" para "Webhook" em todos os níveis, já que o destino passa a ser o n8n.

**Architecture:** O campo entra no fluxo existente de cadastro (`/register` → sessão Redis → `/submit`), com unicidade garantida por constraint no banco e checada otimisticamente antes do envio de OTP para não gastar SMS à toa. A renomeação é mecânica e não muda comportamento — `PipefyService` sempre foi um POST de JSON genérico.

**Tech Stack:** FastAPI, SQLAlchemy 2 (async, `Mapped`/`mapped_column`), Alembic, Pydantic v2 (`field_validator`), Postgres (asyncpg), Redis, pytest + pytest-asyncio, openpyxl, Jinja2, JS vanilla.

## Global Constraints

- Spec de referência: `docs/superpowers/specs/2026-08-06-numero-socio-e-webhook-generico-design.md`.
- Branch de trabalho: `feature/numero-socio-e-webhook-generico`.
- Número de sócio: exatamente 4 dígitos ASCII, armazenado como `String(4)` (texto, nunca inteiro — zeros à esquerda são significativos).
- Validação por `re.fullmatch(r"[0-9]{4}", ...)`, **nunca** `str.isdigit()` (aceita dígitos unicode como `٤` e `²`).
- Constraint única nomeada explicitamente `uq_associados_numero_socio` no modelo, porque os testes criam o schema por `Base.metadata.create_all` e a produção por Alembic — sem nome explícito os dois divergem.
- `str(exc.orig)` de `IntegrityError` contém o valor que violou a constraint (inclusive o CPF completo). Usar só em `if`, **nunca** em `logger.*`.
- Sem endpoint público de consulta de número de sócio (4 dígitos = 10.000 combinações, enumerável).
- Rodar testes com `pytest` na raiz do repo. Requer Postgres em `sondagem_test` e Redis (índice 1) no ar — ver `tests/conftest.py`.
- Mensagens de UI e de erro em português.

**Desvio consciente da spec:** a spec descreve uma migration única (`003_add_numero_socio_and_rename_webhook.py`). Este plano usa **duas** (`003_rename_pipefy_to_webhook.py` e `004_add_numero_socio.py`) para que a renomeação e a feature sejam revisáveis e reversíveis de forma independente. O conteúdo total é idêntico.

---

### Task 1: Renomear Pipefy → Webhook

Mudança mecânica, sem alteração de comportamento. O critério de pronto é a suíte existente continuar verde.

**Files:**
- Rename: `app/integrations/pipefy.py` → `app/integrations/webhook.py`
- Modify: `app/core/config.py:54-60`
- Modify: `app/schemas/__init__.py:156-163`
- Modify: `app/models/__init__.py:83-97`
- Modify: `app/repositories/__init__.py:5`, `110-127`
- Modify: `app/services/survey_service.py:10-20`, `35-37`, `199`, `210-252`
- Modify: `app/api/routes/admin.py:230-238`
- Modify: `app/main.py:38-52`, `62`, `66-70`
- Modify: `tests/conftest.py:37-38`
- Create: `alembic/versions/003_rename_pipefy_to_webhook.py`
- Rename: `docs/PIPEFY.md` → `docs/WEBHOOK.md` (reescrito para n8n)
- Modify: `README.md:66`, `docs/INSTALACAO.md:115`, `docs/PRODUCAO.md:15`, `:37-39`, `:269-271`
- Test: `tests/integration/test_admin_auth.py` (novo teste da rota renomeada)

**Interfaces:**
- Consumes: nada (primeira task).
- Produces: `WebhookService` (`send_webhook(payload: WebhookPayload) -> tuple[bool, str | None]`, `serialize_payload`/`deserialize_payload` estáticos), `WebhookPayload` (Pydantic), `WebhookLog` (tabela `webhook_logs`), `WebhookLogRepository` (`create`, `list_pending`, `update`), `SurveyService._enqueue_webhook(associado_id: int, payload: WebhookPayload) -> None` e `SurveyService.retry_pending_webhook() -> int`, settings `webhook_url`/`webhook_token`/`webhook_retry_max`/`webhook_retry_delay_seconds`, rota `POST /api/admin/webhook/retry`.

- [ ] **Step 1: Renomear as variáveis de configuração**

Em `app/core/config.py`, substituir o bloco `pipefy_*` (linhas 54-60) por:

```python
    webhook_url: str = Field(default="", alias="WEBHOOK_URL")
    webhook_token: str = Field(default="", alias="WEBHOOK_TOKEN")
    webhook_retry_max: int = Field(default=5, alias="WEBHOOK_RETRY_MAX")
    webhook_retry_delay_seconds: int = Field(
        default=60,
        alias="WEBHOOK_RETRY_DELAY_SECONDS",
    )
```

- [ ] **Step 2: Renomear o schema do payload**

Em `app/schemas/__init__.py`, trocar `class PipefyPayload(BaseModel):` por `class WebhookPayload(BaseModel):`. Os campos ficam inalterados nesta task.

- [ ] **Step 3: Renomear o modelo e a tabela**

Em `app/models/__init__.py`, trocar `class PipefyLog(Base):` por `class WebhookLog(Base):` e `__tablename__ = "pipefy_logs"` por `__tablename__ = "webhook_logs"`. As colunas ficam inalteradas.

- [ ] **Step 4: Renomear o repositório**

Em `app/repositories/__init__.py`, ajustar o import da linha 5 (`PipefyLog` → `WebhookLog`) e trocar `class PipefyLogRepository:` por `class WebhookLogRepository:`, incluindo as anotações de tipo `PipefyLog` → `WebhookLog` em `create`, `list_pending` e `update`.

- [ ] **Step 5: Renomear o módulo de integração**

```bash
git mv app/integrations/pipefy.py app/integrations/webhook.py
```

Conteúdo completo do novo `app/integrations/webhook.py`:

```python
import json
import logging

import httpx

from app.core.config import get_settings
from app.schemas import WebhookPayload
from app.utils.cpf import mask_cpf

logger = logging.getLogger(__name__)


class WebhookService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def send_webhook(self, payload: WebhookPayload) -> tuple[bool, str | None]:
        if not self.settings.webhook_url:
            logger.warning("WEBHOOK_URL não configurada")
            return False, "Webhook URL não configurada"

        headers = {"Content-Type": "application/json"}
        if self.settings.webhook_token:
            headers["Authorization"] = f"Bearer {self.settings.webhook_token}"

        body = payload.model_dump()

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self.settings.webhook_url,
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
            logger.info(
                "Webhook enviado com sucesso para CPF %s", mask_cpf(payload.cpf)
            )
            return True, None
        except Exception as exc:
            error_msg = str(exc)
            logger.exception("Falha ao enviar webhook: %s", error_msg)
            return False, error_msg

    @staticmethod
    def serialize_payload(payload: WebhookPayload) -> str:
        return json.dumps(payload.model_dump(), ensure_ascii=False)

    @staticmethod
    def deserialize_payload(data: str) -> WebhookPayload:
        return WebhookPayload(**json.loads(data))
```

- [ ] **Step 6: Atualizar o SurveyService**

Em `app/services/survey_service.py`:

- import: `from app.integrations.pipefy import PipefyService` → `from app.integrations.webhook import WebhookService`
- import de models: `PipefyLog` → `WebhookLog`
- import de repositories: `PipefyLogRepository` → `WebhookLogRepository`
- import de schemas: `PipefyPayload` → `WebhookPayload`
- no `__init__`: `self.pipefy_log_repo = PipefyLogRepository(db)` → `self.webhook_log_repo = WebhookLogRepository(db)` e `self.pipefy_service = PipefyService()` → `self.webhook_service = WebhookService()`
- em `submit_vote`: `payload = PipefyPayload(...)` → `payload = WebhookPayload(...)` e `await self._enqueue_pipefy(...)` → `await self._enqueue_webhook(...)`

Os dois métodos passam a ser:

```python
    async def _enqueue_webhook(self, associado_id: int, payload: WebhookPayload) -> None:
        log = WebhookLog(
            associado_id=associado_id,
            payload=self.webhook_service.serialize_payload(payload),
            status="pending",
        )
        await self.webhook_log_repo.create(log)

        success, error = await self.webhook_service.send_webhook(payload)
        log.tentativas += 1
        if success:
            log.status = "sent"
            log.enviado_em = datetime.now(UTC)
        else:
            log.status = "failed"
            log.ultimo_erro = error
        await self.webhook_log_repo.update(log)

    async def retry_pending_webhook(self) -> int:
        pending = await self.webhook_log_repo.list_pending()
        retried = 0
        settings = self.webhook_service.settings

        for log in pending:
            if log.tentativas >= settings.webhook_retry_max:
                continue

            payload = self.webhook_service.deserialize_payload(log.payload)
            success, error = await self.webhook_service.send_webhook(payload)
            log.tentativas += 1

            if success:
                log.status = "sent"
                log.enviado_em = datetime.now(UTC)
                log.ultimo_erro = None
            else:
                log.status = "failed"
                log.ultimo_erro = error

            await self.webhook_log_repo.update(log)
            retried += 1

        return retried
```

- [ ] **Step 7: Atualizar a rota do admin**

Em `app/api/routes/admin.py`, substituir o bloco das linhas 230-238 por:

```python
@router.post("/webhook/retry")
async def retry_webhook(
    db: AsyncSession = Depends(get_db),
    otp_service: OTPService = Depends(get_otp_service),
    _: dict = Depends(get_admin_token),
) -> dict:
    service = SurveyService(db, otp_service)
    count = await service.retry_pending_webhook()
    return {"retried": count, "message": f"{count} envio(s) reprocessado(s)"}
```

- [ ] **Step 8: Atualizar o worker de background**

Em `app/main.py`, substituir a função das linhas 38-52 por:

```python
async def webhook_retry_worker() -> None:
    """Background task to retry failed webhook deliveries."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                otp_service = OTPService(redis_service)
                service = SurveyService(db, otp_service)
                count = await service.retry_pending_webhook()
                if count:
                    logger.info("Webhook retry worker: %d envio(s) reprocessado(s)", count)
                await db.commit()
        except Exception as exc:
            logger.exception("Erro no worker de webhook: %s", exc)

        await asyncio.sleep(settings.webhook_retry_delay_seconds)
```

Na linha 62, trocar `asyncio.create_task(pipefy_retry_worker())` por `asyncio.create_task(webhook_retry_worker())`. No comentário das linhas 66-70, trocar "tentativa de retry do Pipefy" por "tentativa de retry do webhook".

- [ ] **Step 9: Atualizar as variáveis de ambiente dos testes**

Em `tests/conftest.py`, trocar as linhas 37-38 por:

```python
os.environ.setdefault("WEBHOOK_TOKEN", "")
os.environ.setdefault("WEBHOOK_URL", "")
```

- [ ] **Step 10: Criar a migration de renomeação da tabela**

Criar `alembic/versions/003_rename_pipefy_to_webhook.py`:

```python
"""Rename pipefy_logs to webhook_logs

Revision ID: 003
Revises: 002
Create Date: 2026-08-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("pipefy_logs", "webhook_logs")


def downgrade() -> None:
    op.rename_table("webhook_logs", "pipefy_logs")
```

- [ ] **Step 11: Escrever o teste da rota renomeada**

Acrescentar ao final de `tests/integration/test_admin_auth.py`:

```python
class TestRotaDeRetryDoWebhook:
    """A rota foi renomeada de /admin/pipefy/retry para /admin/webhook/retry
    quando a integração deixou de ser específica do Pipefy."""

    async def test_rota_nova_responde(self, client, admin_token):
        res = await client.post(
            "/api/admin/webhook/retry",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        assert "retried" in res.json()

    async def test_rota_antiga_nao_existe_mais(self, client, admin_token):
        res = await client.post(
            "/api/admin/pipefy/retry",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 404
```

- [ ] **Step 12: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS. A renomeação não muda comportamento, então qualquer falha aqui é referência a nome antigo que passou despercebida. Se algo falhar, procurar sobras com `grep -rn -i pipefy app/ tests/` — o resultado esperado é vazio.

- [ ] **Step 13: Reescrever a documentação da integração**

```bash
git mv docs/PIPEFY.md docs/WEBHOOK.md
```

Substituir o conteúdo de `docs/WEBHOOK.md` por documentação da integração com n8n, cobrindo obrigatoriamente:

- as quatro variáveis: `WEBHOOK_URL`, `WEBHOOK_TOKEN`, `WEBHOOK_RETRY_MAX`, `WEBHOOK_RETRY_DELAY_SECONDS`;
- criar um node **Webhook** (POST) no n8n e colar a URL gerada em `WEBHOOK_URL`;
- ativar **Header Auth** no node com o mesmo valor de `WEBHOOK_TOKEN`, porque o backend envia `Authorization: Bearer <token>` — sem isso o endpoint fica aberto recebendo nome, CPF e telefone de quem descobrir a URL;
- marcar **Respond Immediately** no node, porque o cliente HTTP do backend tem timeout de 30s e o voto do usuário fica preso esperando se o workflow demorar mais que isso;
- registrar que **não é preciso** workflow agendado de retry: `webhook_retry_worker` em `app/main.py` já reprocessa envios falhos em loop a cada `WEBHOOK_RETRY_DELAY_SECONDS`, e `POST /api/admin/webhook/retry` é gatilho manual complementar;
- exemplo do JSON recebido (ainda sem `numero_socio` nesta task — a Task 4 atualiza este exemplo).

- [ ] **Step 14: Atualizar as demais referências na documentação**

- `README.md:66`: `- [Integração Pipefy](docs/PIPEFY.md)` → `- [Integração via Webhook](docs/WEBHOOK.md)`
- `docs/INSTALACAO.md:115`: trocar a linha de troubleshooting para `| Webhook não recebe | Verifique \`WEBHOOK_URL\`; use retry manual no admin |`
- `docs/PRODUCAO.md:15`: `Provedor de OTP contratado (Twilio, Zenvia ou Z-API) com credenciais` fica; acrescentar destino de webhook (n8n) configurado
- `docs/PRODUCAO.md:37-39`: `OTP_PROVIDER` fica; a linha `| \`PIPEFY_API_TOKEN\` / \`PIPEFY_PIPE_ID\` | ver \`docs/PIPEFY.md\` |` vira `| \`WEBHOOK_URL\` / \`WEBHOOK_TOKEN\` | ver \`docs/WEBHOOK.md\` |`. **`PIPEFY_PIPE_ID` não existe em `app/core/config.py`** — é erro de documentação e sai, não é renomeado.
- `docs/PRODUCAO.md:269-271`: trocar a menção a `PIPEFY_API_TOKEN` e a `field_id` do Pipefy por `WEBHOOK_TOKEN` e o node de Webhook do n8n.

- [ ] **Step 15: Confirmar que não sobrou nenhuma referência**

Run: `grep -rn -i pipefy app/ tests/ docs/ README.md alembic/versions/004_add_numero_socio.py 2>/dev/null`
Expected: apenas as duas ocorrências dentro de `alembic/versions/003_rename_pipefy_to_webhook.py` (o nome do arquivo e as chamadas de `rename_table`, que precisam manter o nome antigo). Qualquer outra é sobra a corrigir.

- [ ] **Step 16: Commit**

```bash
git add -A
git commit -m "refactor: renomeia integracao Pipefy para Webhook

O PipefyService nunca teve nada de especifico do Pipefy: sempre foi um
POST de JSON com Bearer token opcional. Com o destino passando a ser o
n8n, o nome so confundia.

Renomeia env vars, classes, tabela, rota, worker e docs. Sem mudanca
de comportamento."
```

---

### Task 2: Módulo de validação do número de sócio

Unidade isolada, sem banco e sem rede. TDD puro.

**Files:**
- Create: `app/utils/socio.py`
- Test: `tests/unit/test_socio.py`

**Interfaces:**
- Consumes: nada.
- Produces: `normalize_numero_socio(valor: str) -> str` e `validate_numero_socio(valor: str) -> bool`, ambos em `app/utils/socio.py`. Usados na Task 3 pelo `CadastroRequest`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/unit/test_socio.py`:

```python
import pytest

from app.utils.socio import normalize_numero_socio, validate_numero_socio


class TestNormalizeNumeroSocio:
    def test_remove_mascara_e_espacos(self):
        assert normalize_numero_socio(" 00-42 ") == "0042"

    def test_mantem_digitos_puros_inalterados(self):
        assert normalize_numero_socio("1234") == "1234"

    def test_string_sem_digitos_vira_vazia(self):
        assert normalize_numero_socio("abcd") == ""


class TestValidateNumeroSocio:
    @pytest.mark.parametrize("valor", ["0001", "9999", "0042", "1234"])
    def test_aceita_quatro_digitos(self, valor):
        assert validate_numero_socio(valor) is True

    def test_preserva_zeros_a_esquerda(self):
        """0042 e 42 sao socios diferentes — por isso o campo e texto, nao int."""
        assert validate_numero_socio("0042") is True
        assert validate_numero_socio("42") is False

    @pytest.mark.parametrize("valor", ["123", "12345", "", "abcd", "12a4", "12 4"])
    def test_rejeita_formato_invalido(self, valor):
        assert validate_numero_socio(valor) is False

    @pytest.mark.parametrize("valor", ["٤٢٣١", "²²²²", "١٢٣٤"])
    def test_rejeita_digitos_unicode(self, valor):
        """
        A premissa deste teste e que str.isdigit() aceita esses caracteres —
        e por isso que a validacao usa re.fullmatch(r"[0-9]{4}"). A primeira
        asserta a armadilha (se um dia deixar de valer, o teste avisa em vez
        de virar tautologia); a segunda asserta o comportamento.
        """
        assert valor.isdigit() is True
        assert validate_numero_socio(valor) is False
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/unit/test_socio.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.utils.socio'`

- [ ] **Step 3: Implementar o módulo**

Criar `app/utils/socio.py`:

```python
import re

# Exatamente 4 dígitos ASCII. Deliberadamente [0-9] e não \d nem
# str.isdigit(): os dois últimos aceitam dígitos unicode ("٤".isdigit() e
# "²".isdigit() são ambos True), o que deixaria entrar no banco um "número
# de sócio" que não corresponde a nenhum número real do quadro social.
_NUMERO_SOCIO_RE = re.compile(r"[0-9]{4}")


def normalize_numero_socio(valor: str) -> str:
    return re.sub(r"\D", "", valor)


def validate_numero_socio(valor: str) -> bool:
    return _NUMERO_SOCIO_RE.fullmatch(valor) is not None
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/unit/test_socio.py -v`
Expected: PASS (todos)

Comportamento verificado dos três casos unicode (Python 3.12+), que explica por que os dois passos são necessários:

| Entrada | `isdigit()` | Sobra de `re.sub(r"\D","",...)` | `validate` |
|---|---|---|---|
| `"٤٢٣١"` (arábico-índico, Nd) | `True` | 4 caracteres — **não** são removidos | `False` |
| `"²²²²"` (sobrescrito, No) | `True` | 0 caracteres — são removidos | `False` |

`\d` em modo unicode casa só a categoria Nd, então `\D` remove os sobrescritos mas preserva os arábico-índicos. A normalização sozinha **não** basta: é `validate_numero_socio` com `[0-9]` explícito que barra o primeiro caso.

- [ ] **Step 5: Commit**

```bash
git add app/utils/socio.py tests/unit/test_socio.py
git commit -m "feat: adiciona validacao de numero de socio

Quatro digitos ASCII, validados por regex explicita em vez de
str.isdigit(), que aceita digitos unicode."
```

---

### Task 3: Número de sócio no backend, ponta a ponta

Modelo, migration, schema, repositório e fluxo. Precisa ser uma task só: a coluna é `NOT NULL`, então assim que ela existe o `submit_vote` quebra até a API passar a enviar o campo, e assim que o campo vira obrigatório no `CadastroRequest` os testes existentes de `/register` quebram até serem atualizados.

**Files:**
- Modify: `app/models/__init__.py:1-27` (import de `UniqueConstraint` e a classe `Associado`)
- Create: `alembic/versions/004_add_numero_socio.py`
- Modify: `app/schemas/__init__.py:1-7` (imports), `22-47` (`CadastroRequest`)
- Modify: `app/repositories/__init__.py:9-26` (`AssociadoRepository`)
- Modify: `app/services/survey_service.py:39-79` (checagens e `register_and_send_otp`), `121-208` (`submit_vote`)
- Modify: `app/api/routes/survey.py:54-82` (passar o campo adiante)
- Modify: `tests/conftest.py` (nova fixture `numero_socio`)
- Modify: `tests/integration/test_otp_flow.py:4`, `:20`, `:45`
- Modify: `tests/integration/test_submit.py:5`, `:100`
- Modify: `tests/security/test_otp_storage_regression.py:19`
- Modify: `tests/load/locustfile.py:71-80`
- Test: `tests/integration/test_numero_socio.py` (novo)

**Interfaces:**
- Consumes: `normalize_numero_socio`, `validate_numero_socio` de `app/utils/socio.py` (Task 2).
- Produces: `Associado.numero_socio: str`; `AssociadoRepository.get_by_numero_socio(numero_socio: str) -> Associado | None`; `SurveyService.check_numero_socio_available(numero_socio: str) -> tuple[bool, str | None]`; `SurveyService.register_and_send_otp(nome, cpf, telefone, numero_socio, ip, user_agent)` com `numero_socio: str` como quarto parâmetro posicional; fixture pytest `numero_socio` que devolve um callable `() -> str`. A Task 4 consome `Associado.numero_socio` nas exportações.

- [ ] **Step 1: Escrever os testes de integração que falham**

Criar `tests/integration/test_numero_socio.py`:

```python
"""
O número de sócio identifica o associado no quadro do clube e é único: dois
cadastros não podem usar o mesmo número, do mesmo jeito que não podem usar o
mesmo CPF.

Não existe endpoint público de consulta desse número, por decisão de
segurança: são 4 dígitos, logo 10.000 combinações, e um endpoint de
"esse número já votou?" seria enumerável por inteiro.
"""


class TestNumeroSocioNoCadastro:
    async def test_register_aceita_numero_de_quatro_digitos(
        self, client, valid_cpf, numero_socio
    ):
        res = await client.post(
            "/api/survey/register",
            json={
                "nome": "Socio Valido",
                "cpf": valid_cpf(),
                "telefone": "11977770001",
                "numero_socio": numero_socio(),
                "recaptcha_token": "",
            },
        )
        assert res.status_code == 200
        assert "session_token" in res.json()

    async def test_register_rejeita_numero_com_tres_digitos(self, client, valid_cpf):
        res = await client.post(
            "/api/survey/register",
            json={
                "nome": "Socio Curto",
                "cpf": valid_cpf(),
                "telefone": "11977770002",
                "numero_socio": "123",
                "recaptcha_token": "",
            },
        )
        assert res.status_code == 422

    async def test_register_rejeita_numero_sem_o_campo(self, client, valid_cpf):
        res = await client.post(
            "/api/survey/register",
            json={
                "nome": "Socio Sem Numero",
                "cpf": valid_cpf(),
                "telefone": "11977770003",
                "recaptcha_token": "",
            },
        )
        assert res.status_code == 422


class TestUnicidadeDoNumeroSocio:
    async def test_numero_repetido_e_barrado_antes_de_enviar_otp(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code
    ):
        """
        Barrar no /register (e não só no /submit) evita gastar um SMS num
        cadastro que seria rejeitado no fim do fluxo.
        """
        from app.models import Associado

        numero = numero_socio()
        db_session.add(
            Associado(
                nome="Ja Votou",
                cpf=valid_cpf(),
                telefone="11966660001",
                numero_socio=numero,
                aceite_lgpd=True,
            )
        )
        await db_session.commit()

        res = await client.post(
            "/api/survey/register",
            json={
                "nome": "Segundo Socio",
                "cpf": valid_cpf(),
                "telefone": "11966660002",
                "numero_socio": numero,
                "recaptcha_token": "",
            },
        )
        assert res.status_code == 400
        assert "sócio" in res.json()["detail"].lower()

    async def test_cpf_repetido_continua_com_a_mensagem_de_cpf(
        self, client, db_session, valid_cpf, numero_socio
    ):
        """Garante que a distinção por constraint funciona nos dois sentidos:
        a mensagem de CPF não pode virar a de número de sócio."""
        from app.models import Associado

        cpf = valid_cpf()
        db_session.add(
            Associado(
                nome="Ja Votou",
                cpf=cpf,
                telefone="11966660003",
                numero_socio=numero_socio(),
                aceite_lgpd=True,
            )
        )
        await db_session.commit()

        res = await client.post(
            "/api/survey/register",
            json={
                "nome": "Outro Socio",
                "cpf": cpf,
                "telefone": "11966660004",
                "numero_socio": numero_socio(),
                "recaptcha_token": "",
            },
        )
        assert res.status_code == 400
        assert "CPF" in res.json()["detail"]


class TestNumeroSocioPersistido:
    async def test_numero_chega_ao_banco_com_zeros_a_esquerda(
        self, client, db_session, valid_cpf, read_otp_code
    ):
        from sqlalchemy import select

        from app.models import Associado, Candidato

        c1 = Candidato(nome="Fulano", apelido="Fu", ativo=True)
        db_session.add(c1)
        await db_session.commit()
        await db_session.refresh(c1)

        telefone = "11955550001"
        res = await client.post(
            "/api/survey/register",
            json={
                "nome": "Socio Zero",
                "cpf": valid_cpf(),
                "telefone": telefone,
                "numero_socio": "0042",
                "recaptcha_token": "",
            },
        )
        session_token = res.json()["session_token"]
        codigo = read_otp_code(telefone)
        await client.post(
            "/api/survey/verify-otp",
            json={
                "session_token": session_token,
                "telefone": telefone,
                "codigo": codigo,
            },
        )
        res = await client.post(
            "/api/survey/submit",
            json={
                "session_token": session_token,
                "candidatos_ids": [c1.id],
                "candidato_preferido_id": c1.id,
                "aceite_lgpd": True,
            },
        )
        assert res.status_code == 200

        result = await db_session.execute(
            select(Associado).where(Associado.nome == "Socio Zero")
        )
        associado = result.scalar_one()
        assert associado.numero_socio == "0042"
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `pytest tests/integration/test_numero_socio.py -v`
Expected: FAIL — `fixture 'numero_socio' not found` nos que usam a fixture, e 200 em vez de 422 nos que esperam rejeição (o campo ainda não existe, então é ignorado pelo Pydantic).

- [ ] **Step 3: Adicionar a fixture de número de sócio**

Em `tests/conftest.py`, logo após a fixture `valid_cpf` (linha 261), acrescentar:

```python
@pytest.fixture
def numero_socio():
    """
    Gera números de sócio de 4 dígitos distintos dentro da mesma run. A
    coluna tem constraint UNIQUE, então dois testes que sorteassem o mesmo
    número quebrariam um ao outro de forma intermitente — daí um contador
    sequencial em vez de random.
    """
    contador = itertools.count(1)

    def generate() -> str:
        return f"{next(contador) % 10000:04d}"

    return generate
```

E acrescentar `import itertools` ao bloco de imports do arquivo (junto de `import re`, linha 61).

- [ ] **Step 4: Adicionar a coluna ao modelo**

Em `app/models/__init__.py`, acrescentar `UniqueConstraint` ao import do SQLAlchemy (já está lá na linha 3 — confirmar) e alterar a classe `Associado`:

```python
class Associado(Base):
    __tablename__ = "associados"
    # Constraint nomeada explicitamente porque Base.metadata não tem
    # naming_convention: com unique=True inline, create_all (usado pelos
    # testes) geraria "associados_numero_socio_key" e o Alembic
    # "uq_associados_numero_socio". A lógica que distingue qual constraint
    # estourou depende de os dois ambientes concordarem.
    __table_args__ = (
        UniqueConstraint("numero_socio", name="uq_associados_numero_socio"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    cpf: Mapped[str] = mapped_column(String(11), unique=True, nullable=False, index=True)
    # Texto, não inteiro: zeros à esquerda são significativos (0042 != 42).
    numero_socio: Mapped[str] = mapped_column(String(4), nullable=False)
    telefone: Mapped[str] = mapped_column(String(20), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    aceite_lgpd: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_resposta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    respostas: Mapped[list["Resposta"]] = relationship(back_populates="associado")
    preferencia: Mapped["Preferencia | None"] = relationship(back_populates="associado")
```

Não acrescentar `index=True` na coluna nova: a constraint `UNIQUE` já cria o índice em Postgres.

- [ ] **Step 5: Criar a migration**

Criar `alembic/versions/004_add_numero_socio.py`:

```python
"""Add numero_socio to associados

Revision ID: 004
Revises: 003
Create Date: 2026-08-06

Pré-condição: a tabela associados precisa estar vazia. A coluna entra como
NOT NULL sem server_default, o que o Postgres recusa se houver linhas. Foi
uma decisão consciente (o sistema ainda não coletou votos em produção); se
isso mudar antes do deploy, esta migration precisa virar coluna nulável +
backfill.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "associados",
        sa.Column("numero_socio", sa.String(length=4), nullable=False),
    )
    op.create_unique_constraint(
        "uq_associados_numero_socio", "associados", ["numero_socio"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_associados_numero_socio", "associados", type_="unique")
    op.drop_column("associados", "numero_socio")
```

- [ ] **Step 6: Adicionar o campo ao schema de cadastro**

Em `app/schemas/__init__.py`, acrescentar aos imports do topo:

```python
from app.utils.socio import normalize_numero_socio, validate_numero_socio
```

E em `CadastroRequest`, acrescentar o campo e seu validador:

```python
class CadastroRequest(BaseModel):
    nome: str = Field(min_length=3, max_length=255)
    cpf: str
    numero_socio: str
    telefone: str
    recaptcha_token: str = Field(default="")

    # ... validadores existentes de nome e cpf ...

    @field_validator("numero_socio")
    @classmethod
    def validate_numero_socio_field(cls, value: str) -> str:
        numero = normalize_numero_socio(value)
        if not validate_numero_socio(numero):
            raise ValueError("Número de sócio deve ter exatamente 4 dígitos")
        return numero
```

- [ ] **Step 7: Adicionar a busca por número de sócio no repositório**

Em `app/repositories/__init__.py`, dentro de `AssociadoRepository`, logo após `get_by_cpf`:

```python
    async def get_by_numero_socio(self, numero_socio: str) -> Associado | None:
        result = await self.db.execute(
            select(Associado).where(Associado.numero_socio == numero_socio)
        )
        return result.scalar_one_or_none()
```

- [ ] **Step 8: Levar o campo pelo fluxo do serviço**

Em `app/services/survey_service.py`, acrescentar após `check_cpf_available`:

```python
    async def check_numero_socio_available(self, numero_socio: str) -> tuple[bool, str | None]:
        existing = await self.associado_repo.get_by_numero_socio(numero_socio)
        if existing:
            return False, "Este número de sócio já participou da sondagem"
        return True, None
```

Alterar `register_and_send_otp` para receber e checar o campo, e guardá-lo na sessão:

```python
    async def register_and_send_otp(
        self,
        nome: str,
        cpf: str,
        telefone: str,
        numero_socio: str,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[str | None, str | None]:
        available, msg = await self.check_cpf_available(cpf)
        if not available:
            return None, msg

        # Checado aqui, e não só no submit, para não gastar um SMS num
        # cadastro que seria rejeitado no fim do fluxo.
        available, msg = await self.check_numero_socio_available(numero_socio)
        if not available:
            return None, msg

        session_token = await self.otp_service.create_session(
            {
                "nome": nome,
                "cpf": cpf,
                "telefone": telefone,
                "numero_socio": numero_socio,
                "verified": False,
                "ip": ip,
                "user_agent": user_agent,
            }
        )

        success, error = await self.otp_service.send_otp(telefone)
        if not success:
            await self.otp_service.delete_session(session_token)
            return None, error

        await self.audit_repo.create(
            "otp_sent",
            f"OTP enviado para telefone {telefone[-4:]}",
            ip,
            user_agent,
        )
        return session_token, None
```

- [ ] **Step 9: Gravar o campo e distinguir a constraint violada no submit**

Em `app/services/survey_service.py`, dentro de `submit_vote`, após `cpf = session["cpf"]`, acrescentar a leitura e a checagem:

```python
        cpf = session["cpf"]
        numero_socio = session["numero_socio"]

        available, msg = await self.check_cpf_available(cpf)
        if not available:
            return False, msg

        available, msg = await self.check_numero_socio_available(numero_socio)
        if not available:
            return False, msg
```

Passar o campo na construção do `Associado`:

```python
        associado = Associado(
            nome=session["nome"],
            cpf=cpf,
            numero_socio=numero_socio,
            telefone=session["telefone"],
            ip=ip or session.get("ip"),
            user_agent=user_agent or session.get("user_agent"),
            aceite_lgpd=aceite_lgpd,
        )
```

E substituir o `except IntegrityError` por uma versão que distingue as duas constraints:

```python
        try:
            associado = await self.associado_repo.create(associado)
        except IntegrityError as exc:
            # As checagens acima são otimistas — não há lock entre o SELECT e
            # este INSERT, e entre os dois ainda passa todo o fluxo de OTP.
            # As constraints UNIQUE do banco são quem realmente garante um
            # voto por CPF e um por número de sócio; aqui só traduzimos a
            # violação numa mensagem que diz qual dos dois repetiu.
            #
            # Casa o nome da COLUNA, não o da constraint: o texto do asyncpg
            # traz os dois (nome da constraint + "DETAIL: Key (coluna)=..."),
            # e casar pela coluna sobrevive a uma renomeação de constraint.
            #
            # Esse texto NUNCA pode ir para log: ele inclui o valor que
            # violou a constraint, ou seja, o CPF completo em texto puro.
            await self.db.rollback()
            if "numero_socio" in str(exc.orig):
                return False, "Este número de sócio já participou da sondagem"
            return False, "Este CPF já participou da sondagem"
```

- [ ] **Step 10: Passar o campo na rota**

Em `app/api/routes/survey.py`, dentro de `register`, acrescentar o argumento na chamada:

```python
    session_token, error = await service.register_and_send_otp(
        nome=body.nome,
        cpf=body.cpf,
        telefone=body.telefone,
        numero_socio=body.numero_socio,
        ip=ip,
        user_agent=get_user_agent(request),
    )
```

- [ ] **Step 11: Atualizar os testes existentes que chamam /register**

Quatro arquivos, todos acrescentando `numero_socio` ao corpo do POST.

`tests/integration/test_submit.py` — o helper do topo passa a receber a fixture:

```python
async def _register_verify(
    client, valid_cpf, numero_socio, read_otp_code, telefone, nome="Teste Fluxo"
):
    """Completa cadastro + verificação de OTP, devolvendo o session_token pronto
    para submissão — evita repetir esse bloco em cada teste de submit."""
    res = await client.post(
        "/api/survey/register",
        json={
            "nome": nome,
            "cpf": valid_cpf(),
            "telefone": telefone,
            "numero_socio": numero_socio(),
            "recaptcha_token": "",
        },
    )
    session_token = res.json()["session_token"]
    code = read_otp_code(telefone)
    await client.post(
        "/api/survey/verify-otp",
        json={"session_token": session_token, "telefone": telefone, "codigo": code},
    )
    return session_token
```

Cada teste que chama `_register_verify` passa a declarar a fixture `numero_socio` na assinatura e a repassá-la: `await _register_verify(client, valid_cpf, numero_socio, read_otp_code, "1198888800X")`. Também a chamada direta de `/register` na linha ~100.

`tests/integration/test_otp_flow.py` — nas três chamadas (linhas 4, 20, 45), acrescentar `"numero_socio": numero_socio(),` ao `json=` e declarar a fixture `numero_socio` na assinatura de cada teste.

`tests/security/test_otp_storage_regression.py` — mesma mudança na chamada da linha 19.

`tests/load/locustfile.py` — o Locust não tem fixtures do pytest, então gera o número inline. Substituir o bloco das linhas 71-80 por:

```python
        res = self.client.post(
            "/api/survey/register",
            json={
                "nome": f"Usuário Carga {random.randint(1, 999999)}",
                "cpf": cpf,
                "telefone": telefone,
                "numero_socio": f"{random.randint(0, 9999):04d}",
                "recaptcha_token": "",
            },
            name="POST /register",
        )
```

Nota para quem for rodar teste de carga: com 4 dígitos há só 10.000 números possíveis e a constraint é única, então em runs longos vão aparecer 400 de número repetido. Isso é comportamento correto do sistema, não falha do teste — o `if res.status_code != 200: return` que já existe na linha seguinte cobre o caso.

- [ ] **Step 12: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS, incluindo os novos `tests/integration/test_numero_socio.py`.

Se `test_numero_socio.py::TestUnicidadeDoNumeroSocio::test_cpf_repetido_continua_com_a_mensagem_de_cpf` falhar dizendo "número de sócio" quando esperava "CPF", a checagem de `check_cpf_available` está sendo pulada ou a ordem das checagens em `register_and_send_otp` foi invertida — CPF é checado primeiro.

- [ ] **Step 13: Verificar que a migration roda de verdade**

Os testes criam o schema por `create_all`, não por Alembic (ver `tests/conftest.py`, fixture `_schema`), então a migration não é exercitada pela suíte. Validar à mão, contra um banco vazio:

Run: `alembic upgrade head && alembic downgrade 002 && alembic upgrade head`
Expected: as três operações completam sem erro. O `downgrade` para `002` desfaz tanto a coluna quanto a renomeação da tabela.

- [ ] **Step 14: Commit**

```bash
git add -A
git commit -m "feat: exige numero de socio no cadastro

Campo de 4 digitos, unico por associado, guardado como texto para
preservar zeros a esquerda. Checado no /register (antes de gastar SMS)
e garantido pela constraint no /submit.

O tratamento de IntegrityError passa a distinguir qual das duas
constraints unicas estourou, senao a mensagem de CPF apareceria para
numero de socio repetido."
```

---

### Task 4: Número de sócio nas saídas de dados

**Files:**
- Modify: `app/schemas/__init__.py` (`WebhookPayload`, `AssociadoResponse`)
- Modify: `app/services/survey_service.py` (montagem do `WebhookPayload` em `submit_vote`; `ExportService.export_csv` e `export_excel`)
- Modify: `app/api/routes/admin.py:180-199` (`search_cpf`)
- Modify: `static/js/admin.js:189-198`
- Modify: `docs/WEBHOOK.md` (exemplo de JSON)
- Test: `tests/integration/test_exportacoes.py` (novo)

**Interfaces:**
- Consumes: `Associado.numero_socio` (Task 3), `WebhookPayload` (Task 1).
- Produces: `WebhookPayload.numero_socio: str`, `AssociadoResponse.numero_socio: str`, coluna `Nº Sócio` em CSV e Excel, chave `numero_socio` no JSON de `/api/admin/search`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/integration/test_exportacoes.py`:

```python
"""
O número de sócio precisa sair nas quatro saídas de dados: CSV, Excel,
webhook e busca do admin. Este arquivo cobre as três que passam por HTTP.
"""

import csv
import io


async def _criar_associado(db_session, nome, cpf, numero, telefone):
    from app.models import Associado, Candidato, Preferencia, Resposta

    candidato = Candidato(nome="Fulano", apelido="Fu", ativo=True)
    db_session.add(candidato)
    await db_session.commit()
    await db_session.refresh(candidato)

    associado = Associado(
        nome=nome,
        cpf=cpf,
        numero_socio=numero,
        telefone=telefone,
        aceite_lgpd=True,
    )
    db_session.add(associado)
    await db_session.commit()
    await db_session.refresh(associado)

    db_session.add(Resposta(associado_id=associado.id, candidato_id=candidato.id))
    db_session.add(
        Preferencia(associado_id=associado.id, candidato_preferido_id=candidato.id)
    )
    await db_session.commit()
    return associado


class TestExportacaoCSV:
    async def test_csv_tem_coluna_de_numero_de_socio(
        self, client, db_session, admin_token, valid_cpf
    ):
        await _criar_associado(db_session, "Socio CSV", valid_cpf(), "0042", "11944440001")

        res = await client.get(
            "/api/admin/export/csv",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200

        linhas = list(csv.reader(io.StringIO(res.text)))
        assert "Nº Sócio" in linhas[0]

        indice = linhas[0].index("Nº Sócio")
        assert linhas[1][indice] == "0042"


class TestExportacaoExcel:
    async def test_excel_tem_coluna_de_numero_de_socio(
        self, client, db_session, admin_token, valid_cpf
    ):
        from openpyxl import load_workbook

        await _criar_associado(db_session, "Socio XLS", valid_cpf(), "0043", "11944440002")

        res = await client.get(
            "/api/admin/export/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200

        wb = load_workbook(io.BytesIO(res.content))
        ws = wb.active
        cabecalho = [c.value for c in ws[1]]
        assert "Nº Sócio" in cabecalho

        indice = cabecalho.index("Nº Sócio")
        assert ws[2][indice].value == "0043"


class TestBuscaDoAdmin:
    async def test_busca_devolve_numero_de_socio(
        self, client, db_session, admin_token, valid_cpf
    ):
        cpf = valid_cpf()
        await _criar_associado(db_session, "Socio Busca", cpf, "0044", "11944440003")

        res = await client.get(
            f"/api/admin/search?cpf={cpf}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        assert res.json()[0]["numero_socio"] == "0044"
```

As rotas usadas acima são as que existem hoje: `@router.get("/export/csv")` em `app/api/routes/admin.py:202` e `@router.get("/export/excel")` em `:216`, ambas sob o prefixo `/api/admin`.

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `pytest tests/integration/test_exportacoes.py -v`
Expected: FAIL — `'Nº Sócio' not in [...]` nos dois primeiros e `KeyError: 'numero_socio'` no terceiro.

- [ ] **Step 3: Acrescentar o campo aos schemas**

Em `app/schemas/__init__.py`:

```python
class AssociadoResponse(BaseModel):
    id: int
    nome: str
    cpf: str
    numero_socio: str
    telefone: str
    data_resposta: datetime
    candidatos: list[str]
    preferido: str

    model_config = {"from_attributes": True}


class WebhookPayload(BaseModel):
    nome: str
    numero_socio: str
    cpf: str
    telefone: str
    candidatos: list[str]
    preferido: str
    aceite_lgpd: bool
    data: str
```

- [ ] **Step 4: Preencher o campo no payload do webhook**

Em `app/services/survey_service.py`, dentro de `submit_vote`, na montagem do payload:

```python
        payload = WebhookPayload(
            nome=associado.nome,
            numero_socio=associado.numero_socio,
            cpf=format_cpf(associado.cpf),
            telefone=associado.telefone,
            candidatos=candidatos_nomes,
            preferido=preferido_nome,
            aceite_lgpd=aceite_lgpd,
            data=associado.data_resposta.isoformat(),
        )
```

- [ ] **Step 5: Acrescentar a coluna às exportações**

Em `app/services/survey_service.py`, classe `ExportService`, nos dois métodos. Cabeçalho e linha, na mesma ordem:

```python
    async def export_csv(self) -> str:
        associados = await self.associado_repo.list_all_with_details()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["ID", "Nº Sócio", "Nome", "CPF", "Telefone", "Candidatos", "Preferido", "Data", "LGPD"]
        )

        for a in associados:
            candidatos = ", ".join(r.candidato.nome for r in a.respostas)
            preferido = a.preferencia.candidato_preferido.nome if a.preferencia else ""
            writer.writerow(
                [
                    a.id,
                    a.numero_socio,
                    a.nome,
                    format_cpf(a.cpf),
                    a.telefone,
                    candidatos,
                    preferido,
                    a.data_resposta.isoformat(),
                    "Sim" if a.aceite_lgpd else "Não",
                ]
            )

        return output.getvalue()
```

```python
    async def export_excel(self) -> bytes:
        associados = await self.associado_repo.list_all_with_details()
        wb = Workbook()
        ws = wb.active
        ws.title = "Respostas"
        ws.append(
            ["ID", "Nº Sócio", "Nome", "CPF", "Telefone", "Candidatos", "Preferido", "Data", "LGPD"]
        )

        for a in associados:
            candidatos = ", ".join(r.candidato.nome for r in a.respostas)
            preferido = a.preferencia.candidato_preferido.nome if a.preferencia else ""
            ws.append(
                [
                    a.id,
                    a.numero_socio,
                    a.nome,
                    format_cpf(a.cpf),
                    a.telefone,
                    candidatos,
                    preferido,
                    a.data_resposta.isoformat(),
                    "Sim" if a.aceite_lgpd else "Não",
                ]
            )

        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()
```

O `numero_socio` é `str` e o openpyxl preserva strings sem converter — `"0042"` não vira `42` na planilha.

- [ ] **Step 6: Acrescentar o campo à busca do admin**

Em `app/api/routes/admin.py`, no dicionário montado por `search_cpf`:

```python
        {
            "id": a.id,
            "nome": a.nome,
            "cpf": a.cpf,
            "numero_socio": a.numero_socio,
            "telefone": a.telefone,
            "data_resposta": a.data_resposta.isoformat(),
            "candidatos": [r.candidato.nome for r in a.respostas],
            "preferido": a.preferencia.candidato_preferido.nome if a.preferencia else None,
        }
```

- [ ] **Step 7: Exibir o número no painel do admin**

Em `static/js/admin.js`, no template do card de resultado (linhas 189-198):

```javascript
    container.innerHTML = results.map(r => `
        <div class="card mb-2">
            <div class="card-body">
                <strong>${escapeHtml(r.nome)}</strong> — Sócio: ${escapeHtml(r.numero_socio)} — CPF: ${escapeHtml(r.cpf)}<br>
                <small class="text-muted">${escapeHtml(r.data_resposta)}</small><br>
                Candidatos: ${escapeHtml(r.candidatos.join(', '))}<br>
                Preferido: ${escapeHtml(r.preferido || '-')}
            </div>
        </div>
    `).join('');
```

Manter o `escapeHtml` — o valor vem do banco e o arquivo já trata todo campo dinâmico assim.

- [ ] **Step 8: Rodar os testes**

Run: `pytest tests/integration/test_exportacoes.py -v`
Expected: PASS

- [ ] **Step 9: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 10: Atualizar o exemplo de JSON na documentação**

Em `docs/WEBHOOK.md`, atualizar o exemplo de payload para incluir o campo novo:

```json
{
  "nome": "Fulano de Tal",
  "numero_socio": "0042",
  "cpf": "123.456.789-00",
  "telefone": "11988887777",
  "candidatos": ["Candidato A", "Candidato B"],
  "preferido": "Candidato A",
  "aceite_lgpd": true,
  "data": "2026-08-06T14:32:10.123456+00:00"
}
```

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: inclui numero de socio nas saidas de dados

CSV, Excel, payload do webhook e busca do admin. Mantido como texto em
todas elas para nao perder zeros a esquerda na planilha."
```

---

### Task 5: Campo no formulário público

**Files:**
- Modify: `templates/index.html:35-44`
- Modify: `static/js/app.js:142-183`
- Test: `tests/integration/test_paginas_html.py`

**Interfaces:**
- Consumes: o endpoint `POST /api/survey/register` já aceitando `numero_socio` (Task 3).
- Produces: input `id="numeroSocio"` na página pública.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `tests/integration/test_paginas_html.py`, dentro de `class TestPaginaPublica`:

```python
    async def test_index_tem_campo_de_numero_de_socio(self, client):
        """O cadastro passou a exigir o número de sócio; se o input sumir do
        template, o formulário quebra com 422 e só apareceria em produção."""
        html = (await client.get("/")).text
        assert 'id="numeroSocio"' in html
        assert 'maxlength="4"' in html
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/integration/test_paginas_html.py::TestPaginaPublica::test_index_tem_campo_de_numero_de_socio -v`
Expected: FAIL com `assert 'id="numeroSocio"' in html`

- [ ] **Step 3: Acrescentar o campo ao formulário**

Em `templates/index.html`, entre o bloco do CPF (que termina na linha 40) e o do telefone (linha 41), inserir:

```html
                            <div class="mb-3">
                                <label for="numeroSocio" class="form-label">Número de Sócio * (4 dígitos)</label>
                                <input type="text" class="form-control form-control-lg" id="numeroSocio" inputmode="numeric" required maxlength="4" minlength="4" pattern="[0-9]{4}" autocomplete="off" placeholder="0000">
                                <div class="invalid-feedback" id="numeroSocioFeedback">Informe os 4 dígitos do seu número de sócio</div>
                            </div>
```

- [ ] **Step 4: Enviar o campo no cadastro**

Em `static/js/app.js`, logo após o listener do telefone (linha 144), acrescentar o filtro de dígitos:

```javascript
document.getElementById('numeroSocio').addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 4);
});
```

E no `submit` do `cadastroForm`, acrescentar a validação local e o campo no corpo:

```javascript
document.getElementById('cadastroForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!cpfValid) {
        showToast('Verifique o CPF antes de continuar');
        return;
    }

    const numeroSocio = document.getElementById('numeroSocio').value;
    if (!/^[0-9]{4}$/.test(numeroSocio)) {
        showToast('Informe os 4 dígitos do seu número de sócio');
        return;
    }

    const btn = document.getElementById('btnCadastro');
    btn.disabled = true;

    try {
        const recaptchaToken = await getRecaptchaToken();
        const res = await fetch('/api/survey/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                nome: document.getElementById('nome').value.trim(),
                cpf: normalizeCPF(cpfInput.value),
                numero_socio: numeroSocio,
                telefone: normalizePhone(document.getElementById('telefone').value),
                recaptcha_token: recaptchaToken,
            }),
        });
```

O restante do handler (linhas 170-183) fica inalterado. O `showToast` do `catch` já exibe a mensagem de número repetido que vem do backend, porque a rota devolve `detail` e a linha 171 usa `data.detail`.

Não acrescentar checagem remota de disponibilidade: são 4 dígitos, e um endpoint de consulta seria enumerável por inteiro em 10.000 requisições, revelando quais sócios já votaram.

- [ ] **Step 5: Rodar os testes de página**

Run: `pytest tests/integration/test_paginas_html.py -v`
Expected: PASS

- [ ] **Step 6: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 7: Conferir o fluxo no navegador**

Subir a aplicação (`docker compose up` ou `uvicorn app.main:app --reload`), abrir `/` e confirmar:

1. o campo aparece entre CPF e celular;
2. digitar letras não escreve nada, e o campo para de aceitar no quarto dígito;
3. enviar com 3 dígitos mostra o toast e não faz requisição;
4. um cadastro completo chega até a etapa de OTP.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: campo de numero de socio no formulario publico

Input de 4 digitos entre CPF e celular, com filtro de digitos no
cliente. Sem checagem remota de disponibilidade: 4 digitos sao
enumeraveis em 10 mil requisicoes."
```

---

## Verificação final

- [ ] `pytest -q` verde na suíte inteira
- [ ] `grep -rn -i pipefy app/ tests/ docs/ README.md` devolve só as ocorrências dentro de `alembic/versions/003_rename_pipefy_to_webhook.py`
- [ ] `alembic upgrade head` roda limpo contra banco vazio
- [ ] Fluxo público completo testado no navegador, do cadastro ao voto
- [ ] CSV exportado abre com a coluna `Nº Sócio` preenchida e com zeros à esquerda preservados
