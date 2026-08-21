"""
Gestão de candidatos pelo painel: editar e excluir.

Antes disso o painel só sabia criar candidato e alternar o "ativo" — não
havia como corrigir um apelido digitado errado nem tirar do banco uma
linha duplicada (a sondagem chegou a ter o mesmo nome cadastrado duas
vezes, uma delas inativa, e a única saída era SQL na mão no servidor).
"""

import pytest

from app.models import Associado, Candidato, Preferencia, Resposta


@pytest.fixture
def auth(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


async def _criar_candidato(db_session, nome="Fulano de Tal", apelido="Fulano", ativo=True):
    candidato = Candidato(nome=nome, apelido=apelido, ativo=ativo)
    db_session.add(candidato)
    await db_session.commit()
    await db_session.refresh(candidato)
    return candidato


async def _dar_um_voto(db_session, candidato, cpf, numero_socio):
    associado = Associado(
        nome="Sócio Votante",
        cpf=cpf,
        numero_socio=numero_socio,
        telefone="11999990000",
        aceite_lgpd=True,
    )
    db_session.add(associado)
    await db_session.commit()
    await db_session.refresh(associado)
    db_session.add(Resposta(associado_id=associado.id, candidato_id=candidato.id))
    await db_session.commit()
    return associado


class TestExcluirCandidato:
    async def test_exclui_candidato_sem_voto(self, client, db_session, auth):
        candidato = await _criar_candidato(db_session, "Juliana Vidal Mayorga", "Ju Vidal", ativo=False)

        res = await client.delete(f"/api/admin/candidatos/{candidato.id}", headers=auth)
        assert res.status_code == 200

        listagem = await client.get("/api/admin/candidatos", headers=auth)
        assert [c["id"] for c in listagem.json()] == []

    async def test_recusa_excluir_candidato_que_ja_recebeu_voto(
        self, client, db_session, auth, valid_cpf, numero_socio
    ):
        """
        Excluir um candidato votado apagaria voto de sócio junto (ou
        estouraria a foreign key com um 500 opaco). A sondagem é o registro
        de uma apuração: quem já recebeu voto se desativa, não se apaga.
        """
        candidato = await _criar_candidato(db_session)
        await _dar_um_voto(db_session, candidato, valid_cpf(), numero_socio())

        res = await client.delete(f"/api/admin/candidatos/{candidato.id}", headers=auth)
        assert res.status_code == 409
        assert "desative" in res.json()["detail"].lower()

        listagem = await client.get("/api/admin/candidatos", headers=auth)
        assert [c["id"] for c in listagem.json()] == [candidato.id]

    async def test_recusa_excluir_candidato_escolhido_como_ponto_focal(
        self, client, db_session, auth, valid_cpf, numero_socio
    ):
        """Preferência é uma segunda tabela apontando para candidatos, e
        pode existir sem que haja Resposta — checar só votos deixaria este
        caso estourar a foreign key."""
        candidato = await _criar_candidato(db_session)
        associado = Associado(
            nome="Sócio Focal",
            cpf=valid_cpf(),
            numero_socio=numero_socio(),
            telefone="11988880000",
            aceite_lgpd=True,
        )
        db_session.add(associado)
        await db_session.commit()
        await db_session.refresh(associado)
        db_session.add(
            Preferencia(associado_id=associado.id, candidato_preferido_id=candidato.id)
        )
        await db_session.commit()

        res = await client.delete(f"/api/admin/candidatos/{candidato.id}", headers=auth)
        assert res.status_code == 409

    async def test_candidato_inexistente_devolve_404(self, client, auth):
        res = await client.delete("/api/admin/candidatos/999999", headers=auth)
        assert res.status_code == 404

    async def test_exige_autenticacao(self, client, db_session):
        candidato = await _criar_candidato(db_session)
        res = await client.delete(f"/api/admin/candidatos/{candidato.id}")
        assert res.status_code == 401


class TestEditarCandidato:
    async def test_edita_apelido_preservando_o_resto(self, client, db_session, auth):
        """O caso real que motivou a tela: 'Cadu' precisava virar 'Cadu
        Summo' sem que o nome completo, a foto ou o status mudassem."""
        candidato = await _criar_candidato(
            db_session, "Carlos Eduardo dos Santos Summo", "Cadu"
        )

        res = await client.put(
            f"/api/admin/candidatos/{candidato.id}",
            data={"apelido": "Cadu Summo"},
            headers=auth,
        )
        assert res.status_code == 200

        listagem = (await client.get("/api/admin/candidatos", headers=auth)).json()
        assert listagem[0]["apelido"] == "Cadu Summo"
        assert listagem[0]["nome"] == "Carlos Eduardo dos Santos Summo"
        assert listagem[0]["ativo"] is True


class TestPainelTemAsAcoes:
    """A API sozinha não resolve: o painel é usado pelo navegador, e até
    aqui o admin.js só desenhava o botão de ativar/desativar."""

    async def test_pagina_do_admin_traz_o_formulario_de_edicao(self, client):
        html = (await client.get("/admin")).text
        assert 'id="editCandidatoForm"' in html
