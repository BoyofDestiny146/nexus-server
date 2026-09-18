"""Response envelope: every payload is wrapped in {code, msg, data} to match
the contract the existing dashboard's axios interceptor expects (success when
code == 0)."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware


class APIException(Exception):
    """Raise from a handler to return a non-zero envelope code with HTTP 200."""

    def __init__(self, code: int, msg: str, data: Any = None):
        self.code = code
        self.msg = msg
        self.data = data
        super().__init__(msg)


def envelope(data: Any = None, *, code: int = 0, msg: str = "success") -> dict[str, Any]:
    return {"code": code, "msg": msg, "data": data}


class EnvelopeMiddleware(BaseHTTPMiddleware):
    """Wrap every JSON response body in {code, msg, data}, unless the body is
    already in that shape (handler may have set it manually) or the response
    isn't JSON (e.g., WebSocket upgrade, file streams, /metrics)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Skip non-JSON responses
        ct = response.headers.get("content-type", "")
        if "application/json" not in ct:
            return response

        # Never wrap the OpenAPI schema — Swagger UI / Redoc fetch the raw spec
        # from openapi.json; wrapping it in {code,msg,data} makes the docs page
        # render with NO endpoints listed.
        if request.url.path.endswith("/openapi.json"):
            return response

        # Only wrap routes under /api (lets us serve /healthz raw if needed)
        if not request.url.path.startswith("/api"):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        import json
        try:
            payload = json.loads(body) if body else None
        except (ValueError, json.JSONDecodeError):
            return response

        # Already wrapped? leave it
        if isinstance(payload, dict) and "code" in payload and "msg" in payload and "data" in payload:
            wrapped = payload
        else:
            wrapped = envelope(payload)

        new_body = json.dumps(wrapped, default=str).encode("utf-8")
        return JSONResponse(
            content=wrapped,
            status_code=200,  # Envelope errors stay HTTP 200; the dashboard reads `code`
            headers={
                k: v for k, v in response.headers.items()
                if k.lower() not in ("content-length", "content-type")
            },
        )


# Exception handlers — register on the app

async def api_exception_handler(_request: Request, exc: APIException):
    return JSONResponse(envelope(exc.data, code=exc.code, msg=exc.msg))


async def http_exception_handler(_request: Request, exc: HTTPException):
    return JSONResponse(envelope(None, code=exc.status_code, msg=exc.detail or "error"))


async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(envelope(exc.errors(), code=400, msg="validation error"))
