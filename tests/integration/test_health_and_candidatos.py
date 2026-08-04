class TestHealth:
    async def test_health_returns_ok(self, client):
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    async def test_readiness_checks_dependencies(self, client):
        res = await client.get("/health/ready")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] is True
        assert body["checks"]["redis"] is True


class TestCandidatos:
    async def test_list_empty_by_default(self, client):
        res = await client.get("/api/survey/candidatos")
        assert res.status_code == 200
        assert res.json() == []

    async def test_list_returns_seeded_candidatos(self, client, db_session):
        from app.models import Candidato

        db_session.add(Candidato(nome="Maria Santos", apelido="Mari", ativo=True))
        db_session.add(Candidato(nome="Inativo", apelido="Nao aparece", ativo=False))
        await db_session.commit()

        res = await client.get("/api/survey/candidatos")
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 1
        assert body[0]["nome"] == "Maria Santos"
