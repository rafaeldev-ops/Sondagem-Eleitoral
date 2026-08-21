"""
A aplicação deixou de morar na raiz do domínio e passou a viver sob um
prefixo configurável (APP_PATH_PREFIX, "/pesquisa2026" em produção).

Motivo: sempretricolor.org vai receber um site institucional próprio, e a
sondagem é provisória — vai ao ar até o fim de novembro. As duas coisas
precisam conviver no mesmo domínio sem que a sondagem ocupe a raiz.

O grosso da cobertura do prefixo não está neste arquivo: a fixture
`client` (tests/conftest.py) monta a base_url COM o prefixo, então a suíte
inteira já roda contra o app deslocado. O que está aqui é o que aquela
fixture não consegue afirmar — o comportamento na raiz, e o escopo do
cookie.
"""

import pytest

from tests.conftest import PATH_PREFIX

# Este arquivo só faz sentido com a aplicação deslocada da raiz. O conftest
# define APP_PATH_PREFIX=/pesquisa2026 por padrão, então na prática ele
# sempre roda; o skip existe para quem exercita deliberadamente o modo
# "montado na raiz" (APP_PATH_PREFIX=""), que continua suportado e é o
# default do .env.example.
pytestmark = pytest.mark.skipif(
    not PATH_PREFIX,
    reason="APP_PATH_PREFIX vazio: a aplicação está na raiz, não há prefixo a verificar",
)


class TestAplicacaoSobPrefixo:
    async def test_a_sondagem_responde_sob_o_prefixo(self, client):
        res = await client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert 'id="step1"' in res.text

    async def test_o_painel_admin_responde_sob_o_prefixo(self, client):
        res = await client.get("/admin")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    async def test_os_assets_do_html_apontam_para_dentro_do_prefixo(self, client):
        """
        Não basta a rota responder: se o HTML continuar pedindo
        /static/js/app.js na raiz, o navegador busca o arquivo no site
        institucional, recebe 404 (ou pior, o HTML dele) e a página carrega
        sem estilo e sem nenhum handler registrado.
        """
        html = (await client.get("/")).text
        assert f"{PATH_PREFIX}/static/css/style.css" in html
        assert f"{PATH_PREFIX}/static/js/app.js" in html

    async def test_o_js_recebe_o_prefixo_para_montar_as_chamadas(self, client):
        """
        O app.js precisa saber o prefixo para chamar /api/survey/... no
        lugar certo. Chega por atributo no <html>, e não por <script>
        inline, porque a CSP não permite 'unsafe-inline' em script-src.
        """
        html = (await client.get("/")).text
        assert f'data-base-path="{PATH_PREFIX}"' in html

    async def test_a_foto_gravada_e_servida_de_volta_sob_o_prefixo(
        self, client, admin_token
    ):
        """
        Faz o caminho inteiro da foto: sobe pelo painel, lê o valor que
        ficou gravado e busca esse valor de volta pelo prefixo.

        É o teste que mais importa aqui, porque o banco guarda a foto como
        "/uploads/<arquivo>" — caminho de raiz. Se o prefixo não entrasse
        na hora de montar a URL, o navegador buscaria a foto no site
        institucional e todo candidato apareceria sem imagem.
        """
        auth = {"Authorization": f"Bearer {admin_token}"}
        # Assinatura real de JPEG: o upload valida os magic bytes.
        jpeg = bytes.fromhex("ffd8ff") + b"conteudo-de-teste"

        criado = await client.post(
            "/api/admin/candidatos",
            data={"nome": "Candidato Com Foto", "apelido": "Foto"},
            files={"foto": ("retrato.jpg", jpeg, "image/jpeg")},
            headers=auth,
        )
        assert criado.status_code == 200

        listagem = (await client.get("/api/admin/candidatos", headers=auth)).json()
        foto = listagem[0]["foto"]
        assert foto.startswith("/uploads/"), "o valor gravado é relativo à aplicação"

        # É assim que o JS monta a URL: prefixo + valor do banco.
        servida = await client.get(foto)
        assert servida.status_code == 200
        assert servida.content == jpeg


class TestDocsInterativa:
    """A suíte roda com DEBUG=true, que é quando /api/docs existe. Em
    produção DEBUG=false e ela não é servida em lugar nenhum."""

    async def test_a_documentacao_interativa_acompanha_o_prefixo(self, client):
        res = await client.get("/api/docs")
        assert res.status_code == 200

    async def test_a_documentacao_interativa_nao_fica_na_raiz(self, root_client):
        """Chumbada na raiz, ela seria a única parte da aplicação servida
        fora do prefixo — no domínio de produção, num caminho que pertence
        ao site institucional."""
        assert (await root_client.get("/api/docs")).status_code == 404


class TestRaizLivre:
    async def test_a_raiz_nao_serve_mais_a_sondagem(self, root_client):
        res = await root_client.get("/")
        assert res.status_code == 404
        assert 'id="step1"' not in res.text

    async def test_a_raiz_nao_serve_mais_o_painel_admin(self, root_client):
        assert (await root_client.get("/admin")).status_code == 404

    async def test_a_api_nao_responde_na_raiz(self, root_client):
        assert (await root_client.get("/api/survey/candidatos")).status_code == 404


class TestHealthNosDoisLugares:
    async def test_health_continua_na_raiz(self, root_client):
        """O HEALTHCHECK do docker-compose bate em localhost:8000/health, de
        dentro do container, onde não existe Nginx nem prefixo. Se o /health
        se mudasse só para o prefixo, o container entraria em loop de
        restart em produção."""
        res = await root_client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    async def test_health_tambem_responde_sob_o_prefixo(self, client):
        """Com a raiz entregue a outro site, um monitor externo de uptime
        não alcança mais o /health pelo domínio — só o que está sob o
        prefixo passa a ser nosso."""
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


class TestCookieEscopadoNoPrefixo:
    async def test_cookies_do_admin_ficam_restritos_ao_prefixo(self, client):
        """
        Com outro site na raiz do mesmo domínio, cookie com Path=/ é
        enviado nas requisições dele também. O de sessão é httpOnly, mas
        ainda assim viaja para uma aplicação que não é esta — e o de CSRF
        é legível por qualquer script servido de lá.
        """
        res = await client.post(
            "/api/admin/login", json={"username": "admin", "password": "admin123"}
        )
        assert res.status_code == 200

        set_cookies = res.headers.get_list("set-cookie")
        assert set_cookies, "login deveria devolver cookies"
        for cookie in set_cookies:
            assert f"Path={PATH_PREFIX}" in cookie, f"cookie sem escopo de path: {cookie}"
