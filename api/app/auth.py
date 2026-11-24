import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from .config import Settings, get_settings

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _check(provided: str | None, expected: str) -> None:
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")


def require_app_key(key: str | None = Depends(_header), s: Settings = Depends(get_settings)) -> None:
    _check(key, s.app_api_key)


def require_ingest_key(key: str | None = Depends(_header), s: Settings = Depends(get_settings)) -> None:
    _check(key, s.ingest_api_key)
