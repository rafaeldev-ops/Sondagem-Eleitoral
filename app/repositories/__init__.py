from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Associado,
    AuditLog,
    Candidato,
    Departamento,
    Preferencia,
    Resposta,
    WebhookLog,
)
from app.utils.cpf import normalize_cpf
from app.utils.socio import normalize_numero_socio


class AssociadoRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_cpf(self, cpf: str) -> Associado | None:
        result = await self.db.execute(
            select(Associado).where(Associado.cpf == normalize_cpf(cpf))
        )
        return result.scalar_one_or_none()

    async def get_by_numero_socio(self, numero_socio: str) -> Associado | None:
        result = await self.db.execute(
            select(Associado).where(
                Associado.numero_socio == normalize_numero_socio(numero_socio)
            )
        )
        return result.scalar_one_or_none()

    async def create(self, associado: Associado) -> Associado:
        self.db.add(associado)
        await self.db.flush()
        return associado

    async def count(self) -> int:
        result = await self.db.execute(select(func.count()).select_from(Associado))
        return result.scalar_one()

    async def search_by_cpf(self, cpf_partial: str) -> list[Associado]:
        cpf = normalize_cpf(cpf_partial)
        result = await self.db.execute(
            select(Associado)
            .where(Associado.cpf.contains(cpf))
            .options(
                selectinload(Associado.respostas).selectinload(Resposta.candidato),
                selectinload(Associado.preferencia).selectinload(Preferencia.candidato_preferido),
            )
            .order_by(Associado.data_resposta.desc())
        )
        return list(result.scalars().all())

    async def list_all_with_details(self) -> list[Associado]:
        result = await self.db.execute(
            select(Associado)
            .options(
                selectinload(Associado.respostas).selectinload(Resposta.candidato),
                selectinload(Associado.preferencia).selectinload(Preferencia.candidato_preferido),
            )
            .order_by(Associado.data_resposta.desc())
        )
        return list(result.scalars().all())


class CandidatoRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_active(self) -> list[Candidato]:
        result = await self.db.execute(
            select(Candidato).where(Candidato.ativo.is_(True)).order_by(Candidato.nome)
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[Candidato]:
        result = await self.db.execute(select(Candidato).order_by(Candidato.nome))
        return list(result.scalars().all())

    async def get_by_id(self, candidato_id: int) -> Candidato | None:
        result = await self.db.execute(select(Candidato).where(Candidato.id == candidato_id))
        return result.scalar_one_or_none()

    async def create(self, candidato: Candidato) -> Candidato:
        self.db.add(candidato)
        await self.db.flush()
        return candidato

    async def update(self, candidato: Candidato) -> Candidato:
        await self.db.flush()
        return candidato

    async def count_active(self) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(Candidato).where(Candidato.ativo.is_(True))
        )
        return result.scalar_one()

    async def count_all(self) -> int:
        result = await self.db.execute(select(func.count()).select_from(Candidato))
        return result.scalar_one()


class DepartamentoRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_active(self) -> list[Departamento]:
        result = await self.db.execute(
            select(Departamento)
            .where(Departamento.ativo.is_(True))
            .order_by(Departamento.ordem)
        )
        return list(result.scalars().all())


class RespostaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_bulk(self, respostas: list[Resposta]) -> None:
        self.db.add_all(respostas)
        await self.db.flush()


class PreferenciaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, preferencia: Preferencia) -> Preferencia:
        self.db.add(preferencia)
        await self.db.flush()
        return preferencia


class WebhookLogRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, log: WebhookLog) -> WebhookLog:
        self.db.add(log)
        await self.db.flush()
        return log

    async def list_pending(self) -> list[WebhookLog]:
        result = await self.db.execute(
            select(WebhookLog).where(WebhookLog.status.in_(["pending", "failed"]))
        )
        return list(result.scalars().all())

    async def update(self, log: WebhookLog) -> WebhookLog:
        await self.db.flush()
        return log


class AuditLogRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, evento: str, detalhes: str | None, ip: str | None, user_agent: str | None) -> None:
        self.db.add(
            AuditLog(
                evento=evento,
                detalhes=detalhes,
                ip=ip,
                user_agent=user_agent,
            )
        )
        await self.db.flush()
