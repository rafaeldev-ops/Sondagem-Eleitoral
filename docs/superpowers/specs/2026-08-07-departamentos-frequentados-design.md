# Modalidades/departamentos frequentados pelo sócio

Data: 2026-08-07

## Objetivo

Acrescentar ao fluxo da sondagem uma etapa perguntando **"Quais modalidade/departamentos
do clube você participa?"**, com seleção múltipla obrigatória sobre uma lista de 48
modalidades mais "Outros", e fazer a resposta aparecer nas quatro saídas de dados (CSV,
Excel, webhook e busca do admin) junto com o voto.

A etapa entra **entre a seleção de candidatos e o consentimento LGPD**, levando o fluxo
público de 4 para 5 etapas.

## Decisões tomadas

| Questão | Decisão |
|---|---|
| Marcar ao menos uma é obrigatório? | Sim — sem seleção não avança |
| Onde a lista de modalidades vive? | Tabela `departamentos` no banco, populada por migration |
| CRUD de modalidades no admin? | Não — mudança na lista é migration ou script de seed |
| "Outros" abre campo de texto? | Sim, obrigatório quando "Outros" está marcado |
| Posição no fluxo | Nova etapa 4, entre Candidatos (3) e LGPD (que vira 5) |
| Layout da lista | Lista única rolável com campo de busca filtrando no cliente |
| Sai nas exportações? | Sim — CSV, Excel, webhook e busca do admin |
| Limite de seleções | Nenhum além do total de opções (49) |
| Painel de "sócios por modalidade" no admin | Fora de escopo |

O layout de lista única foi escolhido sobre agrupamento por categoria e sobre etiquetas
compactas. O agrupamento exigiria classificar as 48 numa taxonomia — e **COD, COTI e
FAVA não têm significado conhecido por quem escreveu esta spec**, então a classificação
seria chute. As etiquetas compactas economizariam rolagem ao custo de alvo de toque
menor, numa etapa obrigatória, no celular. A pergunta é sobre o próprio sócio, que já
sabe o que frequenta: a busca resolve em dois toques.

## Modelo de dados

Duas tabelas novas, espelhando o par `Candidato`/`Resposta` que já existe, e uma coluna
nova em `Associado`.

```python
class Departamento(Base):
    __tablename__ = "departamentos"
    __table_args__ = (UniqueConstraint("nome", name="uq_departamentos_nome"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    # Ordem de exibição explícita — ver "Por que ordem explícita" abaixo.
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    # True apenas para "Outros": marca a opção que exige texto complementar.
    exige_texto: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    associados: Mapped[list["AssociadoDepartamento"]] = relationship(
        back_populates="departamento"
    )
```

```python
class AssociadoDepartamento(Base):
    __tablename__ = "associado_departamentos"
    __table_args__ = (
        UniqueConstraint(
            "associado_id", "departamento_id", name="uq_associado_departamento"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # index=True nas duas FKs: o Postgres não indexa coluna de chave estrangeira
    # sozinho, e a exportação faz join pelas duas. Declarado aqui, e não só na
    # migration, para que create_all (testes) e Alembic (produção) concordem.
    associado_id: Mapped[int] = mapped_column(
        ForeignKey("associados.id"), nullable=False, index=True
    )
    departamento_id: Mapped[int] = mapped_column(
        ForeignKey("departamentos.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    associado: Mapped["Associado"] = relationship(back_populates="departamentos")
    departamento: Mapped["Departamento"] = relationship(back_populates="associados")
```

Em `Associado`, uma coluna e uma relação:

```python
    # Preenchido só por quem marcou "Outros". Um texto por pessoa, não por
    # modalidade — daí ficar aqui e não em AssociadoDepartamento.
    departamento_outros: Mapped[str | None] = mapped_column(String(100), nullable=True)

    departamentos: Mapped[list["AssociadoDepartamento"]] = relationship(
        back_populates="associado"
    )
```

Todas as constraints únicas são **nomeadas explicitamente**, pela mesma razão registrada
na spec do número de sócio: `Base.metadata` não tem `naming_convention`, então sem nome
explícito o `create_all` dos testes e o Alembic da produção geram nomes diferentes.

