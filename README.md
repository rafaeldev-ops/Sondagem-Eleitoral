# Sondagem Clube — Sondagem de Intenção de Votos

Aplicação web mobile-first para sondagem de intenção de votos entre associados de um clube, com validação de CPF, autenticação OTP e painel administrativo.

## Stack

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy, PostgreSQL, Redis, Alembic
- **Frontend:** HTML5, CSS3, JavaScript, Bootstrap 5
- **Infra:** Docker, Docker Compose

## Início Rápido

### 1. Configurar ambiente

```bash
cp .env.example .env
# Edite .env com suas credenciais
```

### 2. Subir com Docker

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
```

Acesse:
- **Sondagem:** http://localhost:8000
- **Admin:** http://localhost:8000/admin
- **API Docs:** http://localhost:8000/api/docs

### 3. Desenvolvimento local (sem Docker)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# PostgreSQL e Redis devem estar rodando
alembic upgrade head
uvicorn app.main:app --reload
```

## Fluxo da Aplicação

1. **Cadastro** — Nome, CPF (validação em tempo real), número de sócio, celular e declaração de titular do grupo
2. **OTP** — Código de 6 dígitos via SMS (Twilio/Zenvia/Z-API)
3. **Candidatos** — Seleção múltipla (até 20)
4. **Ponto focal** — Escolha única entre os candidatos marcados na etapa 3
5. **Modalidades** — Departamentos/modalidades que o sócio frequenta
6. **LGPD** — Consentimento obrigatório

## Segurança

- Validação matemática de CPF
- Controle de duplicidade (1 voto por CPF)
- OTP com Redis (5 min, 5 tentativas, cooldown 60s)
- Google reCAPTCHA v3
- Rate limiting (SlowAPI)
- Headers de segurança (CSP, X-Frame-Options, etc.)
- Sanitização de entradas (Bleach)
- Logs de auditoria

## Documentação

- [Instalação](docs/INSTALACAO.md)
- [API](docs/API.md)
- [Deploy em Produção](docs/DEPLOY.md)

## Estrutura

```
app/
├── api/           # Rotas REST
├── core/          # Config, segurança, logging
├── database/      # Sessão SQLAlchemy
├── integrations/  # OTP providers, reCAPTCHA
├── middlewares/   # Headers de segurança
├── models/        # Modelos ORM
├── repositories/  # Acesso a dados
├── schemas/       # Pydantic schemas
├── services/      # Lógica de negócio
└── utils/         # CPF, telefone, OTP
```

## Licença

Projeto privado — uso interno do clube.
