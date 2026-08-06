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
