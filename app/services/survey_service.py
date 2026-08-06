import csv
import io
import logging
from datetime import UTC, datetime

from openpyxl import Workbook
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.webhook import WebhookService
from app.models import Associado, Preferencia, Resposta, WebhookLog
from app.repositories import (
    AssociadoRepository,
    AuditLogRepository,
    CandidatoRepository,
    PreferenciaRepository,
    RespostaRepository,
    WebhookLogRepository,
)
from app.schemas import WebhookPayload
from app.services.otp_service import OTPService
from app.utils.cpf import format_cpf

logger = logging.getLogger(__name__)


def _mensagem_para_erro_de_unicidade(detalhe_erro: str) -> str:
    """
    Escolhe a mensagem certa a partir do texto de uma violação de UNIQUE
    constraint em `associados` (ou seja, `str(exc.orig)` de um
    IntegrityError capturado em `submit_vote`).

    Casa o nome da COLUNA, não o da constraint: o texto do driver traz os
    dois (nome da constraint + "DETAIL: Key (coluna)=..."), e casar pela
    coluna sobrevive a uma renomeação de constraint. É também o único jeito
    que funciona nos dois ambientes ao mesmo tempo — Alembic (produção) nomeia
    a constraint de cpf como "associados_cpf_key", enquanto create_all (usado
    pelos testes) não cria constraint nenhuma para cpf, só o índice único
    "ix_associados_cpf" (efeito colateral de `unique=True` + `index=True`
    juntos no mapeamento); só "numero_socio" tem o mesmo nome de constraint
    nos dois, por ser nomeada explicitamente em `__table_args__`.

    ATENÇÃO: quem chama esta função nunca deve logar `detalhe_erro` — ele
    contém o valor que violou a constraint, ou seja, o CPF completo em
    texto puro.
    """
    if "numero_socio" in detalhe_erro:
        return "Este número de sócio já participou da sondagem"
    return "Este CPF já participou da sondagem"


class SurveyService:
    def __init__(self, db: AsyncSession, otp_service: OTPService) -> None:
        self.db = db
        self.otp_service = otp_service
        self.associado_repo = AssociadoRepository(db)
        self.candidato_repo = CandidatoRepository(db)
        self.resposta_repo = RespostaRepository(db)
        self.preferencia_repo = PreferenciaRepository(db)
        self.webhook_log_repo = WebhookLogRepository(db)
        self.audit_repo = AuditLogRepository(db)
        self.webhook_service = WebhookService()

    async def check_cpf_available(self, cpf: str) -> tuple[bool, str | None]:
        existing = await self.associado_repo.get_by_cpf(cpf)
        if existing:
            return False, "Este CPF já participou da sondagem"
        return True, None

    async def check_numero_socio_available(self, numero_socio: str) -> tuple[bool, str | None]:
        existing = await self.associado_repo.get_by_numero_socio(numero_socio)
        if existing:
            return False, "Este número de sócio já participou da sondagem"
        return True, None

    async def register_and_send_otp(
        self,
        nome: str,
        cpf: str,
        telefone: str,
        numero_socio: str,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[str | None, str | None]:
        available, msg = await self.check_cpf_available(cpf)
        if not available:
            return None, msg

        # Checado aqui, e não só no submit, para não gastar um SMS num
        # cadastro que seria rejeitado no fim do fluxo.
        available, msg = await self.check_numero_socio_available(numero_socio)
        if not available:
            return None, msg

        session_token = await self.otp_service.create_session(
            {
                "nome": nome,
                "cpf": cpf,
                "telefone": telefone,
                "numero_socio": numero_socio,
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
        numero_socio = session["numero_socio"]

        available, msg = await self.check_cpf_available(cpf)
        if not available:
            return False, msg

        available, msg = await self.check_numero_socio_available(numero_socio)
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
            numero_socio=numero_socio,
            telefone=session["telefone"],
            ip=ip or session.get("ip"),
            user_agent=user_agent or session.get("user_agent"),
            aceite_lgpd=aceite_lgpd,
        )
        try:
            associado = await self.associado_repo.create(associado)
        except IntegrityError as exc:
            # As checagens acima são otimistas — não há lock entre o SELECT e
            # este INSERT, e entre os dois ainda passa todo o fluxo de OTP.
            # As constraints UNIQUE do banco são quem realmente garante um
            # voto por CPF e um por número de sócio; aqui só traduzimos a
            # violação numa mensagem que diz qual dos dois repetiu (ver
            # _mensagem_para_erro_de_unicidade acima para a lógica e o
            # motivo de nunca logar esse texto).
            await self.db.rollback()
            return False, _mensagem_para_erro_de_unicidade(str(exc.orig))

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

        payload = WebhookPayload(
            nome=associado.nome,
            numero_socio=associado.numero_socio,
            cpf=format_cpf(associado.cpf),
            telefone=associado.telefone,
            candidatos=candidatos_nomes,
            preferido=preferido_nome,
            aceite_lgpd=aceite_lgpd,
            data=associado.data_resposta.isoformat(),
        )

        await self._enqueue_webhook(associado.id, payload)
        await self.otp_service.delete_session(session_token)

        await self.audit_repo.create(
            "vote_submitted",
            f"CPF {cpf[-4:]} votou",
            ip,
            user_agent,
        )
        return True, None

    async def _enqueue_webhook(self, associado_id: int, payload: WebhookPayload) -> None:
        log = WebhookLog(
            associado_id=associado_id,
            payload=self.webhook_service.serialize_payload(payload),
            status="pending",
        )
        await self.webhook_log_repo.create(log)

        success, error = await self.webhook_service.send_webhook(payload)
        log.tentativas += 1
        if success:
            log.status = "sent"
            log.enviado_em = datetime.now(UTC)
        else:
            log.status = "failed"
            log.ultimo_erro = error
        await self.webhook_log_repo.update(log)

    async def retry_pending_webhook(self) -> int:
        pending = await self.webhook_log_repo.list_pending()
        retried = 0
        settings = self.webhook_service.settings

        for log in pending:
            if log.tentativas >= settings.webhook_retry_max:
                continue

            payload = self.webhook_service.deserialize_payload(log.payload)
            success, error = await self.webhook_service.send_webhook(payload)
            log.tentativas += 1

            if success:
                log.status = "sent"
                log.enviado_em = datetime.now(UTC)
                log.ultimo_erro = None
            else:
                log.status = "failed"
                log.ultimo_erro = error

            await self.webhook_log_repo.update(log)
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
            ["ID", "Nº Sócio", "Nome", "CPF", "Telefone", "Candidatos", "Preferido", "Data", "LGPD"]
        )

        for a in associados:
            candidatos = ", ".join(r.candidato.nome for r in a.respostas)
            preferido = a.preferencia.candidato_preferido.nome if a.preferencia else ""
            writer.writerow(
                [
                    a.id,
                    a.numero_socio,
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
        ws.append(
            ["ID", "Nº Sócio", "Nome", "CPF", "Telefone", "Candidatos", "Preferido", "Data", "LGPD"]
        )

        for a in associados:
            candidatos = ", ".join(r.candidato.nome for r in a.respostas)
            preferido = a.preferencia.candidato_preferido.nome if a.preferencia else ""
            ws.append(
                [
                    a.id,
                    a.numero_socio,
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
