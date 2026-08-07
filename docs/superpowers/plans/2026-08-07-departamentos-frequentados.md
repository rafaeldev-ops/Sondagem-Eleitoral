# Modalidades/departamentos frequentados — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Acrescentar uma etapa obrigatória de seleção múltipla de modalidades/departamentos frequentados entre a escolha de candidatos e o consentimento LGPD, e fazer a resposta sair nas quatro saídas de dados.

**Architecture:** Duas tabelas novas (`departamentos` e a associativa `associado_departamentos`) espelhando o par `Candidato`/`Resposta` que já existe, mais uma coluna nullable `departamento_outros` em `associados`. A lista é populada por migration. A validação se divide entre o schema Pydantic (forma) e o `SurveyService` (semântica), porque a regra do "Outros" depende de dado do banco.

**Tech Stack:** FastAPI, SQLAlchemy 2 (async, `Mapped`/`mapped_column`), Alembic, Pydantic v2 (`field_validator`), Postgres (asyncpg), Redis, pytest + pytest-asyncio, openpyxl, Jinja2, JS vanilla, Bootstrap 5.

## Global Constraints

- Spec de referência: `docs/superpowers/specs/2026-08-07-departamentos-frequentados-design.md`.
- Branch de trabalho: `feature/departamentos-frequentados`, saindo de `main`.
- Toda constraint única é **nomeada explicitamente** no modelo (`uq_departamentos_nome`, `uq_associado_departamento`): `Base.metadata` não tem `naming_convention`, então sem nome explícito `create_all` (testes) e Alembic (produção) divergem.
- Índices de chave estrangeira são declarados **no modelo** com `index=True`, nunca só na migration — a divergência entre os dois ambientes já custou a migration `005`.
- Os 48 nomes são gravados **exatamente** como na lista do seed, com a grafia e a pontuação originais (travessão `–` U+2013, "Ginastica" sem acento, "Tenis de mesa", "Volei Masculino"). Não corrigir grafia.
- A ordem do seed **não é alfabética** e não deve ser reordenada: no bloco de ginástica, "Rítmica" vem antes de "Feminina / Fitness". É deliberado.
- Mensagens de UI e de erro em português.
- **Como rodar os testes:** o host não tem Python 3.12. Construir uma vez a imagem de teste e reusá-la:

  ```bash
  docker compose up -d db redis
  docker build -f Dockerfile.test -t sondagem-test:latest .   # ver nota abaixo
  docker run --rm --network sondagem-eleitoral_default \
    -v "$PWD:/app" -w /app \
    -e DATABASE_URL="postgresql+asyncpg://sondagemtest:sondagemtest@db:5432/sondagem_test" \
    -e DATABASE_URL_SYNC="postgresql+psycopg2://sondagemtest:sondagemtest@db:5432/sondagem_test" \
    -e REDIS_URL="redis://:testpass123@redis:6379/1" \
    sondagem-test:latest pytest -q
  ```

  `Dockerfile.test` é `FROM python:3.12-slim` + `apt-get install gcc libpq-dev` + `pip install -r requirements-dev.txt`. Onde o plano diz `pytest ...`, use esse `docker run` trocando o comando final.
- **`docker compose exec app alembic ...` usa o código copiado na imagem, não o do disco.** Depois de criar ou editar migration, ou `docker compose up -d --build app`, ou rodar o `alembic` pela imagem de teste com o código montado. Sem isso o `upgrade head` vira no-op silencioso.

---

### Task 1: Modelo, migration e seed

Cria o schema. Sem comportamento novo ainda — o critério de pronto é a suíte existente continuar verde e a migration subir e descer limpa.

**Files:**
- Modify: `app/models/__init__.py` (classe `Associado`; duas classes novas ao fim)
- Create: `alembic/versions/006_add_departamentos.py`
- Test: `tests/unit/test_modelos_departamento.py` (novo)

**Interfaces:**
- Consumes: nada (primeira task).
- Produces: `Departamento` (tabela `departamentos`; campos `id`, `nome`, `ordem`, `exige_texto`, `ativo`, `created_at`; relação `associados`), `AssociadoDepartamento` (tabela `associado_departamentos`; campos `id`, `associado_id`, `departamento_id`, `created_at`; relações `associado` e `departamento`), `Associado.departamento_outros: str | None`, `Associado.departamentos: list[AssociadoDepartamento]`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_modelos_departamento.py`:

```python
"""
O schema dos departamentos é criado por Alembic em produção e por
create_all nos testes. Estes testes travam os nomes de constraint e a
presença dos índices, que são exatamente o que diverge entre os dois
ambientes quando alguém esquece de declarar no modelo.
"""

from app.models import Associado, AssociadoDepartamento, Departamento


class TestTabelaDepartamentos:
    def test_nome_da_tabela(self):
        assert Departamento.__tablename__ == "departamentos"

    def test_constraint_unica_tem_nome_explicito(self):
        nomes = {c.name for c in Departamento.__table__.constraints if c.name}
        assert "uq_departamentos_nome" in nomes

    def test_colunas_esperadas(self):
        colunas = set(Departamento.__table__.columns.keys())
        assert colunas == {"id", "nome", "ordem", "exige_texto", "ativo", "created_at"}


class TestTabelaAssociativa:
    def test_nome_da_tabela(self):
        assert AssociadoDepartamento.__tablename__ == "associado_departamentos"

    def test_constraint_unica_tem_nome_explicito(self):
        nomes = {c.name for c in AssociadoDepartamento.__table__.constraints if c.name}
        assert "uq_associado_departamento" in nomes

    def test_as_duas_fks_sao_indexadas(self):
        """Postgres não indexa coluna de FK sozinho, e a exportação faz join
        pelas duas. Declarado no modelo para que create_all e Alembic gerem
        o mesmo schema."""
        indexadas = {
            col.name
            for idx in AssociadoDepartamento.__table__.indexes
            for col in idx.columns
        }
        assert indexadas == {"associado_id", "departamento_id"}


class TestColunaEmAssociado:
    def test_departamento_outros_e_nullable(self):
        coluna = Associado.__table__.columns["departamento_outros"]
        assert coluna.nullable is True
        assert coluna.type.length == 100
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/unit/test_modelos_departamento.py -v`
Expected: FAIL com `ImportError: cannot import name 'AssociadoDepartamento' from 'app.models'`

- [ ] **Step 3: Acrescentar a coluna e a relação em `Associado`**

Em `app/models/__init__.py`, dentro da classe `Associado`, acrescentar a coluna logo após `aceite_lgpd` e a relação junto das outras duas:

```python
    # Preenchido só por quem marcou a modalidade que exige texto ("Outros").
    # Um texto por pessoa, não por modalidade — daí ficar aqui e não em
    # AssociadoDepartamento.
    departamento_outros: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

```python
    departamentos: Mapped[list["AssociadoDepartamento"]] = relationship(
        back_populates="associado"
    )
```

- [ ] **Step 4: Acrescentar as duas classes novas**

Ao fim de `app/models/__init__.py`:

