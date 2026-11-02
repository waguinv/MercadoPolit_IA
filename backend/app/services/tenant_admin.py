"""Criação de lojas e usuários sempre dentro da organização do ator."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.organization import Store
from app.models.user import User
from app.schemas.auth import StoreCreate, UserCreate
from app.services import audit


class ConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


def get_store(db: Session, organization_id: UUID, store_id: UUID) -> Store:
    store = db.scalar(
        select(Store).where(Store.id == store_id, Store.organization_id == organization_id)
    )
    if store is None:
        raise NotFoundError("Loja não encontrada.")
    return store


def create_store(db: Session, actor: User, data: StoreCreate) -> Store:
    store = Store(organization_id=actor.organization_id, code=data.code.strip(), name=data.name.strip())
    db.add(store)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Já existe loja com esse código.") from exc
    audit.record(db, action="store.created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="store", entity_id=store.id,
                 details={"code": store.code})
    db.commit()
    return store


def create_user(db: Session, actor: User, data: UserCreate) -> User:
    if data.store_id is not None:
        get_store(db, actor.organization_id, data.store_id)  # 404 se for de outro tenant
    user = User(
        organization_id=actor.organization_id,
        store_id=data.store_id,
        email=data.email.lower(),
        full_name=data.full_name.strip(),
        password_hash=hash_password(data.password),
        role=data.role,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("E-mail já cadastrado.") from exc
    audit.record(db, action="user.created", organization_id=actor.organization_id,
                 actor_user_id=actor.id, entity_type="user", entity_id=user.id,
                 details={"role": user.role.value, "store_id": str(data.store_id) if data.store_id else None})
    db.commit()
    return user
