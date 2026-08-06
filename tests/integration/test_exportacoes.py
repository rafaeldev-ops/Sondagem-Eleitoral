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