```python
class Departamento(Base):
    __tablename__ = "departamentos"
    __table_args__ = (UniqueConstraint("nome", name="uq_departamentos_nome"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    # Ordem de exibição explícita, e não ORDER BY nome: a collation do banco
    # (en_US.utf8) ordena "Volei Masculino" longe dos outros dois "Vôlei", e o
    # resultado pode variar entre desenvolvimento e produção. A ordem é um
    # dado, não um efeito colateral do ambiente.
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    # True só para "Outros". Marca a opção que exige texto complementar; o
    # serviço consulta esta coluna em vez de comparar nome == "Outros", que
    # quebraria silenciosamente numa renomeação ou correção de acento.
    exige_texto: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    associados: Mapped[list["AssociadoDepartamento"]] = relationship(
        back_populates="departamento"
    )


class AssociadoDepartamento(Base):
    __tablename__ = "associado_departamentos"
    __table_args__ = (
        UniqueConstraint(
            "associado_id", "departamento_id", name="uq_associado_departamento"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # index=True nas duas: Postgres não indexa FK sozinho e a exportação faz
    # join pelas duas. No modelo, não só na migration, para que create_all
    # (testes) e Alembic (produção) concordem.
    associado_id: Mapped[int] = mapped_column(
        ForeignKey("associados.id"), nullable=False, index=True
    )
    departamento_id: Mapped[int] = mapped_column(
        ForeignKey("departamentos.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    associado: Mapped["Associado"] = relationship(back_populates="departamentos")
    departamento: Mapped["Departamento"] = relationship(back_populates="associados")
```

Todos os nomes do SQLAlchemy usados acima (`Boolean`, `DateTime`, `ForeignKey`, `Integer`, `String`, `UniqueConstraint`, `func`) já estão no import da linha 3 do arquivo. Nenhum import novo é necessário.

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `pytest tests/unit/test_modelos_departamento.py -v`
Expected: PASS (todos)

- [ ] **Step 6: Criar a migration com o seed**

Criar `alembic/versions/006_add_departamentos.py`:

```python
"""Add departamentos, associado_departamentos and departamento_outros

Revision ID: 006
Revises: 005
Create Date: 2026-08-07

Ao contrário da 004, esta migration NÃO exige que associados esteja vazia:
as duas tabelas são novas e a coluna nova é nullable. Roda com a sondagem
em produção e votos já coletados.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Lista oficial do clube. A ordem NÃO é alfabética e não deve ser
# reordenada: no bloco de ginástica, "Rítmica" vem antes de "Feminina /
# Fitness". A grafia também é a original ("Ginastica" sem acento, "Tenis de
# mesa", "Volei Masculino") e o separador é o travessão U+2013.
MODALIDADES = [
    "Academia / Musculação",
    "Atletismo",
    "Basquete – Feminino",
    "Basquete – Masculino",
    "Beach Tennis",
    "Biribol",
    "Boxe",
    "Capoeira",
    "Carteado",
    "COD",
    "COTI",
    "Esportes Amadores",
    "FAVA",
    "Fitness / Dança",
    "Futebol de Mesa",
    "Futebol Social – Feminino",
    "Futebol Social – Masculino",
    "Futebol Social – Menores",
    "Futebol Society – Feminino",
    "Futebol Society – Masculino",
    "Futevôlei",
    "Ginastica Aeróbica",
    "Ginastica Artística",
    "Ginastica Rítmica",
    "Ginastica Feminina / Fitness",
    "Handebol",
    "Hidroginástica",
    "Jiu Jitsu",
    "Judô",
    "Karaokê",
    "Kickboxing",
    "Natação",
    "Paddle",
    "Patinação",
    "Pickleball",
    "Piscina",
    "Polo Aquático",
    "Sauna",
    "Sinuca",
    "Social",
    "Tai Chi Chuan",
    "Teatro",
    "Tenis de mesa",
    "Tennis",
    "Triathlon",
    "Vôlei de Praia",
    "Vôlei Feminino",
    "Volei Masculino",
]


def upgrade() -> None:
    op.create_table(
        "departamentos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nome", sa.String(length=100), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("exige_texto", sa.Boolean(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nome", name="uq_departamentos_nome"),
    )

    op.create_table(
        "associado_departamentos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("associado_id", sa.Integer(), nullable=False),
        sa.Column("departamento_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["associado_id"], ["associados.id"]),
        sa.ForeignKeyConstraint(["departamento_id"], ["departamentos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "associado_id", "departamento_id", name="uq_associado_departamento"
        ),
    )
    op.create_index(
        "ix_associado_departamentos_associado_id",
        "associado_departamentos",
        ["associado_id"],
    )
    op.create_index(
        "ix_associado_departamentos_departamento_id",
        "associado_departamentos",
        ["departamento_id"],
    )

    op.add_column(
        "associados",
        sa.Column("departamento_outros", sa.String(length=100), nullable=True),
    )

    departamentos = sa.table(
        "departamentos",
        sa.column("nome", sa.String),
        sa.column("ordem", sa.Integer),
        sa.column("exige_texto", sa.Boolean),
        sa.column("ativo", sa.Boolean),
    )
    op.bulk_insert(
        departamentos,
        [
            {"nome": nome, "ordem": i, "exige_texto": False, "ativo": True}
            for i, nome in enumerate(MODALIDADES, start=1)
        ]
        + [{"nome": "Outros", "ordem": 999, "exige_texto": True, "ativo": True}],
    )


def downgrade() -> None:
    op.drop_column("associados", "departamento_outros")
    op.drop_index(
        "ix_associado_departamentos_departamento_id",
        table_name="associado_departamentos",
    )
    op.drop_index(
        "ix_associado_departamentos_associado_id", table_name="associado_departamentos"
    )
    # A associativa primeiro: ela é quem tem as FKs.
    op.drop_table("associado_departamentos")
    op.drop_table("departamentos")
```

Os nomes de índice acima (`ix_associado_departamentos_associado_id` e `ix_associado_departamentos_departamento_id`) são exatamente os que o SQLAlchemy gera a partir de `index=True` no modelo — é isso que mantém os dois ambientes iguais.

- [ ] **Step 7: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS. Nada de comportamento mudou ainda; falha aqui é erro de digitação no modelo.

- [ ] **Step 8: Verificar a migration contra um banco vazio**

A suíte usa `create_all` e **não** exercita a migration, então o seed precisa de verificação manual.

```bash
docker compose exec -T db psql -U sondagemtest -d postgres \
  -c "DROP DATABASE IF EXISTS sondagem_migcheck;" \
  -c "CREATE DATABASE sondagem_migcheck OWNER sondagemtest;"

docker run --rm --network sondagem-eleitoral_default -v "$PWD:/app" -w /app \
  -e DATABASE_URL="postgresql+asyncpg://sondagemtest:sondagemtest@db:5432/sondagem_migcheck" \
  -e DATABASE_URL_SYNC="postgresql+psycopg2://sondagemtest:sondagemtest@db:5432/sondagem_migcheck" \
  -e REDIS_URL="redis://:testpass123@redis:6379/1" \
  -e SECRET_KEY=x -e ADMIN_USERNAME=a -e ADMIN_PASSWORD=b -e APP_ENV=development \
  sondagem-test:latest sh -c "alembic upgrade head && alembic downgrade 005 && alembic upgrade head"
```

Expected: as três operações completam sem erro.

Depois, conferir o seed:

```bash
docker compose exec -T db psql -U sondagemtest -d sondagem_migcheck -c \
  "SELECT count(*) AS total, count(*) FILTER (WHERE exige_texto) AS exigem_texto FROM departamentos;"
docker compose exec -T db psql -U sondagemtest -d sondagem_migcheck -c \
  "SELECT nome FROM departamentos ORDER BY ordem OFFSET 21 LIMIT 4;"
```

Expected: `total = 49`, `exigem_texto = 1`. A segunda consulta devolve, nesta ordem: `Ginastica Aeróbica`, `Ginastica Artística`, `Ginastica Rítmica`, `Ginastica Feminina / Fitness` — confirmando que a inversão deliberada sobreviveu ao seed.

Ao terminar: `docker compose exec -T db psql -U sondagemtest -d postgres -c "DROP DATABASE sondagem_migcheck;"`