### Por que ordem explícita, e não `ORDER BY nome`

O banco de produção usa collation `en_US.utf8`. Medido no Postgres do projeto:

```
… Natação | Volei Masculino | Vôlei Feminino | Vôlei de Praia
```

"Volei Masculino" (sem acento) se separa dos outros dois "Vôlei", e "Feminino" aparece
antes de "de Praia". Ordenar pelo nome entregaria essa sequência ao sócio, e o resultado
pode ainda variar entre a máquina de desenvolvimento e o servidor, que não precisam ter a
mesma collation. A coluna `ordem` torna a sequência um dado, não um efeito colateral do
ambiente.

### Por que `exige_texto`, e não comparar `nome == "Outros"`

O serviço precisa saber se a seleção inclui a opção que pede texto complementar.
Compará-la pelo nome quebra silenciosamente se alguém renomear para "Outra modalidade"
ou corrigir um acento no seed; cravar o `id` no código quebra se o seed mudar de ordem.
A coluna booleana elimina as duas fragilidades e permite que uma segunda opção passe a
exigir texto no futuro sem tocar no serviço.

### Por que `ativo`, copiando `Candidato.ativo`

Quando o clube encerrar uma modalidade, ela sai do formulário sem apagar as linhas de
`associado_departamentos` de quem a marcou. Desativar, nunca deletar — deletar levaria o
histórico junto.

### Migration

Migration `006`, com três operações e o seed:

1. `create_table("departamentos")`
2. `create_table("associado_departamentos")`
3. `add_column("associados", "departamento_outros", String(100), nullable=True)`
4. `bulk_insert` das 48 modalidades (ordem 1..48, na sequência fornecida pelo clube)
   mais "Outros" (`ordem=999`, `exige_texto=True`)

Índices em `associado_departamentos.associado_id` e `.departamento_id`: o Postgres não
indexa colunas de chave estrangeira automaticamente, e a exportação faz join pelas duas.
Os índices vão **no modelo**, para que testes e produção tenham o mesmo schema — a
migration `002` criou índices que só existem em produção, divergência que já custou uma
correção (migration `005`).

**A migration não exige tabela vazia.** As duas tabelas são novas e a coluna é nullable —
diferente da `004`, que só rodou porque `associados` estava vazia. Esta roda com a
sondagem em produção e votos já coletados.

O `downgrade` derruba a coluna e as duas tabelas, nesta ordem: primeiro
`associado_departamentos` (tem as FKs), depois `departamentos`.

### Seed

Ordem 1 a 48, exatamente na sequência fornecida pelo clube:

```
Academia / Musculação, Atletismo, Basquete – Feminino, Basquete – Masculino,
Beach Tennis, Biribol, Boxe, Capoeira, Carteado, COD, COTI, Esportes Amadores,
FAVA, Fitness / Dança, Futebol de Mesa, Futebol Social – Feminino,
Futebol Social – Masculino, Futebol Social – Menores, Futebol Society – Feminino,
Futebol Society – Masculino, Futevôlei, Ginastica Aeróbica, Ginastica Artística,
Ginastica Rítmica, Ginastica Feminina / Fitness, Handebol, Hidroginástica,
Jiu Jitsu, Judô, Karaokê, Kickboxing, Natação, Paddle, Patinação, Pickleball,
Piscina, Polo Aquático, Sauna, Sinuca, Social, Tai Chi Chuan, Teatro,
Tenis de mesa, Tennis, Triathlon, Vôlei de Praia, Vôlei Feminino, Volei Masculino
```

Depois, `Outros` com `ordem=999` e `exige_texto=True`.

Os nomes são gravados **exatamente como acima**, incluindo a grafia sem acento de
"Ginastica", "Tenis de mesa" e "Volei Masculino", e o travessão (`–`, U+2013) nos nomes
de "Basquete – Feminino" e similares. Corrigir grafia é decisão do clube, não da
implementação; qualquer normalização silenciosa faria o texto exibido divergir da lista
oficial.

