import csv
import io
import logging
from datetime import UTC, datetime

from openpyxl import Workbook
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pipefy import PipefyService
from app.models import Associado, PipefyLog, Preferencia, Resposta
from app.repositories import (
    AssociadoRepository,
    AuditLogRepository,
    CandidatoRepository,
    PipefyLogRepository,
    PreferenciaRepository,
    RespostaRepository,
)
from app.schemas import PipefyPayload
from app.services.otp_service import OTPService
from app.utils.cpf import format_cpf

logger = logging.getLogger(__name__)


class SurveyService:
    def __init__(self, db: AsyncSession, otp_service: OTPService) -> None:
        self.db = db
        self.otp_service = otp_service
        self.associado_repo = AssociadoRepository(db)
        self.candidato_repo = CandidatoRepository(db)
        self.resposta_repo = RespostaRepository(db)
        self.preferencia_repo = PreferenciaRepository(db)
        self.pipefy_log_repo = PipefyLogRepository(db)
        self.audit_repo = AuditLogRepository(db)
        self.pipefy_service = PipefyService()

    async def check_cpf_available(self, cpf: str) -> tuple[bool, str | None]:
        existing = await self.associado_repo.get_by_cpf(cpf)
        if existing:
            return False, "Este CPF já participou da sondagem"
        return True, None

    async def register_and_send_otp(
        self,
        nome: str,
        cpf: str,
        telefone: str,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[str | None, str | None]:
        available, msg = await self.check_cpf_available(cpf)
        if not available:
            return None, msg

        session_token = await self.otp_service.create_session(
            {
                "nome": nome,
                "cpf": cpf,
                "telefone": telefone,
                "verified": False,
                "ip": ip,
                "user_agent": user_agent,
            }
        )

        success, error = await self.otp_service.send_otp(telefone)
        if not success:
            await self.otp_service.delete_session(session_token)
            return None, error

        await self.audit_repo.create(
            "otp_sent",
            f"OTP enviado para telefone {telefone[-4:]}",
            ip,
            user_agent,
        )
        return session_token, None

    async def verify_otp(
        self,
        telefone: str,
        codigo: str,
        session_token: str,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[bool, str | None]:
        session = await self.otp_service.get_session(session_token)
        if not session:
            return False, "Sessão expirada. Reinicie o cadastro."

        if session.get("telefone") != telefone:
            return False, "Telefone não corresponde à sessão"

        valid, error = await self.otp_service.verify_otp(telefone, codigo)
        if not valid:
            await self.audit_repo.create("otp_failed", error, ip, user_agent)
            return False, error

        session["verified"] = True
        await self.otp_service.update_session(session_token, session, ttl=3600)

        await self.audit_repo.create("otp_verified", f"Telefone {telefone[-4:]}", ip, user_agent)
        return True, None

    async def resend_otp(
        self,
        telefone: str,
        session_token: str,
    ) -> tuple[bool, str | None]:
        session = await self.otp_service.get_session(session_token)
        if not session:
            return False, "Sessão expirada. Reinicie o cadastro."

        if session.get("telefone") != telefone:
            return False, "Telefone não corresponde à sessão"

        return await self.otp_service.send_otp(telefone)

    async def submit_vote(
        self,
        session_token: str,
        candidatos_ids: list[int],
        candidato_preferido_id: int,
        aceite_lgpd: bool,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[bool, str | None]:
        session = await self.otp_service.get_session(session_token)
        if not session or not session.get("verified"):
            return False, "Sessão inválida ou não autenticada"

        cpf = session["cpf"]
        available, msg = await self.check_cpf_available(cpf)
        if not available:
            return False, msg

        candidatos = await self.candidato_repo.list_active()
        active_ids = {c.id for c in candidatos}
        candidato_map = {c.id: c for c in candidatos}

        if not all(cid in active_ids for cid in candidatos_ids):
            return False, "Um ou mais candidatos selecionados são inválidos"

        if candidato_preferido_id not in active_ids:
            return False, "Candidato preferencial inválido"

        if candidato_preferido_id not in candidatos_ids:
            return False, "O candidato preferencial deve estar entre os selecionados"

        associado = Associado(
            nome=session["nome"],
            cpf=cpf,
            telefone=session["telefone"],
            ip=ip or session.get("ip"),
            user_agent=user_agent or session.get("user_agent"),
            aceite_lgpd=aceite_lgpd,
        )
        try:
            associado = await self.associado_repo.create(associado)
        except IntegrityError:
            # check_cpf_available() acima é um check "otimista" — não há lock
            # a nível de transação entre o SELECT e este INSERT, então duas
            # requisições concorrentes com o mesmo CPF podem ambas passar no
            # check antes de qualquer uma commitar. A constraint UNIQUE do
            # banco (ver alembic/versions/001_initial_schema.py) é quem
            # realmente garante "um voto por CPF" — aqui só traduzimos a
            # violação dela em uma mensagem amigável em vez de deixar
            # propagar como 500 genérico.
            await self.db.rollback()
            return False, "Este CPF já participou da sondagem"

        respostas = [
            Resposta(associado_id=associado.id, candidato_id=cid) for cid in candidatos_ids
        ]
        await self.resposta_repo.create_bulk(respostas)

        await self.preferencia_repo.create(
            Preferencia(
                associado_id=associado.id,
                candidato_preferido_id=candidato_preferido_id,
            )
        )

        candidatos_nomes = [candidato_map[cid].nome for cid in candidatos_ids]
        preferido_nome = candidato_map[candidato_preferido_id].nome

        payload = PipefyPayload(
            nome=associado.nome,
            cpf=format_cpf(associado.cpf),
            telefone=associado.telefone,
            candidatos=candidatos_nomes,
            preferido=preferido_nome,
            aceite_lgpd=aceite_lgpd,
            data=associado.data_resposta.isoformat(),
        )

        await self._enqueue_pipefy(associado.id, payload)
        await self.otp_service.delete_session(session_token)

        await self.audit_repo.create(
            "vote_submitted",
            f"CPF {cpf[-4:]} votou",
            ip,
            user_agent,
        )
        return True, None

    async def _enqueue_pipefy(self, associado_id: int, payload: PipefyPayload) -> None:
        log = PipefyLog(
            associado_id=associado_id,
            payload=self.pipefy_service.serialize_payload(payload),
            status="pending",
        )
        await self.pipefy_log_repo.create(log)

        success, error = await self.pipefy_service.send_webhook(payload)
        log.tentativas += 1
        if success:
            log.status = "sent"
            log.enviado_em = datetime.now(UTC)
        else:
            log.status = "failed"
            log.ultimo_erro = error
        await self.pipefy_log_repo.update(log)

    async def retry_pending_pipefy(self) -> int:
        pending = await self.pipefy_log_repo.list_pending()
        retried = 0
        settings = self.pipefy_service.settings

        for log in pending:
            if log.tentativas >= settings.pipefy_retry_max:
                continue

            payload = self.pipefy_service.deserialize_payload(log.payload)
            success, error = await self.pipefy_service.send_webhook(payload)
            log.tentativas += 1

            if success:
                log.status = "sent"
                log.enviado_em = datetime.now(UTC)
                log.ultimo_erro = None
            else:
                log.status = "failed"
                log.ultimo_erro = error

            await self.pipefy_log_repo.update(log)
            retried += 1

        return retried


class ExportService:
    def __init__(self, db: AsyncSession) -> None:
        self.associado_repo = AssociadoRepository(db)

    async def export_csv(self) -> str:
        associados = await self.associado_repo.list_all_with_details()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["ID", "Nome", "CPF", "Telefone", "Candidatos", "Preferido", "Data", "LGPD"]
        )

        for a in associados:
            candidatos = ", ".join(r.candidato.nome for r in a.respostas)
            preferido = a.preferencia.candidato_preferido.nome if a.preferencia else ""
            writer.writerow(
                [
                    a.id,
                    a.nome,
                    format_cpf(a.cpf),
                    a.telefone,
                    candidatos,
                    preferido,
                    a.data_resposta.isoformat(),
                    "Sim" if a.aceite_lgpd else "Não",
                ]
            )

        return output.getvalue()

    async def export_excel(self) -> bytes:
        associados = await self.associado_repo.list_all_with_details()
        wb = Workbook()
        ws = wb.active
        ws.title = "Respostas"
        ws.append(["ID", "Nome", "CPF", "Telefone", "Candidatos", "Preferido", "Data", "LGPD"])

        for a in associados:
            candidatos = ", ".join(r.candidato.nome for r in a.respostas)
            preferido = a.preferencia.candidato_preferido.nome if a.preferencia else ""
            ws.append(
                [
                    a.id,
                    a.nome,
                    format_cpf(a.cpf),
                    a.telefone,
                    candidatos,
                    preferido,
                    a.data_resposta.isoformat(),
                    "Sim" if a.aceite_lgpd else "Não",
                ]
            )

        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()