- [ ] **Step 9: Commit**

```bash
git add app/models/__init__.py alembic/versions/006_add_departamentos.py tests/unit/test_modelos_departamento.py
git commit -m "feat: tabelas de departamentos e seed das 48 modalidades

Duas tabelas novas espelhando o par Candidato/Resposta, mais a coluna
nullable departamento_outros em associados.

A ordem de exibicao e uma coluna e nao ORDER BY nome: sob a collation
en_US.utf8 do banco, ordenar pelo nome separa Volei Masculino dos outros
dois Volei. A opcao que exige texto e marcada por booleano e nao por
comparacao com o nome Outros.

Diferente da 004, esta migration nao exige tabela vazia."
```

---

### Task 2: Repositório e endpoint público

**Files:**
- Modify: `app/repositories/__init__.py:5` (import), e classe nova após `CandidatoRepository`
- Modify: `app/api/routes/survey.py:1-30` (imports), e rota nova após `list_candidatos` (linha 138)
- Modify: `tests/conftest.py` (fixture nova)
- Test: `tests/integration/test_departamentos.py` (novo)

**Interfaces:**
- Consumes: `Departamento` (Task 1).
- Produces: `DepartamentoRepository(db)` com `list_active() -> list[Departamento]` (ordenado por `ordem`); rota `GET /api/survey/departamentos` devolvendo `list[dict]` com chaves `id` e `nome`; fixture pytest `departamentos` que devolve um callable `async (quantos: int = 3, com_outros: bool = True) -> list[Departamento]`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/integration/test_departamentos.py`:

```python
"""
A lista de modalidades é populada por migration, mas os testes criam o
schema com create_all e NÃO rodam migrations — então aqui cada teste cria
as suas próprias linhas, como já se faz com candidatos.
"""


class TestListagemDeDepartamentos:
    async def test_lista_apenas_ativos(self, client, departamentos):
        await departamentos(quantos=3, com_outros=False)

        res = await client.get("/api/survey/departamentos")
        assert res.status_code == 200
        assert len(res.json()) == 3
        assert {"id", "nome"} == set(res.json()[0].keys())

    async def test_respeita_a_coluna_ordem_e_nao_o_nome(self, client, db_session):
        """A ordem é um dado, não alfabética: sob a collation do banco,
        ORDER BY nome devolveria outra sequência."""
        from app.models import Departamento

        db_session.add_all(
            [
                Departamento(nome="Zumba", ordem=1, ativo=True),
                Departamento(nome="Atletismo", ordem=2, ativo=True),
                Departamento(nome="Outros", ordem=999, exige_texto=True, ativo=True),
            ]
        )
        await db_session.commit()

        nomes = [d["nome"] for d in (await client.get("/api/survey/departamentos")).json()]
        assert nomes == ["Zumba", "Atletismo", "Outros"]

    async def test_inativo_nao_aparece(self, client, db_session):
        from app.models import Departamento

        db_session.add_all(
            [
                Departamento(nome="Ativa", ordem=1, ativo=True),
                Departamento(nome="Encerrada", ordem=2, ativo=False),
            ]
        )
        await db_session.commit()

        nomes = [d["nome"] for d in (await client.get("/api/survey/departamentos")).json()]
        assert nomes == ["Ativa"]
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/integration/test_departamentos.py -v`
Expected: FAIL — `fixture 'departamentos' not found` no primeiro e 404 nos outros dois.

- [ ] **Step 3: Acrescentar a fixture de departamentos**

Em `tests/conftest.py`, logo após a fixture `numero_socio`, acrescentar:

```python
@pytest_asyncio.fixture
async def departamentos(db_session):
    """
    Cria departamentos para o teste. O seed real vive na migration 006, que
    a suíte não roda (o schema vem de create_all) — então cada teste que
    precisa de modalidades cria as suas.

    Devolve os objetos já com id, na ordem em que foram criados; quando
    com_outros=True o último é a opção que exige texto complementar.
    """

    async def create(quantos: int = 3, com_outros: bool = True):
        from app.models import Departamento

        criados = [
            Departamento(nome=f"Modalidade {i}", ordem=i, ativo=True)
            for i in range(1, quantos + 1)
        ]
        if com_outros:
            criados.append(
                Departamento(nome="Outros", ordem=999, exige_texto=True, ativo=True)
            )

        db_session.add_all(criados)
        await db_session.commit()
        for d in criados:
            await db_session.refresh(d)
        return criados

    return create
```

- [ ] **Step 4: Acrescentar o repositório**

Em `app/repositories/__init__.py`, acrescentar `Departamento` ao import da linha 5:

```python
from app.models import (
    Associado,
    AuditLog,
    Candidato,
    Departamento,
    Preferencia,
    Resposta,
    WebhookLog,
)
```

**Só `Departamento`.** `AssociadoDepartamento` também será necessário neste arquivo, mas apenas na Task 4 (no `selectinload`) — importá-lo agora deixaria um import sem uso, e o `ruff` do projeto seleciona `["E", "F", "W"]`, então F401 reprova o lint.

E, após a classe `CandidatoRepository`:

```python
class DepartamentoRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_active(self) -> list[Departamento]:
        result = await self.db.execute(
            select(Departamento)
            .where(Departamento.ativo.is_(True))
            .order_by(Departamento.ordem)
        )
        return list(result.scalars().all())
```

- [ ] **Step 5: Acrescentar a rota**

Em `app/api/routes/survey.py`, acrescentar `DepartamentoRepository` ao import de repositórios que já existe no topo do arquivo, e a rota após `list_candidatos` (que termina na linha 138):

```python
@router.get("/departamentos")
async def list_departamentos(db: AsyncSession = Depends(get_db)) -> list[dict]:
    repo = DepartamentoRepository(db)
    departamentos = await repo.list_active()
    return [{"id": d.id, "nome": d.nome} for d in departamentos]
```

Sem `@limiter.limit`: é a mesma decisão de `/candidatos`, que também não tem — são dados públicos e não sensíveis, servidos uma vez por carregamento de página.

- [ ] **Step 6: Rodar os testes**

Run: `pytest tests/integration/test_departamentos.py -v`
Expected: PASS

- [ ] **Step 7: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/repositories/__init__.py app/api/routes/survey.py tests/conftest.py tests/integration/test_departamentos.py
git commit -m "feat: endpoint publico de modalidades

GET /api/survey/departamentos devolve as ativas ordenadas pela coluna
ordem, espelhando /candidatos. A fixture de teste cria as proprias
modalidades porque a suite usa create_all e nao roda a migration do seed."
```

---

### Task 3: Gravação no submit

O ponto de virada: assim que `departamentos_ids` vira obrigatório, **todo teste que chama `/submit` quebra** até ser atualizado. Por isso a atualização deles faz parte desta task.

**Files:**
- Modify: `app/schemas/__init__.py` (`VotoRequest`, linhas 88-99)
- Modify: `app/services/survey_service.py` (imports; `__init__`; `submit_vote` linhas 160-254)
- Modify: `app/api/routes/survey.py:150-157` (passar os campos adiante)
- Modify: `tests/integration/test_submit.py` (helper `_register_verify` e chamadas diretas)
- Modify: `tests/integration/test_numero_socio.py` (a chamada de `/submit` em `TestNumeroSocioPersistido`)
- Modify: `tests/load/locustfile.py`
- Test: `tests/integration/test_departamentos.py` (acrescentar casos)

