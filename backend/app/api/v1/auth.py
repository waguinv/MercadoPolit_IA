from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.security import create_access_token
from app.schemas.auth import LoginRequest, PasswordChange, TokenResponse, UserOut
from app.services import auth as svc
from app.services import refresh_tokens

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "mp_refresh"
_COOKIE_PATH = "/api/v1/auth"
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def _set_refresh_cookie(response: Response, raw: str) -> None:
    # HttpOnly: script da página não lê o token. Strict: outro site não consegue usá-lo.
    s = get_settings()
    response.set_cookie(
        REFRESH_COOKIE, raw, max_age=s.refresh_token_expire_days * 86400, path=_COOKIE_PATH, httponly=True,
        samesite="strict", secure=s.app_env not in ("development", "test"))


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=_COOKIE_PATH)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, response: Response, db: DbSession) -> TokenResponse:
    user = svc.authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciais inválidas.")
    raw = refresh_tokens.issue(db, user)
    db.commit()
    _set_refresh_cookie(response, raw)
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/refresh", response_model=TokenResponse)
def refresh(db: DbSession, token: RefreshCookie = None):
    try:
        user, raw = refresh_tokens.rotate(db, token)
    except refresh_tokens.InvalidRefreshToken:
        expired = JSONResponse({"detail": "Sessão expirada."}, status_code=status.HTTP_401_UNAUTHORIZED)
        _clear_refresh_cookie(expired)
        return expired
    ok = JSONResponse(TokenResponse(access_token=create_access_token(user.id)).model_dump())
    _set_refresh_cookie(ok, raw)
    return ok


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, db: DbSession, token: RefreshCookie = None) -> None:
    refresh_tokens.revoke_login(db, token)
    _clear_refresh_cookie(response)


@router.post("/change-password", response_model=TokenResponse)
def change_password(body: PasswordChange, response: Response, db: DbSession,
                    user: CurrentUser) -> TokenResponse:
    raw = svc.change_password(db, user, body.current_password, body.new_password)
    if raw is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A senha atual não confere.")
    _set_refresh_cookie(response, raw)
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
