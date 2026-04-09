"""
FastAPI application for the Chamber Sentinel CIED Telemetry Simulator.

Provides REST and WebSocket endpoints for simulation control, patient
management, analytics, scenario execution, and real-time telemetry streaming.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import analytics, chambers, patients, scenarios, simulation
from src.api.websockets.stream import router as ws_router

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("chamber_sentinel")


def _configure_logging() -> None:
    """Set up structured logging with ISO-8601 timestamps."""
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Application-level shared state
# ---------------------------------------------------------------------------

class AppState:
    """Mutable singleton bag attached to ``app.state`` at startup."""

    started_at: float = 0.0
    ready: bool = False


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    """Async lifespan context manager for startup and shutdown hooks."""
    _configure_logging()
    logger.info("Chamber Sentinel CIED Simulator starting up")

    app.state.app_state = AppState()
    app.state.app_state.started_at = time.time()
    app.state.app_state.ready = True

    logger.info("Startup complete -- accepting requests")
    yield

    logger.info("Chamber Sentinel CIED Simulator shutting down")
    app.state.app_state.ready = False
    logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Chamber Sentinel CIED Simulator",
    description=(
        "Burn-by-default vs persist-by-default architecture comparison "
        "for cardiac implantable electronic devices (CIEDs). "
        "Provides simulation control, patient management, telemetry streaming, "
        "analytics, and scenario execution."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS -- allow the Dash/React dashboard and local dev
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8050",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8050",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request timing middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_timing_header(request: Request, call_next):  # noqa: ANN001, ANN201
    """Inject ``X-Process-Time`` response header for observability."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    response.headers["X-Process-Time"] = f"{elapsed_ms:.2f}ms"
    return response


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler that returns a structured JSON error body."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "detail": str(exc),
            "path": str(request.url.path),
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Return 422 for domain validation failures."""
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "detail": str(exc),
            "path": str(request.url.path),
        },
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health_check() -> dict[str, Any]:
    """Return service health status and uptime information."""
    state: AppState = app.state.app_state
    uptime_s = time.time() - state.started_at if state.started_at else 0.0
    return {
        "status": "healthy" if state.ready else "starting",
        "version": app.version,
        "uptime_seconds": round(uptime_s, 2),
        "service": "chamber-sentinel-cied-sim",
    }


# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------

app.include_router(simulation.router)
app.include_router(patients.router)
app.include_router(analytics.router)
app.include_router(scenarios.router)
app.include_router(chambers.router)
app.include_router(ws_router)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def run() -> None:
    """Launch the API server via uvicorn.  Used by the ``cied-sim`` console script."""
    _configure_logging()
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    run()