**A sequência também não deve ser reordenada.** Ela é quase alfabética, mas tem uma
exceção deliberada: no bloco de ginástica a ordem é Aeróbica, Artística, **Rítmica**,
**Feminina / Fitness** — "Rítmica" antes de "Feminina". Verificado item a item: essa é a
única inversão real da lista. (Uma comparação ingênua também acusa "Vôlei Feminino" antes
de "Volei Masculino", mas isso é o mesmo efeito de acento descrito em "Por que ordem
explícita" — a leitura humana da sequência Vôlei de Praia / Vôlei Feminino / Volei
Masculino está correta.) Ordenar alfabeticamente "para arrumar" contraria a lista oficial:
é exatamente por isso que a ordem é um dado gravado, e não calculada.

## Validação

A validação se divide em dois lugares, e a divisão é deliberada.

**No schema (`VotoRequest`) — forma:**

```python
    departamentos_ids: list[int] = Field(min_length=1, max_length=49)
    departamento_outros: str = Field(default="", max_length=100)

    @field_validator("departamento_outros")
    @classmethod
    def sanitize_departamento_outros(cls, value: str) -> str:
        return sanitize_text(value, 100)
```

`min_length=1` é a obrigatoriedade. `max_length=49` é o total de opções e existe para
recusar payload absurdo. O texto passa por `sanitize_text` (Bleach), como todo campo livre
do projeto — é o primeiro texto aberto do fluxo público.

**No serviço — semântica:**

- todo id enviado existe e tem `ativo=True`, senão `"Modalidade inválida"`;
- se algum departamento selecionado tem `exige_texto=True` e `departamento_outros` está
  vazio, `"Descreva qual modalidade em Outros"`.

A regra do "Outros" **não pode ficar no schema**: o Pydantic não tem acesso ao banco e
portanto não sabe qual id exige texto. Colocá-la lá exigiria cravar um número no código.

Quando nenhum departamento tem `exige_texto=True` entre os selecionados, o valor de
`departamento_outros` é descartado (gravado como `None`) — não se guarda texto órfão de
quem preencheu o campo e depois desmarcou "Outros".

## Fluxo

1. `GET /api/survey/departamentos` devolve `[{"id": …, "nome": …}]`, só `ativo=True`,
   ordenado por `ordem`. Espelha `GET /api/survey/candidatos`
   (`app/api/routes/survey.py:126`). As 49 opções vão num payload só; a busca da tela
   filtra no cliente, sem endpoint de busca no servidor.
2. `POST /api/survey/submit` passa a levar `departamentos_ids` e `departamento_outros`.
3. `SurveyService.submit_vote` grava, **na mesma transação** que já grava `Associado`,
   `Resposta` e `Preferencia`, as linhas de `AssociadoDepartamento`. Sem commit separado:
   se o voto falhar, não sobram modalidades órfãs.

## Frontend

O template ganha uma `<section id="step4">` com as modalidades, e a seção de LGPD que
hoje é `step4` passa a ser `step5`.

Em `static/js/app.js`:

- `steps` vira `['step1','step2','step3','step4','step5','stepThanks']`;
- `stepLabels` ganha a quinta entrada e todos os rótulos passam a dizer "de 5";
- `goToStep` deixa de usar os literais `4` e `25`:

```js
    if (n <= stepLabels.length) {
        const pct = (n / stepLabels.length) * 100;
        document.getElementById('stepProgress').style.width = `${pct}%`;
        document.getElementById('stepLabel').textContent = stepLabels[n - 1];
    }
```

O `4` e o `25` são a mesma informação escrita duas vezes e ambos ficam errados no instante
em que existe uma quinta etapa. Derivá-los de `stepLabels.length` faz a próxima etapa
custar só uma entrada no array.

A tela: campo de busca filtrando no cliente, lista rolável das 49 opções com checkbox,
contador de selecionadas. Marcar "Outros" revela o campo de texto. Os nomes vêm do banco
e são renderizados com `escapeHtml`, como todo campo dinâmico do arquivo.

