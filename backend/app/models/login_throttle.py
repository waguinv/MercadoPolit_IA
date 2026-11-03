from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoginThrottle(Base):
    """Contador de tentativas por chave, em janela fixa. A chave é um hash: e-mail e IP não ficam aqui."""

    __tablename__ = "login_throttles"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