**Interfaces:**
- Consumes: `Departamento`, `AssociadoDepartamento` (Task 1); `DepartamentoRepository.list_active()` (Task 2); fixture `departamentos` (Task 2).
- Produces: `VotoRequest.departamentos_ids: list[int]` e `VotoRequest.departamento_outros: str`; `SurveyService.submit_vote(..., departamentos_ids: list[int], departamento_outros: str, ...)` com os dois campos como parâmetros nomeados antes de `ip`.

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar ao fim de `tests/integration/test_departamentos.py`:

```python
class TestSubmitComDepartamentos:
    async def test_submit_sem_nenhuma_modalidade_e_recusado(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code, departamentos
    ):
        await departamentos()
        token, cid = await _preparar_voto(
            client, db_session, valid_cpf, numero_socio, read_otp_code, "11933330001"
        )

        res = await client.post(
            "/api/survey/submit",
            json={
                "session_token": token,
                "candidatos_ids": [cid],
                "candidato_preferido_id": cid,
                "departamentos_ids": [],
                "aceite_lgpd": True,
            },
        )
        assert res.status_code == 422

    async def test_modalidade_inexistente_e_recusada(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code, departamentos
    ):
        await departamentos()
        token, cid = await _preparar_voto(
            client, db_session, valid_cpf, numero_socio, read_otp_code, "11933330002"
        )

        res = await client.post(
            "/api/survey/submit",
            json={
                "session_token": token,
                "candidatos_ids": [cid],
                "candidato_preferido_id": cid,
                "departamentos_ids": [999999],
                "aceite_lgpd": True,
            },
        )
        assert res.status_code == 400
        assert "modalidade" in res.json()["detail"].lower()

    async def test_modalidade_inativa_e_recusada(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code
    ):
        from app.models import Departamento

        inativa = Departamento(nome="Encerrada", ordem=1, ativo=False)
        db_session.add(inativa)
        await db_session.commit()
        await db_session.refresh(inativa)

        token, cid = await _preparar_voto(
            client, db_session, valid_cpf, numero_socio, read_otp_code, "11933330003"
        )

        res = await client.post(
            "/api/survey/submit",
            json={
                "session_token": token,
                "candidatos_ids": [cid],
                "candidato_preferido_id": cid,
                "departamentos_ids": [inativa.id],
                "aceite_lgpd": True,
            },
        )
        assert res.status_code == 400

    async def test_outros_sem_texto_e_recusado(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code, departamentos
    ):
        deps = await departamentos()
        outros = deps[-1]
        token, cid = await _preparar_voto(
            client, db_session, valid_cpf, numero_socio, read_otp_code, "11933330004"
        )

        res = await client.post(
            "/api/survey/submit",
            json={
                "session_token": token,
                "candidatos_ids": [cid],
                "candidato_preferido_id": cid,
                "departamentos_ids": [outros.id],
                "departamento_outros": "   ",
                "aceite_lgpd": True,
            },
        )
        assert res.status_code == 400
        assert "outros" in res.json()["detail"].lower()

    async def test_caminho_feliz_grava_as_modalidades_e_o_texto(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code, departamentos
    ):
        from sqlalchemy import select

        from app.models import Associado

        deps = await departamentos()
        escolhidas = [deps[0].id, deps[-1].id]
        token, cid = await _preparar_voto(
            client, db_session, valid_cpf, numero_socio, read_otp_code, "11933330005"
        )

        res = await client.post(
            "/api/survey/submit",
            json={
                "session_token": token,
                "candidatos_ids": [cid],
                "candidato_preferido_id": cid,
                "departamentos_ids": escolhidas,
                "departamento_outros": "Xadrez",
                "aceite_lgpd": True,
            },
        )
        assert res.status_code == 200

        result = await db_session.execute(
            select(Associado).where(Associado.telefone == "11933330005")
        )
        associado = result.scalar_one()
        await db_session.refresh(associado, ["departamentos"])
        assert {d.departamento_id for d in associado.departamentos} == set(escolhidas)
        assert associado.departamento_outros == "Xadrez"

    async def test_texto_sem_outros_marcado_e_descartado(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code, departamentos
    ):
        """Não se guarda texto órfão de quem preencheu o campo e depois
        desmarcou "Outros"."""
        from sqlalchemy import select

        from app.models import Associado

        deps = await departamentos()
        token, cid = await _preparar_voto(
            client, db_session, valid_cpf, numero_socio, read_otp_code, "11933330006"
        )

        res = await client.post(
            "/api/survey/submit",
            json={
                "session_token": token,
                "candidatos_ids": [cid],
                "candidato_preferido_id": cid,
                "departamentos_ids": [deps[0].id],
                "departamento_outros": "Xadrez",
                "aceite_lgpd": True,
            },
        )
        assert res.status_code == 200

        result = await db_session.execute(
            select(Associado).where(Associado.telefone == "11933330006")
        )
        assert result.scalar_one().departamento_outros is None
```

E acrescentar, no **topo** do mesmo arquivo (logo abaixo do docstring), o helper usado por esses testes:

```python
async def _preparar_voto(
    client, db_session, valid_cpf, numero_socio, read_otp_code, telefone
):
    """Cria um candidato e leva a sessão até logo antes do /submit,
    devolvendo (session_token, candidato_id)."""
    from app.models import Candidato

    candidato = Candidato(nome="Fulano", apelido="Fu", ativo=True)
    db_session.add(candidato)
    await db_session.commit()
    await db_session.refresh(candidato)

    res = await client.post(
        "/api/survey/register",
        json={
            "nome": "Socio Modalidades",
            "cpf": valid_cpf(),
            "telefone": telefone,
            "numero_socio": numero_socio(),
            "recaptcha_token": "",
        },
    )
    token = res.json()["session_token"]
    codigo = read_otp_code(telefone)
    await client.post(
        "/api/survey/verify-otp",
        json={"session_token": token, "telefone": telefone, "codigo": codigo},
    )
    return token, candidato.id
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `pytest tests/integration/test_departamentos.py -v`
Expected: FAIL — os testes de recusa devolvem 200 em vez de 422/400 (o campo ainda não existe e o Pydantic o ignora), e os dois últimos falham ao ler `associado.departamentos`.

- [ ] **Step 3: Acrescentar os campos ao schema**

Em `app/schemas/__init__.py`, a classe `VotoRequest` (linhas 88-99) passa a ser:

```python
class VotoRequest(BaseModel):
    session_token: str
    candidatos_ids: list[int] = Field(min_length=1, max_length=20)
    candidato_preferido_id: int
    # min_length=1 é a obrigatoriedade da etapa; max_length=49 é o total de
    # opções da lista e existe só para recusar payload absurdo.
    departamentos_ids: list[int] = Field(min_length=1, max_length=49)
    departamento_outros: str = Field(default="", max_length=100)
    aceite_lgpd: bool

    @field_validator("aceite_lgpd")
    @classmethod
    def validate_lgpd(cls, value: bool) -> bool:
        if not value:
            raise ValueError("É necessário aceitar os termos da LGPD")
        return value

    @field_validator("departamento_outros")
    @classmethod
    def sanitize_departamento_outros(cls, value: str) -> str:
        # Primeiro texto livre do fluxo público — mesmo tratamento que o nome.
        return sanitize_text(value, 100)
```

A regra "se `Outros` está marcado, o texto é obrigatório" **não entra aqui**: o Pydantic não tem acesso ao banco e não sabe qual id exige texto. Ela vive no serviço.

- [ ] **Step 4: Levar os campos pelo serviço**

Em `app/services/survey_service.py`:

Acrescentar aos imports de models e repositories:

```python
from app.models import (
    Associado,
    AssociadoDepartamento,
    Preferencia,
    Resposta,
    WebhookLog,
)
from app.repositories import (
    AssociadoRepository,
    AuditLogRepository,
    CandidatoRepository,
    DepartamentoRepository,
    PreferenciaRepository,
    RespostaRepository,
    WebhookLogRepository,
)
```

(Os nomes exatos já presentes no arquivo permanecem; acrescente `AssociadoDepartamento` e `DepartamentoRepository` às listas existentes.)

No `__init__` da classe, junto dos outros repositórios:

```python
        self.departamento_repo = DepartamentoRepository(db)
