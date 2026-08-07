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
