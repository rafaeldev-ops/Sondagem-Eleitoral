from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="SEMPRE TRICOLOR", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    secret_key: str = Field(alias="SECRET_KEY")
    allowed_origins: str = Field(
        default="http://localhost:8000",
        alias="ALLOWED_ORIGINS",
    )

    database_url: str = Field(alias="DATABASE_URL")
    database_url_sync: str = Field(alias="DATABASE_URL_SYNC")
    redis_url: str = Field(alias="REDIS_URL")

    otp_provider: Literal["twilio", "zenvia", "zapi", "mock"] = Field(
        default="mock",
        alias="OTP_PROVIDER",
    )
    otp_expiry_seconds: int = Field(default=300, alias="OTP_EXPIRY_SECONDS")
    otp_max_attempts: int = Field(default=5, alias="OTP_MAX_ATTEMPTS")
    otp_resend_cooldown_seconds: int = Field(
        default=60,
        alias="OTP_RESEND_COOLDOWN_SECONDS",
    )

    twilio_account_sid: str = Field(default="", alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(default="", alias="TWILIO_AUTH_TOKEN")
    twilio_from_number: str = Field(default="", alias="TWILIO_FROM_NUMBER")

    zenvia_api_token: str = Field(default="", alias="ZENVIA_API_TOKEN")
    zenvia_from: str = Field(default="", alias="ZENVIA_FROM")

    zapi_instance_id: str = Field(default="", alias="ZAPI_INSTANCE_ID")
    zapi_token: str = Field(default="", alias="ZAPI_TOKEN")
    zapi_client_token: str = Field(default="", alias="ZAPI_CLIENT_TOKEN")

    recaptcha_secret_key: str = Field(default="", alias="RECAPTCHA_SECRET_KEY")
    recaptcha_site_key: str = Field(default="", alias="RECAPTCHA_SITE_KEY")
    recaptcha_min_score: float = Field(default=0.5, alias="RECAPTCHA_MIN_SCORE")

    admin_username: str = Field(alias="ADMIN_USERNAME")
    admin_password: str = Field(alias="ADMIN_PASSWORD")
    admin_jwt_expire_minutes: int = Field(
        default=480,
        alias="ADMIN_JWT_EXPIRE_MINUTES",
    )

    rate_limit_default: str = Field(default="60/minute", alias="RATE_LIMIT_DEFAULT")
    rate_limit_otp: str = Field(default="10/minute", alias="RATE_LIMIT_OTP")
    rate_limit_admin_login: str = Field(default="5/minute", alias="RATE_LIMIT_ADMIN_LOGIN")
    # Limites das rotas públicas de sondagem. Estavam hardcoded em
    # app/api/routes/survey.py, o que impedia afrouxá-los em ambiente de
    # teste de carga (e apertá-los em produção) sem editar código. Os
    # defaults abaixo são exatamente os valores que já estavam no código —
    # nenhuma mudança de comportamento para quem não define as variáveis.
    rate_limit_validate_cpf: str = Field(default="30/minute", alias="RATE_LIMIT_VALIDATE_CPF")
    rate_limit_register: str = Field(default="10/minute", alias="RATE_LIMIT_REGISTER")
    rate_limit_verify_otp: str = Field(default="15/minute", alias="RATE_LIMIT_VERIFY_OTP")
    rate_limit_resend_otp: str = Field(default="5/minute", alias="RATE_LIMIT_RESEND_OTP")
    rate_limit_submit: str = Field(default="10/minute", alias="RATE_LIMIT_SUBMIT")
    https_only: bool = Field(default=False, alias="HTTPS_ONLY")
    trust_proxy_headers: bool = Field(default=False, alias="TRUST_PROXY_HEADERS")

    upload_dir: str = Field(default="uploads/candidatos", alias="UPLOAD_DIR")
    max_upload_size_mb: int = Field(default=5, alias="MAX_UPLOAD_SIZE_MB")

    @model_validator(mode="after")
    def _validar_admin_password_hash(self) -> "Settings":
        """
        Fora de development, recusa subir a aplicação se ADMIN_PASSWORD não
        for um hash bcrypt (prefixo "$2"). Antes deste validador, um
        ADMIN_PASSWORD em texto puro era silenciosamente aceito em
        qualquer ambiente (ver app/api/routes/admin.py, comparação com
        `==` em vez de verify_password) — bastava esquecer de rodar
        scripts/hash_password.py para a senha do admin ficar em texto
        puro no .env e nas comparações de login.
        """
        if self.app_env != "development" and not self.admin_password.startswith("$2"):
            raise ValueError(
                "ADMIN_PASSWORD precisa ser um hash bcrypt fora de "
                "APP_ENV=development. Gere um com: "
                "python scripts/hash_password.py \"sua-senha\""
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
