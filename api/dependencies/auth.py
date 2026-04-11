from __future__ import annotations

from fastapi import Header, HTTPException, Request, status


async def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    expected = getattr(request.app.state, "api_key", None)
    if not expected or not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий X-API-Key.",
        )
