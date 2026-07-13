"""Plain FastAPI app for the Sentinel public demo.

Runnable locally via:
    uvicorn backend.demo_app:app --reload --port 8000

Deliberately has no Modal decorators — `app` is a plain FastAPI instance so
it can be trivially wrapped with @app.function + @modal.asgi_app() later
(a separate deploy step).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.demo_endpoints import router
from backend.guardrails import MAX_BODY_BYTES, rate_limiter

app = FastAPI(title="Sentinel Demo API", version="1.0.0")

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN != "*" else ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def guardrails_middleware(request: Request, call_next):
    # Body size cap — only applies to the triage endpoint's POST body.
    if request.method == "POST":
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": "payload_too_large", "message": "Request body exceeds 1KB"},
            )

        # Rebuild request stream since we already consumed it.
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive)

    # Rate limiting per-IP.
    client_ip = request.client.host if request.client else "unknown"
    if request.url.path.startswith("/triage"):
        result = rate_limiter.check(client_ip)
        if not result.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": result.reason,
                    "retry_after_seconds": result.retry_after_seconds,
                },
                headers={"Retry-After": str(result.retry_after_seconds)},
            )

    return await call_next(request)


app.include_router(router)
