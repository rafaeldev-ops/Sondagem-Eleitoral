import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.deps import redis_service
from app.api.routes import admin, health, survey
from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.logging_config import setup_logging
from app.middlewares.request_id import RequestIdMiddleware
from app.middlewares.security_headers import SecurityHeadersMiddleware
from app.services.otp_service import OTPService
from app.services.survey_service import SurveyService
from app.database.session import AsyncSessionLocal, engine

settings = get_settings()
# Logs em JSON fora de modo debug: formato esperado por agregadores de log
# em produção. Em desenvolvimento mantém o formato de uma linha legível.
setup_logging(json_logs=not settings.debug)
logger = logging.getLogger(__name__)

limiter.default_limits = [settings.rate_limit_default]

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def webhook_retry_worker() -> None:
    """Background task to retry failed webhook deliveries."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                otp_service = OTPService(redis_service)
                service = SurveyService(db, otp_service)
                count = await service.retry_pending_webhook()
                if count:
                    logger.info("Webhook retry worker: %d envio(s) reprocessado(s)", count)
                await db.commit()
        except Exception as exc:
            logger.exception("Erro no worker de webhook: %s", exc)

        await asyncio.sleep(settings.webhook_retry_delay_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    static_uploads = BASE_DIR / "static" / "uploads" / "candidatos"
    static_uploads.mkdir(parents=True, exist_ok=True)

    retry_task = asyncio.create_task(webhook_retry_worker())
    logger.info("Aplicação iniciada: %s", settings.app_name)
    yield

    # Shutdown gracioso: cancela o worker de background e espera ele
    # realmente parar (não só dispara cancel() e segue em frente) antes de
    # fechar as conexões que ele usa — evita "connection already closed" no
    # meio de uma iteração do worker se o shutdown pegar ele no meio de uma
    # tentativa de retry do webhook.
    retry_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await retry_task

    await redis_service.close()
    await engine.dispose()
    logger.info("Aplicação encerrada")


app = FastAPI(
    title=settings.app_name,
    description="Sondagem de intenção de votos para associados de clube",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url="/api/redoc" if settings.debug else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(SecurityHeadersMiddleware)
# Adicionado por último = executa primeiro (Starlette monta a pilha de
# middlewares na ordem inversa). O request id precisa existir antes de
# qualquer outro middleware logar, e o header X-Request-ID deve sair na
# resposta mesmo quando o rate limiter corta a requisição antes das rotas.
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount(
    "/uploads",
    StaticFiles(directory=str(Path(settings.upload_dir).resolve())),
    name="uploads",
)

app.include_router(health.router)
app.include_router(survey.router)
app.include_router(admin.router)


# A assinatura de TemplateResponse mudou no starlette: o request passou a
# ser o PRIMEIRO argumento posicional, e o contexto não o carrega mais
# dentro de si. Na forma antiga — TemplateResponse(nome, {"request": ...})
# — o starlette novo lê o nome do template como se fosse o request e o
# dicionário de contexto como se fosse o nome, e quebra com
# "TypeError: unhashable type: 'dict'" ao tentar usar o dict como chave de
# cache do Jinja.
@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "recaptcha_site_key": settings.recaptcha_site_key,
            "app_name": settings.app_name,
        },
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"app_name": settings.app_name},
    )