```

A assinatura de `submit_vote` passa a ser:

```python
    async def submit_vote(
        self,
        session_token: str,
        candidatos_ids: list[int],
        candidato_preferido_id: int,
        departamentos_ids: list[int],
        departamento_outros: str,
        aceite_lgpd: bool,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[bool, str | None]:
```

Logo após o bloco que valida o candidato preferencial (que termina na linha 195 com `return False, "O candidato preferencial deve estar entre os selecionados"`), acrescentar:

```python
        departamentos = await self.departamento_repo.list_active()
        departamento_map = {d.id: d for d in departamentos}

        if not all(did in departamento_map for did in departamentos_ids):
            return False, "Modalidade inválida"

        # Qual opção exige texto é dado do banco (coluna exige_texto), não o
        # nome "Outros" nem um id cravado no código.
        exige_texto = any(
            departamento_map[did].exige_texto for did in departamentos_ids
        )
        texto_outros = departamento_outros.strip()

        if exige_texto and not texto_outros:
            return False, "Descreva qual modalidade em Outros"

        # Sem a opção que exige texto, o campo é descartado: não se guarda
        # texto órfão de quem preencheu e depois desmarcou.
        if not exige_texto:
            texto_outros = ""
```

Na construção do `Associado` (linhas 197-205), acrescentar o campo:

```python
        associado = Associado(
            nome=session["nome"],
            cpf=cpf,
            numero_socio=numero_socio,
            telefone=session["telefone"],
            departamento_outros=texto_outros or None,
            ip=ip or session.get("ip"),
            user_agent=user_agent or session.get("user_agent"),
            aceite_lgpd=aceite_lgpd,
        )
```

E logo após o `await self.preferencia_repo.create(...)` (que termina na linha 229), gravar a associativa — na mesma transação, antes de qualquer commit:

```python
        self.db.add_all(
            [
                AssociadoDepartamento(
                    associado_id=associado.id, departamento_id=did
                )
                for did in departamentos_ids
            ]
        )
        await self.db.flush()
```

- [ ] **Step 5: Passar os campos na rota**

Em `app/api/routes/survey.py`, na chamada de `service.submit_vote` (linhas 150-157):

```python
    success, error = await service.submit_vote(
        session_token=body.session_token,
        candidatos_ids=body.candidatos_ids,
        candidato_preferido_id=body.candidato_preferido_id,
        departamentos_ids=body.departamentos_ids,
        departamento_outros=body.departamento_outros,
        aceite_lgpd=body.aceite_lgpd,
        ip=await get_client_ip(request),
        user_agent=get_user_agent(request),
    )
```

- [ ] **Step 6: Rodar os testes novos**

Run: `pytest tests/integration/test_departamentos.py -v`
Expected: PASS

- [ ] **Step 7: Atualizar os testes existentes que chamam /submit**

Rodar `pytest -q` agora mostra quais quebraram. Os três lugares:

`tests/integration/test_submit.py` — o helper `_register_verify` já entrega a sessão; o que muda é cada `POST /api/survey/submit`. Em **todos** eles, acrescentar ao corpo:

```python
                "departamentos_ids": [dep.id],
```

onde `dep` vem da fixture. Cada teste que chama `/submit` passa a declarar `departamentos` na assinatura e a criar a modalidade antes:

```python
    deps = await departamentos(quantos=1, com_outros=False)
    dep = deps[0]
```

`tests/integration/test_numero_socio.py` — mesma mudança na chamada de `/submit` dentro de `TestNumeroSocioPersistido::test_numero_chega_ao_banco_com_zeros_a_esquerda`: declarar a fixture `departamentos`, criar uma modalidade e acrescentar `"departamentos_ids": [dep.id]` ao corpo.

`tests/load/locustfile.py` — o Locust não tem fixtures. No `on_start` (ou equivalente), buscar a lista uma vez e guardar os ids:

```python
        res = self.client.get("/api/survey/departamentos", name="GET /departamentos")
        self.departamento_ids = [d["id"] for d in res.json()] if res.status_code == 200 else []
```

E no corpo do `/submit`:

```python
                "departamentos_ids": self.departamento_ids[:1] or [1],
```

Nota para quem rodar carga: se o banco de carga não tiver passado pela migration 006, `/departamentos` volta vazio e o `or [1]` faz o submit ser recusado com 400 — o que é comportamento correto do sistema, não falha do teste.

- [ ] **Step 8: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: exige modalidades frequentadas no envio do voto

Ao menos uma modalidade por voto, gravada na associativa dentro da mesma
transacao do voto. A opcao que exige texto complementar e reconhecida pela
coluna exige_texto, nao pelo nome — e o texto e descartado quando essa
opcao nao esta entre as selecionadas.

A validacao se divide: forma no VotoRequest, semantica no servico, porque
saber qual id exige texto depende do banco."
```

---

### Task 4: Saídas de dados

**Files:**
- Modify: `app/repositories/__init__.py` (`search_by_cpf` e `list_all_with_details`, linhas 37-59)
- Modify: `app/schemas/__init__.py` (`WebhookPayload`, `AssociadoResponse`)
- Modify: `app/services/survey_service.py` (montagem do payload; `ExportService.export_csv` e `export_excel`, linhas ~305-360)
- Modify: `app/api/routes/admin.py` (dicionário de `search_cpf`)
- Modify: `static/js/admin.js` (card de resultado)
- Modify: `docs/WEBHOOK.md` (exemplo de JSON)
- Test: `tests/integration/test_exportacoes.py` (acrescentar casos)

**Interfaces:**
- Consumes: `Associado.departamentos`, `Associado.departamento_outros` (Task 1); gravação funcionando (Task 3).
- Produces: `WebhookPayload.departamentos: list[str]` e `.departamento_outros: str`; colunas `Modalidades` e `Outros (descrição)` em CSV e Excel; chaves `departamentos` e `departamento_outros` no JSON de `/api/admin/search`.

- [ ] **Step 1: Escrever os testes que falham**

Em `tests/integration/test_exportacoes.py`, o helper `_criar_associado` passa a aceitar modalidades. Substituir a assinatura e acrescentar a criação:

```python
async def _criar_associado(
    db_session, nome, cpf, numero, telefone, modalidades=None, outros=None
):
    from app.models import (
        Associado,
        AssociadoDepartamento,
        Candidato,
        Departamento,
        Preferencia,
        Resposta,
    )

    candidato = Candidato(nome="Fulano", apelido="Fu", ativo=True)
    db_session.add(candidato)
    await db_session.commit()
    await db_session.refresh(candidato)

    associado = Associado(
        nome=nome,
        cpf=cpf,
        numero_socio=numero,
        telefone=telefone,
        departamento_outros=outros,
        aceite_lgpd=True,
    )
    db_session.add(associado)
    await db_session.commit()
    await db_session.refresh(associado)

    db_session.add(Resposta(associado_id=associado.id, candidato_id=candidato.id))
    db_session.add(
        Preferencia(associado_id=associado.id, candidato_preferido_id=candidato.id)
    )

    for ordem, nome_mod in enumerate(modalidades or [], start=1):
        dep = Departamento(nome=nome_mod, ordem=ordem, ativo=True)
        db_session.add(dep)
        await db_session.commit()
        await db_session.refresh(dep)
        db_session.add(
            AssociadoDepartamento(associado_id=associado.id, departamento_id=dep.id)
        )

    await db_session.commit()
    return associado
```

E acrescentar as classes de teste ao fim do arquivo:

```python
class TestModalidadesNasExportacoes:
    async def test_csv_tem_as_duas_colunas(
        self, client, db_session, admin_token, valid_cpf
    ):
        await _criar_associado(
            db_session,
            "Socio Mod",
            valid_cpf(),
            "0050",
            "11922220001",
            modalidades=["Natação", "Sauna"],
            outros=None,
        )

        res = await client.get(
            "/api/admin/export/csv",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        linhas = list(csv.reader(io.StringIO(res.text)))
        assert "Modalidades" in linhas[0]
        assert "Outros (descrição)" in linhas[0]
        assert linhas[1][linhas[0].index("Modalidades")] == "Natação, Sauna"

    async def test_excel_tem_as_duas_colunas(
        self, client, db_session, admin_token, valid_cpf
    ):
        from openpyxl import load_workbook

        await _criar_associado(
            db_session,
            "Socio Mod XLS",
            valid_cpf(),
            "0051",
            "11922220002",
            modalidades=["Judô"],
            outros="Xadrez",
        )

        res = await client.get(
            "/api/admin/export/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        ws = load_workbook(io.BytesIO(res.content)).active
        cabecalho = [c.value for c in ws[1]]
        assert ws[2][cabecalho.index("Modalidades")].value == "Judô"
        assert ws[2][cabecalho.index("Outros (descrição)")].value == "Xadrez"

    async def test_busca_do_admin_devolve_as_modalidades(
        self, client, db_session, admin_token, valid_cpf
    ):
        cpf = valid_cpf()
        await _criar_associado(
            db_session,
            "Socio Busca Mod",
            cpf,
            "0052",
            "11922220003",
            modalidades=["Piscina"],
            outros=None,
        )

        res = await client.get(
            f"/api/admin/search?cpf={cpf}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.json()[0]["departamentos"] == ["Piscina"]
        assert res.json()[0]["departamento_outros"] == ""


class TestPayloadDoWebhook:
    """O payload nunca teve teste automatizado — o campo numero_socio só foi
    conferido à mão, com um receptor HTTP. Como esta mudança mexe nele,
    entra a cobertura."""

    async def test_payload_serializado_tem_as_modalidades(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code, departamentos
    ):
        import json

        from sqlalchemy import select

        from app.models import WebhookLog

        deps = await departamentos()
        token, cid = await _preparar_voto_export(
            client, db_session, valid_cpf, numero_socio, read_otp_code, "11922220004"
        )

        await client.post(
            "/api/survey/submit",
            json={
                "session_token": token,
                "candidatos_ids": [cid],
                "candidato_preferido_id": cid,
                "departamentos_ids": [deps[0].id, deps[-1].id],
                "departamento_outros": "Xadrez",
                "aceite_lgpd": True,
            },
        )

        log = (await db_session.execute(select(WebhookLog))).scalars().first()
        payload = json.loads(log.payload)
        assert payload["departamentos"] == [deps[0].nome, deps[-1].nome]
        assert payload["departamento_outros"] == "Xadrez"

    async def test_sem_outros_o_campo_vai_como_string_vazia(
        self, client, db_session, valid_cpf, numero_socio, read_otp_code, departamentos
    ):
        """Nunca null: o n8n trata campo ausente e campo nulo de formas
        diferentes."""
        import json

        from sqlalchemy import select

        from app.models import WebhookLog

        deps = await departamentos()
        token, cid = await _preparar_voto_export(
            client, db_session, valid_cpf, numero_socio, read_otp_code, "11922220005"
        )

        await client.post(
            "/api/survey/submit",
            json={
                "session_token": token,
                "candidatos_ids": [cid],
                "candidato_preferido_id": cid,
                "departamentos_ids": [deps[0].id],
                "aceite_lgpd": True,
            },
        )

        log = (await db_session.execute(select(WebhookLog))).scalars().first()
        assert json.loads(log.payload)["departamento_outros"] == ""
```

E o helper que essas duas usam, no topo do arquivo:

```python
async def _preparar_voto_export(
    client, db_session, valid_cpf, numero_socio, read_otp_code, telefone
):
    """Igual ao de test_departamentos.py — repetido aqui de propósito para
    que cada arquivo de teste seja legível sozinho."""
    from app.models import Candidato

    candidato = Candidato(nome="Fulano", apelido="Fu", ativo=True)
    db_session.add(candidato)
    await db_session.commit()
    await db_session.refresh(candidato)

    res = await client.post(
        "/api/survey/register",
        json={
            "nome": "Socio Export",
            "cpf": valid_cpf(),
            "telefone": telefone,
            "numero_socio": numero_socio(),
            "recaptcha_token": "",
        },
    )
    token = res.json()["session_token"]
    codigo = read_otp_code(telefone)
    await client.post(
        "/api/survey/verify-otp",
        json={"session_token": token, "telefone": telefone, "codigo": codigo},
    )
    return token, candidato.id
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `pytest tests/integration/test_exportacoes.py -v`
Expected: FAIL — `'Modalidades' not in [...]`, `KeyError: 'departamentos'` e `KeyError` no payload.

- [ ] **Step 3: Carregar as modalidades nas duas consultas**

Em `app/repositories/__init__.py`, acrescentar `AssociadoDepartamento` ao import de `app.models` (a Task 2 deliberadamente não o importou, porque até aqui ele não teria uso e o `ruff` reprova F401), e o terceiro `selectinload` **nos dois métodos** (`search_by_cpf`, linhas 42-45, e `list_all_with_details`, linhas 53-56):

```python
            .options(
                selectinload(Associado.respostas).selectinload(Resposta.candidato),
                selectinload(Associado.preferencia).selectinload(Preferencia.candidato_preferido),
                selectinload(Associado.departamentos).selectinload(
                    AssociadoDepartamento.departamento
                ),
            )
```

Sem isso, a exportação faz uma consulta por sócio em vez de uma consulta a mais no total.

- [ ] **Step 4: Acrescentar os campos aos schemas**

Em `app/schemas/__init__.py`, **só** `WebhookPayload`:

```python
class WebhookPayload(BaseModel):
    nome: str
    numero_socio: str
    cpf: str
    telefone: str
    candidatos: list[str]
    preferido: str
    departamentos: list[str]
    # String vazia quando não se aplica, nunca None: o n8n trata campo
    # ausente e campo nulo de formas diferentes.
    departamento_outros: str
    aceite_lgpd: bool
    data: str
```

**Não tocar em `AssociadoResponse`** (linha 144 do mesmo arquivo), apesar de ele listar os mesmos campos do sócio. Verificado: a classe não é referenciada em nenhum lugar de `app/` nem de `tests/` — é código morto. Acrescentar `departamentos: list[str]` a um schema com `from_attributes=True` que ninguém constrói hoje planta uma falha para depois: no dia em que alguém fizesse `AssociadoResponse.model_validate(associado)`, o Pydantic receberia `list[AssociadoDepartamento]` onde o tipo diz `list[str]` e levantaria `ValidationError`. A busca do admin monta um dicionário à mão (Step 7) e não passa por esse schema.

- [ ] **Step 5: Preencher o payload do webhook**

Em `app/services/survey_service.py`, na montagem do payload dentro de `submit_vote` (linhas 234-243), acrescentar os dois campos. Os nomes saem de `departamento_map`, já carregado na Task 3, na ordem de `ordem`:

```python
        departamentos_nomes = [
            d.nome
            for d in sorted(
                (departamento_map[did] for did in departamentos_ids),
                key=lambda d: d.ordem,
            )
        ]

        payload = WebhookPayload(
            nome=associado.nome,
            numero_socio=associado.numero_socio,
            cpf=format_cpf(associado.cpf),
            telefone=associado.telefone,
            candidatos=candidatos_nomes,
            preferido=preferido_nome,
            departamentos=departamentos_nomes,
            departamento_outros=associado.departamento_outros or "",
            aceite_lgpd=aceite_lgpd,
            data=associado.data_resposta.isoformat(),
        )
```

- [ ] **Step 6: Acrescentar as colunas às exportações**

Em `app/services/survey_service.py`, classe `ExportService`. Nos **dois** métodos, o cabeçalho passa a ser:

```python
            [
                "ID",
                "Nº Sócio",
                "Nome",
                "CPF",
                "Telefone",
                "Candidatos",
                "Preferido",
                "Modalidades",
                "Outros (descrição)",
                "Data",
                "LGPD",
            ]
```

E, dentro do laço, antes de montar a linha:

```python
            modalidades = ", ".join(
                ad.departamento.nome
                for ad in sorted(a.departamentos, key=lambda x: x.departamento.ordem)
            )
```

A linha passa a ser, nos dois métodos, na mesma ordem do cabeçalho:

```python
                [
                    a.id,
                    a.numero_socio,
                    a.nome,
                    format_cpf(a.cpf),
                    a.telefone,
                    candidatos,
                    preferido,
                    modalidades,
                    a.departamento_outros or "",
                    a.data_resposta.isoformat(),
                    "Sim" if a.aceite_lgpd else "Não",
                ]
```

- [ ] **Step 7: Acrescentar os campos à busca do admin**

Em `app/api/routes/admin.py`, no dicionário montado por `search_cpf`:

```python
        {
            "id": a.id,
            "nome": a.nome,
            "cpf": a.cpf,
            "numero_socio": a.numero_socio,
            "telefone": a.telefone,
            "departamentos": [
                ad.departamento.nome
                for ad in sorted(a.departamentos, key=lambda x: x.departamento.ordem)
            ],
            "departamento_outros": a.departamento_outros or "",
            "data_resposta": a.data_resposta.isoformat(),
            "candidatos": [r.candidato.nome for r in a.respostas],
            "preferido": a.preferencia.candidato_preferido.nome if a.preferencia else None,
        }
```

- [ ] **Step 8: Exibir no painel do admin**

Em `static/js/admin.js`, no template do card de resultado, acrescentar uma linha após a de candidatos:

```javascript
                Modalidades: ${escapeHtml(r.departamentos.join(', ') || '-')}${r.departamento_outros ? ` (${escapeHtml(r.departamento_outros)})` : ''}<br>
```

O `escapeHtml` é obrigatório nos dois: `departamento_outros` é o único texto do painel escrito livremente pelo sócio.

- [ ] **Step 9: Rodar os testes**

Run: `pytest tests/integration/test_exportacoes.py -v`
Expected: PASS

- [ ] **Step 10: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 11: Atualizar o exemplo de JSON na documentação**

Em `docs/WEBHOOK.md`, o exemplo de payload passa a ser:

```json
{
  "nome": "Fulano de Tal",
  "numero_socio": "0042",
  "cpf": "123.456.789-00",
  "telefone": "11988887777",
  "candidatos": ["Candidato A", "Candidato B"],
  "preferido": "Candidato A",
  "departamentos": ["Natação", "Sauna", "Outros"],
  "departamento_outros": "Xadrez",
  "aceite_lgpd": true,
  "data": "2026-08-07T14:32:10.123456+00:00"
}
```

Acrescentar abaixo do exemplo: `departamento_outros` vem como string vazia quando o sócio não marcou "Outros" — nunca `null`.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat: inclui modalidades nas saidas de dados

CSV, Excel, payload do webhook e busca do admin. O texto do Outros vai em
coluna propria e nao embutido na lista, para nao poluir contagem e filtro
da coluna de modalidades.

As duas consultas do repositorio ganham selectinload da associativa: sem
ele a exportacao faz uma consulta por socio.

Inclui o primeiro teste automatizado do payload do webhook, que so tinha
verificacao manual."
```

---

### Task 5: Etapa no formulário público

**Files:**
- Modify: `templates/index.html` (nova `<section id="step4">`; a de LGPD vira `step5`)
- Modify: `static/js/app.js:3-10` (`state`), `:26-32` (`steps` e `stepLabels`), `:40-49` (`goToStep`), e o handler de envio
- Test: `tests/integration/test_paginas_html.py`

**Interfaces:**
- Consumes: `GET /api/survey/departamentos` (Task 2) e `/submit` aceitando os campos (Task 3).
- Produces: `<section id="step4">` com `id="departamentosLista"`, `id="departamentosBusca"` e `id="departamentoOutros"`; seção de LGPD com `id="step5"`.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `tests/integration/test_paginas_html.py`, dentro de `class TestPaginaPublica`:

```python
    async def test_index_tem_a_etapa_de_modalidades(self, client):
        """A etapa entrou entre candidatos e LGPD; se sumir do template, o
        fluxo quebra com 422 no submit e só apareceria em produção."""
        html = (await client.get("/")).text
        assert 'id="departamentosLista"' in html
        assert 'id="departamentosBusca"' in html
        assert 'id="departamentoOutros"' in html

    async def test_lgpd_virou_a_quinta_etapa(self, client):
        html = (await client.get("/")).text
        assert 'id="step5"' in html
        assert "Etapa 1 de 5" in html
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/integration/test_paginas_html.py -v`
Expected: FAIL com `assert 'id="departamentosLista"' in html`

- [ ] **Step 3: Renomear a seção de LGPD e atualizar o rótulo**

Em `templates/index.html`:

- linha 20: `<small id="stepLabel">Etapa 1 de 4 — Cadastro</small>` vira `Etapa 1 de 5 — Cadastro`
- a seção `<section id="step4" class="step-panel d-none">` do consentimento (linha 100) vira `<section id="step5" class="step-panel d-none">`

- [ ] **Step 4: Acrescentar a seção de modalidades**

Em `templates/index.html`, entre o fim da seção de candidatos (linha 98, `</section>`) e o comentário da etapa de LGPD:

```html
            <!-- Etapa 4: Modalidades -->
            <section id="step4" class="step-panel d-none">
                <div class="mx-auto step-card">
                    <h2 class="h6 fw-bold mb-1">Modalidades e Departamentos</h2>
                    <p class="subtitle-muted mb-3">Quais modalidade/departamentos do clube você participa? Marque todas que se aplicam.</p>

                    <input type="search" class="form-control mb-2" id="departamentosBusca" placeholder="Buscar modalidade..." autocomplete="off" aria-label="Buscar modalidade">

                    <div id="departamentosLista" class="departamentos-lista"></div>

                    <div class="mb-3 mt-2 d-none" id="departamentoOutrosWrap">
                        <label for="departamentoOutros" class="form-label">Qual? Descreva a modalidade ou departamento *</label>
                        <input type="text" class="form-control" id="departamentoOutros" maxlength="100" autocomplete="off" placeholder="Ex.: Xadrez">
                    </div>

                    <p class="subtitle-muted small mb-2"><span id="departamentosCount">0</span> selecionada(s)</p>
                    <button type="button" class="btn btn-primary btn-lg w-100" id="btnDepartamentos">CONTINUAR</button>
                </div>
            </section>
```

E em `static/css/style.css`, ao fim do arquivo:

```css
/* Lista de modalidades: 49 itens numa tela de celular precisam de rolagem
   própria, senão a página inteira vira uma rolagem de vários metros. */
.departamentos-lista {
    max-height: 320px;
    overflow-y: auto;
    background: #fff;
    border: 1px solid #dfe3e8;
    border-radius: 8px;
}

.departamento-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border-bottom: 1px solid #eef0f3;
    cursor: pointer;
}

.departamento-item:last-child {
    border-bottom: 0;
}

.departamento-item.selected {
    background: #fff5f5;
}
```

- [ ] **Step 5: Atualizar a navegação de etapas**

Em `static/js/app.js`:

No objeto `state` (linhas 3-10), acrescentar:

```javascript
    departamentos: [],
    selectedDepartamentoIds: new Set(),
```

As linhas 26-32 passam a ser:

```javascript
const steps = ['step1', 'step2', 'step3', 'step4', 'step5', 'stepThanks'];
const stepLabels = [
    'Etapa 1 de 5 — Cadastro',
    'Etapa 2 de 5 — Verificação',
    'Etapa 3 de 5 — Candidatos',
    'Etapa 4 de 5 — Modalidades',
    'Etapa 5 de 5 — Consentimento',
];
```

E o `goToStep` (linhas 40-49) deixa de usar os literais `4` e `25`:

```javascript
function goToStep(n) {
    state.step = n;
    steps.forEach((id, i) => {
        document.getElementById(id).classList.toggle('d-none', i !== n - 1);
    });
    // Derivado de stepLabels.length, não cravado: o 4 e o 25 anteriores eram
    // a mesma informação escrita duas vezes, e ambos ficavam errados assim
    // que uma etapa era acrescentada.
    if (n <= stepLabels.length) {
        document.getElementById('stepProgress').style.width = `${(n / stepLabels.length) * 100}%`;
        document.getElementById('stepLabel').textContent = stepLabels[n - 1];
    }
}
```

- [ ] **Step 6: Renderizar a lista e ligar a busca**

Em `static/js/app.js`, acrescentar as funções (junto das que renderizam candidatos):

```javascript
async function loadDepartamentos() {
    const res = await fetch('/api/survey/departamentos');
    state.departamentos = await res.json();
    renderDepartamentos();
}

function renderDepartamentos() {
    const lista = document.getElementById('departamentosLista');
    lista.innerHTML = state.departamentos.map(d => `
        <div class="departamento-item" data-id="${d.id}">
            <input class="form-check-input mt-0" type="checkbox" id="dep-${d.id}">
            <label class="form-check-label flex-grow-1" for="dep-${d.id}">${escapeHtml(d.nome)}</label>
        </div>`).join('');

    lista.querySelectorAll('.departamento-item').forEach(item => {
        const id = Number(item.dataset.id);
        const check = item.querySelector('input');
        item.addEventListener('click', (e) => {
            if (e.target !== check) check.checked = !check.checked;
            item.classList.toggle('selected', check.checked);
            if (check.checked) {
                state.selectedDepartamentoIds.add(id);
            } else {
                state.selectedDepartamentoIds.delete(id);
            }
            updateDepartamentosUI();
        });
    });
}

function updateDepartamentosUI() {
    document.getElementById('departamentosCount').textContent =
        state.selectedDepartamentoIds.size;

    // "Outros" é reconhecido pelo nome só no cliente, para revelar o campo.
    // A obrigatoriedade de verdade é do servidor, que usa a coluna
    // exige_texto — se o nome mudar, o pior caso aqui é o campo não
    // aparecer e o backend recusar com a mensagem correta.
    const outros = state.departamentos.find(d => d.nome === 'Outros');
    const marcado = outros && state.selectedDepartamentoIds.has(outros.id);
    document.getElementById('departamentoOutrosWrap').classList.toggle('d-none', !marcado);
}

document.getElementById('departamentosBusca').addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll('.departamento-item').forEach(item => {
        const nome = item.querySelector('label').textContent.toLowerCase();
        item.classList.toggle('d-none', !nome.includes(q));
    });
});

document.getElementById('btnDepartamentos').addEventListener('click', () => {
    if (state.selectedDepartamentoIds.size === 0) {
        showToast('Marque ao menos uma modalidade');
        return;
    }
    const wrap = document.getElementById('departamentoOutrosWrap');
    const texto = document.getElementById('departamentoOutros').value.trim();
    if (!wrap.classList.contains('d-none') && !texto) {
        showToast('Descreva qual modalidade em Outros');
        return;
    }
    goToStep(5);
});
```

Chamar `loadDepartamentos()` no mesmo ponto em que os candidatos já são carregados (após a verificação do OTP), para que a lista esteja pronta quando a etapa 4 aparecer.

- [ ] **Step 7: Enviar os campos no submit**

No handler do `btnSubmit`, acrescentar ao corpo do `fetch` de `/api/survey/submit`:

```javascript
                departamentos_ids: [...state.selectedDepartamentoIds],
                departamento_outros: document.getElementById('departamentoOutros').value.trim(),
```

Conferir também o `btnCandidatos`: ele já chama `goToStep(4)` e **a chamada não muda**, mas o destino sim — a etapa 4 deixou de ser o consentimento e passou a ser as modalidades. Quem leva ao consentimento agora é o `btnDepartamentos`, com `goToStep(5)`. É o tipo de mudança que não quebra nada visivelmente no código e manda o usuário para a tela errada, então vale conferir na execução do Step 10.

- [ ] **Step 8: Rodar os testes de página**

Run: `pytest tests/integration/test_paginas_html.py -v`
Expected: PASS

- [ ] **Step 9: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 10: Conferir o fluxo no navegador**

Subir a stack e aplicar a migration:

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
docker compose exec -T db psql -U sondagemtest -d sondagem_clube -c \
  "INSERT INTO candidatos (nome, apelido, ativo) VALUES ('Ana Ribeiro','Ana',true);"
```

Abrir `http://localhost:8000` e confirmar:

1. a barra de progresso mostra "Etapa 1 de 5" e avança de 20% em 20%;
2. depois dos candidatos vem a tela de modalidades, e só depois o consentimento;
3. a lista tem 49 itens, rola dentro da própria caixa e a página não estica;
4. digitar "vol" na busca deixa só os itens de vôlei;
5. tentar continuar sem marcar nada mostra o toast e não avança;
6. marcar "Outros" revela o campo de texto; tentar continuar com ele vazio mostra o toast;
7. um voto completo chega à tela de agradecimento.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: etapa de modalidades no formulario publico

Nova etapa 4 entre candidatos e consentimento, com busca filtrando no
cliente e lista rolavel — 49 itens numa tela de celular precisam de
rolagem propria.

goToStep passa a derivar largura e limite de stepLabels.length: o 4 e o 25
cravados eram a mesma informacao escrita duas vezes e ambos ficavam
errados ao acrescentar uma etapa."
```

---

## Verificação final

- [ ] `pytest -q` verde na suíte inteira
- [ ] `alembic upgrade head` roda limpo contra banco vazio, e o seed devolve 49 linhas com exatamente uma `exige_texto`
- [ ] `alembic downgrade 005 && alembic upgrade head` completa sem erro
- [ ] A ordem do bloco de ginástica no banco é Aeróbica, Artística, Rítmica, Feminina / Fitness
- [ ] Fluxo público completo testado no navegador, do cadastro ao agradecimento, passando pelas 5 etapas
- [ ] CSV exportado abre com `Modalidades` preenchida e `Outros (descrição)` só para quem marcou
- [ ] Payload do webhook conferido com destino real (ou pelo teste automatizado da Task 4)
