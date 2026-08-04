from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Associado(Base):
    __tablename__ = "associados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    cpf: Mapped[str] = mapped_column(String(11), unique=True, nullable=False, index=True)
    telefone: Mapped[str] = mapped_column(String(20), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    aceite_lgpd: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_resposta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    respostas: Mapped[list["Resposta"]] = relationship(back_populates="associado")
    preferencia: Mapped["Preferencia | None"] = relationship(back_populates="associado")


class Candidato(Base):
    __tablename__ = "candidatos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    apelido: Mapped[str] = mapped_column(String(100), nullable=False)
    foto: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    respostas: Mapped[list["Resposta"]] = relationship(back_populates="candidato")


class Resposta(Base):
    __tablename__ = "respostas"
    __table_args__ = (UniqueConstraint("associado_id", "candidato_id", name="uq_resposta_associado_candidato"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    associado_id: Mapped[int] = mapped_column(ForeignKey("associados.id"), nullable=False)
    candidato_id: Mapped[int] = mapped_column(ForeignKey("candidatos.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    associado: Mapped["Associado"] = relationship(back_populates="respostas")
    candidato: Mapped["Candidato"] = relationship(back_populates="respostas")


class Preferencia(Base):
    __tablename__ = "preferencias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    associado_id: Mapped[int] = mapped_column(
        ForeignKey("associados.id"),
        unique=True,
        nullable=False,
    )
    candidato_preferido_id: Mapped[int] = mapped_column(ForeignKey("candidatos.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    associado: Mapped["Associado"] = relationship(back_populates="preferencia")
    candidato_preferido: Mapped["Candidato"] = relationship()


class PipefyLog(Base):
    __tablename__ = "pipefy_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    associado_id: Mapped[int] = mapped_column(ForeignKey("associados.id"), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    tentativas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evento: Mapped[str] = mapped_column(String(100), nullable=False)
    detalhes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
