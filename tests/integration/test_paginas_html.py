"""
As duas páginas HTML servidas por Jinja: / (fluxo público) e /admin.

Motivo de existir: a suíte só batia em rotas de API e em /health. As
páginas renderizadas nunca eram requisitadas por nenhum teste, então
qualquer erro de renderização passava despercebido — foi exatamente o que
aconteceu no upgrade starlette 0.41 -> 1.3, que mudou a assinatura de
TemplateResponse (o request virou o primeiro argumento posicional). Os 71
testes ficaram verdes e as duas páginas devolviam 500; só apareceu ao
abrir a aplicação de verdade no container.
"""


class TestPaginaPublica:
    async def test_index_renderiza(self, client):
        res = await client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    async def test_index_tem_as_quatro_etapas(self, client):
        """O fluxo é cadastro -> OTP -> candidatos -> LGPD."""
        html = (await client.get("/")).text
        for step in ("step1", "step2", "step3", "step4"):
            assert f'id="{step}"' in html

    async def test_index_interpola_o_contexto(self, client):
        """
        app_name vem do contexto do template. Se o contexto não chegar ao
        Jinja, o título fica vazio em vez de quebrar — por isso a asserção
        é no valor, não só no status 200.
        """
        html = (await client.get("/")).text
        assert "SEMPRE TRICOLOR" in html

    async def test_index_carrega_o_js_do_fluxo(self, client):
        html = (await client.get("/")).text
        assert "/static/js/app.js" in html


class TestPaginaAdmin:
    async def test_admin_renderiza(self, client):
        res = await client.get("/admin")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    async def test_admin_tem_login_e_dashboard(self, client):
        html = (await client.get("/admin")).text
        assert 'id="loginSection"' in html
        assert 'id="dashboardSection"' in html

    async def test_admin_interpola_o_contexto(self, client):
        html = (await client.get("/admin")).text
        assert "SEMPRE TRICOLOR" in html

    async def test_admin_carrega_o_js_do_painel(self, client):
        html = (await client.get("/admin")).text
        assert "/static/js/admin.js" in html


class TestEstaticos:
    async def test_css_do_fluxo_publico(self, client):
        res = await client.get("/static/css/style.css")
        assert res.status_code == 200

    async def test_css_do_admin(self, client):
        res = await client.get("/static/css/admin.css")
        assert res.status_code == 200

    async def test_js_do_fluxo_publico(self, client):
        res = await client.get("/static/js/app.js")
        assert res.status_code == 200

    async def test_js_do_admin(self, client):
        res = await client.get("/static/js/admin.js")
        assert res.status_code == 200
