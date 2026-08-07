from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.utils.cpf import normalize_cpf, validate_cpf
from app.utils.phone import normalize_phone, validate_phone
from app.utils.sanitize import sanitize_text
from app.utils.socio import normalize_numero_socio, validate_numero_socio


class CPFValidateRequest(BaseModel):
    cpf: str

    @field_validator("cpf")
    @classmethod
    def validate_cpf_field(cls, value: str) -> str:
        cpf = normalize_cpf(value)
        if not validate_cpf(cpf):
            raise ValueError("CPF inválido")
        return cpf


class CadastroRequest(BaseModel):
    nome: str = Field(min_length=3, max_length=255)
    cpf: str
    numero_socio: str
    telefone: str
    # Sem default, ao contrário de recaptcha_token: o formulário sempre manda
    # os dois estados do checkbox (marcado/desmarcado), então payload sem o
    # campo é bug de frontend e deve estourar 422 na hora. Um default False
    # aqui gravaria "não é titular" para quem nunca foi perguntado — o mesmo
    # erro que a migration 007 evita no banco.
    titular: bool
    recaptcha_token: str = Field(default="")

    @field_validator("nome")
    @classmethod
    def sanitize_nome(cls, value: str) -> str:
        return sanitize_text(value, 255)

    @field_validator("cpf")
    @classmethod
    def validate_cpf_field(cls, value: str) -> str:
        cpf = normalize_cpf(value)
        if not validate_cpf(cpf):
            raise ValueError("CPF inválido")
        return cpf

    @field_validator("numero_socio")
    @classmethod
    def validate_numero_socio_field(cls, value: str) -> str:
        numero = normalize_numero_socio(value)
        if not validate_numero_socio(numero):
            raise ValueError("Número de sócio deve ter exatamente 4 dígitos")
        return numero

    @field_validator("telefone")
    @classmethod
    def validate_telefone(cls, value: str) -> str:
        phone = normalize_phone(value)
        if not validate_phone(phone):
            raise ValueError("Telefone inválido")
        return phone


class OTPVerifyRequest(BaseModel):
    telefone: str
    codigo: str = Field(min_length=6, max_length=6)
    session_token: str

    @field_validator("telefone")
    @classmethod
    def validate_telefone(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("codigo")
    @classmethod
    def validate_codigo(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("Código deve conter apenas dígitos")
        return value


class OTPResendRequest(BaseModel):
    telefone: str
    session_token: str

    @field_validator("telefone")
    @classmethod
    def validate_telefone(cls, value: str) -> str:
        return normalize_phone(value)


class VotoRequest(BaseModel):
    session_token: str
    candidatos_ids: list[int] = Field(min_length=1, max_length=20)
    candidato_preferido_id: int
    # min_length=1 é a obrigatoriedade da etapa; max_length=49 é o total de
    # opções da lista e existe só para recusar payload absurdo.
    departamentos_ids: list[int] = Field(min_length=1, max_length=49)
    departamento_outros: str = Field(default="", max_length=100)
    aceite_lgpd: bool

    @field_validator("aceite_lgpd")
    @classmethod
    def validate_lgpd(cls, value: bool) -> bool:
        if not value:
            raise ValueError("É necessário aceitar os termos da LGPD")
        return value

    @field_validator("departamento_outros")
    @classmethod
    def sanitize_departamento_outros(cls, value: str) -> str:
        # Primeiro texto livre do fluxo público — mesmo tratamento que o nome.
        return sanitize_text(value, 100)


class CandidatoPublic(BaseModel):
    id: int
    nome: str
    apelido: str
    foto: str | None

    model_config = {"from_attributes": True}


class CandidatoCreate(BaseModel):
    nome: str = Field(min_length=2, max_length=255)
    apelido: str = Field(min_length=1, max_length=100)
    ativo: bool = True

    @field_validator("nome", "apelido")
    @classmethod
    def sanitize_fields(cls, value: str) -> str:
        return sanitize_text(value, 255)


class CandidatoUpdate(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=255)
    apelido: str | None = Field(default=None, min_length=1, max_length=100)
    ativo: bool | None = None


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class StatsResponse(BaseModel):
    total_respostas: int
    total_candidatos_ativos: int
    total_candidatos: int


class AssociadoResponse(BaseModel):
    id: int
    nome: str
    cpf: str
    numero_socio: str
    telefone: str
    # None = respondeu antes da pergunta existir (ver migration 007).
    titular: bool | None = None
    data_resposta: datetime
    candidatos: list[str]
    preferido: str

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    message: str
    detail: str | None = None


class SessionResponse(BaseModel):
    session_token: str
    message: str


class WebhookPayload(BaseModel):
    nome: str
    # numero_socio, departamentos e departamento_outros têm default: o
    # worker de retry re-hidrata payloads GRAVADOS em webhook_logs
    # (deserialize_payload -> WebhookPayload(**json.loads(...))), e linhas
    # antigas não têm esses campos. Sem default, uma única linha antiga
    # trava o retry para sempre (ValidationError não tratado no loop do
    # SurveyService, antes do contador de tentativas ser incrementado).
    # Isso NÃO muda o payload de saída: submit_vote sempre define os três
    # explicitamente e model_dump() sempre emite todos os campos
    # declarados.
    numero_socio: str = ""
    cpf: str
    telefone: str
    # Mesmo motivo de numero_socio acima: default obrigatório porque este
    # schema também é o sentido de VOLTA (webhook_logs -> retry). False aqui
    # é só o valor de re-hidratação de payload antigo, que já foi montado e
    # gravado sem o campo; não é resposta de ninguém. Toda saída nova passa
    # pelo submit_vote, que sempre informa titular explicitamente.
    titular: bool = False
    candidatos: list[str]
    preferido: str
    departamentos: list[str] = Field(default_factory=list)
    # String vazia quando não se aplica, nunca None: o n8n trata campo
    # ausente e campo nulo de formas diferentes.
    departamento_outros: str = ""
    aceite_lgpd: bool
    data: str
