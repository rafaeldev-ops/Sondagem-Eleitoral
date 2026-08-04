import json
import logging

import httpx

from app.core.config import get_settings
from app.schemas import PipefyPayload
from app.utils.cpf import mask_cpf

logger = logging.getLogger(__name__)


class PipefyService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def send_webhook(self, payload: PipefyPayload) -> tuple[bool, str | None]:
        if not self.settings.pipefy_webhook_url:
            logger.warning("PIPEFY_WEBHOOK_URL não configurada")
            return False, "Webhook URL não configurada"

        headers = {"Content-Type": "application/json"}
        if self.settings.pipefy_api_token:
            headers["Authorization"] = f"Bearer {self.settings.pipefy_api_token}"

        body = payload.model_dump()

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self.settings.pipefy_webhook_url,
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
            logger.info(
                "Pipefy webhook enviado com sucesso para CPF %s", mask_cpf(payload.cpf)
            )
            return True, None
        except Exception as exc:
            error_msg = str(exc)
            logger.exception("Falha ao enviar webhook Pipefy: %s", error_msg)
            return False, error_msg

    @staticmethod
    def serialize_payload(payload: PipefyPayload) -> str:
        return json.dumps(payload.model_dump(), ensure_ascii=False)

    @staticmethod
    def deserialize_payload(data: str) -> PipefyPayload:
        return PipefyPayload(**json.loads(data))
