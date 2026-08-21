import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
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
from app.database.session import engine

settings = get_settings()
# Logs em JSON fora de modo debug: formato esperado por agregadores de log
# em produção. Em desenvolvimento mantém o formato de uma linha legível.
setup_logging(json_logs=not settings.debug)
logger = logging.getLogger(__name__)

limiter.default_limits = [settings.rate_limit_default]

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Prefixo em que a aplicação inteira é montada ("/pesquisa2026" em
# produção, vazio na raiz). Já vem normalizado pelo Settings.
PATH_PREFIX = settings.app_path_prefix

# Criados AQUI, no import, e não no lifespan: o app.mount() de /uploads lá
# embaixo também roda no import, e StaticFiles levanta
# "Directory '...' does not exist" já no construtor. Como o lifespan só roda
# depois, criar os diretórios lá dentro chegava tarde demais.
#
# Não é hipótese: o git versiona só `uploads/.gitkeep`, e UPLOAD_DIR aponta
# por padrão para `uploads/candidatos` — um subdiretório. Num clone novo a
# aplicação não subia, e a suíte inteira virava erro de conexão recusada
# (o servidor de teste morria antes de abrir a porta). Quem recebesse o
# repositório batia nisso no primeiro `docker compose up`.
#
# parents=True cobre UPLOAD_DIR apontando para caminho aninhado;
# exist_ok=True torna a chamada idempotente a cada import.
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)


def _calcular_asset_version() -> str:
    """
    Impressão digital do CSS e do JS servidos, para entrar como `?v=` nos
    links dos templates.

    Sem isto, o navegador reaproveita o arquivo em cache e mistura HTML novo
    com script velho — que é bem pior do que só ficar com o visual antigo.
    O app.js anterior procurava `<select id="preferido">`; num HTML onde esse
    elemento não existe mais, a busca devolve null, o acesso estoura
    TypeError no topo do arquivo e NENHUM handler chega a ser registrado.
    A página carrega bonita e nenhum botão funciona. Foi exatamente assim
    que os botões novos de exportação do admin pareceram "não funcionar".

    Usa tamanho + mtime em vez do conteúdo: não precisa ler os arquivos, e
    qualquer edição muda pelo menos um dos dois. Calculado uma vez no import
    — dentro de um mesmo processo os estáticos não mudam, e no Docker a
    imagem é reconstruída a cada deploy de qualquer forma.
    """
    marcas = []
    for caminho in sorted((BASE_DIR / "static").rglob("*")):
        # uploads/ é conteúdo enviado pelo admin, não asset da aplicação:
        # incluir invalidaria o cache de todo mundo a cada foto nova.
        if caminho.is_file() and "uploads" not in caminho.parts:
            st = caminho.stat()
            marcas.append(f"{caminho.name}:{st.st_size}:{int(st.st_mtime)}")
    return hashlib.sha256("|".join(marcas).encode()).hexdigest()[:12]


ASSET_VERSION = _calcular_asset_version()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Aplicação iniciada: %s", settings.app_name)
    yield

    await redis_service.close()
    await engine.dispose()
    logger.info("Aplicação encerrada")


app = FastAPI(
    title=settings.app_name,
    description="Sondagem de intenção de votos para associados de clube",
    version="1.0.0",
    lifespan=lifespan,
    # Sob o prefixo, como todo o resto: chumbada na raiz, a documentação
    # interativa seria a única parte da aplicação servida fora dele — num
    # caminho do domínio que pertence ao site institucional.
    docs_url=f"{PATH_PREFIX}/api/docs" if settings.debug else None,
    redoc_url=f"{PATH_PREFIX}/api/redoc" if settings.debug else None,
    # O openapi.json continua servido fora de debug, como era antes desta
    # mudança — o que muda aqui é só o lugar. Desligá-lo seria outra
    # decisão, e não é a que está sendo tomada agora.
    openapi_url=f"{PATH_PREFIX}/openapi.json",
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

# Estáticos e uploads entram no prefixo junto com o resto: fora dele, a
# raiz do domínio pertence a outro site, e pedir /static/js/app.js lá
# devolveria o 404 (ou o HTML) daquele site em vez do nosso arquivo.
app.mount(
    f"{PATH_PREFIX}/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
app.mount(
    f"{PATH_PREFIX}/uploads",
    StaticFiles(directory=str(Path(settings.upload_dir).resolve())),
    name="uploads",
)

# O health é o único que fica nos DOIS lugares, e cada cópia tem um
# consumidor real:
#
#   - na raiz, porque o HEALTHCHECK do docker-compose bate em
#     localhost:8000/health de dentro do container, onde não existe Nginx
#     nem prefixo. Movê-lo só para o prefixo põe o container em loop de
#     restart em produção.
#   - sob o prefixo, porque com a raiz do domínio entregue a outro site um
#     monitor externo de uptime não alcança mais o /health por lá.
#
# Quando PATH_PREFIX é vazio as duas inclusões seriam idênticas e o
# FastAPI registraria a rota duas vezes (a segunda nunca alcançada), então
# a segunda só acontece se houver prefixo de verdade.
app.include_router(health.router)
if PATH_PREFIX:
    app.include_router(health.router, prefix=PATH_PREFIX)

app.include_router(survey.router, prefix=PATH_PREFIX)
app.include_router(admin.router, prefix=PATH_PREFIX)


# As duas páginas HTML num APIRouter, e não em @app.get direto, só para
# poderem receber o mesmo PATH_PREFIX das rotas de API. O decorador é
# avaliado no import, então montar o prefixo na mão dentro dele
# (@app.get(f"{PATH_PREFIX}/")) funcionaria, mas espalharia a concatenação
# por cada rota nova.
pages = APIRouter(tags=["Páginas"])


# A assinatura de TemplateResponse mudou no starlette: o request passou a
# ser o PRIMEIRO argumento posicional, e o contexto não o carrega mais
# dentro de si. Na forma antiga — TemplateResponse(nome, {"request": ...})
# — o starlette novo lê o nome do template como se fosse o request e o
# dicionário de contexto como se fosse o nome, e quebra com
# "TypeError: unhashable type: 'dict'" ao tentar usar o dict como chave de
# cache do Jinja.
@pages.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "recaptcha_site_key": settings.recaptcha_site_key,
            "app_name": settings.app_name,
            "asset_version": ASSET_VERSION,
            "base_path": PATH_PREFIX,
        },
    )


@pages.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "app_name": settings.app_name,
            "asset_version": ASSET_VERSION,
            "base_path": PATH_PREFIX,
        },
    )


# Incluído por último de propósito: a rota "/" deste router casa com o
# prefixo inteiro, então registrá-la antes dos mounts de estáticos faria
# ela sombrear nada — mas manter a ordem "mounts, API, páginas" deixa
# explícito que a página é o fallback do prefixo, não o contrário.
app.include_router(pages, prefix=PATH_PREFIX)
