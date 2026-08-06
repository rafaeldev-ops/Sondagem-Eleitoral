import json
import logging

import httpx

from app.core.config import get_settings
from app.schemas import WebhookPayload
from app.utils.cpf import mask_cpf

logger = logging.getLogger(__name__)


class WebhookService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def send_webhook(self, payload: WebhookPayload) -> tuple[bool, str | None]:
        if not self.settings.webhook_url:
            logger.warning("WEBHOOK_URL não configurada")
            return False, "Webhook URL não configurada"

        headers = {"Content-Type": "application/json"}
        if self.settings.webhook_token:
            headers["Authorization"] = f"Bearer {self.settings.webhook_token}"

        body = payload.model_dump()

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self.settings.webhook_url,
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
            logger.info(
                "Webhook enviado com sucesso para CPF %s", mask_cpf(payload.cpf)
            )
            return True, None
        except Exception as exc:
            error_msg = str(exc)
            logger.exception("Falha ao enviar webhook: %s", error_msg)
            return False, error_msg

    @staticmethod
    def serialize_payload(payload: WebhookPayload) -> str:
        return json.dumps(payload.model_dump(), ensure_ascii=False)

    @staticmethod
    def deserialize_payload(data: str) -> WebhookPayload:
        return WebhookPayload(**json.loads(data))