Validação no cliente espelha a do servidor — ao menos uma marcada, texto obrigatório se
"Outros" estiver marcado — só para evitar ida e volta. Quem contornar o JS encontra a
mesma regra no backend.

## Saídas

As quatro, como no número de sócio.

**CSV e Excel** ganham duas colunas, entre as respostas e os metadados:

```
ID · Nº Sócio · Nome · CPF · Telefone · Candidatos · Preferido · Modalidades ·
Outros (descrição) · Data · LGPD
```

`Modalidades` é `", ".join(...)`, igual à coluna `Candidatos`. O texto do "Outros" fica em
coluna própria e não embutido na lista, para que contar e filtrar a coluna `Modalidades`
não seja poluído pela descrição livre de um sócio. Quem marcou "Outros" aparece com
`Outros` na lista **e** com o texto ao lado.

**Webhook** ganha dois campos:

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

`departamento_outros` vai como string vazia quando não se aplica, nunca `null`: o tipo
fica estável para o n8n, que trata campo ausente e campo nulo de formas diferentes.

**Busca do admin** ganha as mesmas duas chaves no JSON, renderizadas no card do painel
com `escapeHtml` — é o único ponto do admin que exibe texto escrito pelo sócio.

**A consulta:** `AssociadoRepository.list_all_with_details()` ganha mais um
`selectinload` encadeado, seguindo os dois que já existem:

```python
    selectinload(Associado.departamentos).selectinload(AssociadoDepartamento.departamento),
```

Sem isso a exportação faz uma consulta por sócio em vez de uma consulta a mais no total.

## Testes

**Os testes criam o schema com `create_all` e não rodam as migrations** (ver a fixture
`_schema` em `tests/conftest.py`), então as 49 modalidades do seed **não existem na
suíte**. Os testes criam as suas próprias, como já fazem com candidatos. Vai uma fixture
nova no `conftest.py` para isso.

Consequência: o seed da migration `006` não é exercitado pela suíte e precisa ser
verificado à mão contra um banco vazio, como foi feito com as migrations `004` e `005`.

Arquivos:

- `tests/integration/test_departamentos.py` (novo)
  - `GET /departamentos` devolve só os ativos, na ordem de `ordem`;
  - submit sem nenhuma modalidade → 422;
  - submit com id inexistente → 400;
  - submit com id de modalidade inativa → 400;
  - "Outros" marcado sem texto → 400;
  - caminho feliz: as linhas de `associado_departamentos` são gravadas e o texto do
    "Outros" persiste;
  - texto preenchido sem "Outros" marcado é descartado.
- `tests/integration/test_exportacoes.py` — estende para as duas colunas novas no CSV e no
  Excel e para as duas chaves na busca do admin.
- `tests/integration/test_paginas_html.py` — a quinta etapa existe no HTML servido.
- **Teste novo do payload do webhook.** Hoje não existe nenhum: o campo `numero_socio` no
  payload só foi verificado manualmente, com um receptor HTTP real. Como esta mudança
  mexe no payload, entra um teste automatizado dele.
- **Todos os testes que chamam `/submit` quebram** quando `departamentos_ids` vira
  obrigatório, exatamente como aconteceu com o número de sócio: `test_submit.py`,
  `test_numero_socio.py` e `tests/load/locustfile.py`.

## Fora de escopo

- **Painel ou gráfico de "sócios por modalidade" no admin.** O pedido é que a informação
  saia nas exportações; o cruzamento é feito na planilha.
- **CRUD de modalidades no admin.** Mudança na lista é migration ou script de seed.
- **Corrigir a grafia da lista** ("Ginastica", "Tenis de mesa", "Volei Masculino"). Os
  nomes são gravados como o clube forneceu.
- **Revisar a coexistência de `candidatos_ids` e `candidato_preferido_id`.** Levantado
  durante o design: a seleção de até 20 candidatos só faz sentido se a eleição preenche
  várias vagas; se for vaga única, a modelagem merece revisão. Tratado separadamente.
