"""
Teste de carga do fluxo de sondagem (Locust).

Uso:
    pip install -r requirements-dev.txt
    locust -f tests/load/locustfile.py --host http://localhost:8000

    # modo headless, 100 usuários simultâneos, rampa de 10/s, por 2 minutos:
    locust -f tests/load/locustfile.py --host http://localhost:8000 \
        --headless -u 100 -r 10 -t 2m

IMPORTANTE — rodar SOMENTE contra ambiente de teste/homologação, nunca
contra produção com dados reais: cada usuário simulado grava um associado
no banco e consome um CPF (que fica marcado como "já votou").

Pré-requisitos no ambiente alvo:
- OTP_PROVIDER=mock e DEBUG=true (senão cada usuário simulado dispararia
  um SMS real — caro e abusivo com o provedor)
- rate limits afrouxados, senão o teste mede o rate limiter e não a
  aplicação (ver RATE_LIMIT_* no .env)
"""

import random

from locust import HttpUser, between, task


def _gerar_cpf_valido() -> str:
    """CPF com dígitos verificadores corretos — a API rejeita inválidos na
    camada de schema, então gerar aleatório puro só mediria a velocidade de
    resposta 422."""

    def calc(base: list[int]) -> int:
        peso = len(base) + 1
        total = sum(d * (peso - i) for i, d in enumerate(base))
        resto = total % 11
        return 0 if resto < 2 else 11 - resto

    base = [random.randint(0, 9) for _ in range(9)]
    d1 = calc(base)
    d2 = calc(base + [d1])
    return "".join(map(str, base)) + str(d1) + str(d2)


def _gerar_telefone() -> str:
    return f"11 9{random.randint(10000000, 99999999)}"


class SondagemUser(HttpUser):
    """Simula um associado percorrendo o fluxo completo da sondagem."""

    wait_time = between(1, 3)

    def on_start(self):
        self.candidatos = []
        res = self.client.get("/api/survey/candidatos", name="GET /candidatos")
        if res.status_code == 200:
            self.candidatos = res.json()

    @task(3)
    def fluxo_completo(self):
        cpf = _gerar_cpf_valido()
        telefone = _gerar_telefone()

        # Passo 1: checagem de CPF (o front dispara isso no blur do campo)
        self.client.post(
            "/api/survey/validate-cpf", json={"cpf": cpf}, name="POST /validate-cpf"
        )

        # Passo 2: cadastro + envio de OTP
        res = self.client.post(
            "/api/survey/register",
            json={
                "nome": f"Usuário Carga {random.randint(1, 999999)}",
                "cpf": cpf,
                "telefone": telefone,
                "recaptcha_token": "",
            },
            name="POST /register",
        )
        if res.status_code != 200:
            return

        # O fluxo real pararia aqui aguardando o código que chegou por SMS.
        # Um teste de carga não tem como ler esse código (ele só existe no
        # log do servidor), então medimos até aqui: registro + envio de OTP
        # é justamente o trecho mais pesado (escrita no banco, escrita no
        # Redis e chamada ao provedor externo).

    @task(1)
    def somente_leitura(self):
        """Carga de leitura pura — representa quem abre a página e sai, ou
        recarrega. Proporção 1:3 em relação ao fluxo completo."""
        self.client.get("/api/survey/candidatos", name="GET /candidatos")
        self.client.get("/health", name="GET /health")
