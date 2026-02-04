"""System manager service: composes system-status and cache routers on one app."""

from fastapi import FastAPI

from sek8s.config import SystemManagerConfig
from sek8s.server import WebServer
from sek8s.system_manager.cache.router import router as cache_router
from sek8s.system_manager.status.router import router as status_router


class SystemManagerServer(WebServer):
    """Web server for the system manager (status + cache routes)."""

    def _setup_routes(self) -> None:
        self.app.include_router(status_router, prefix="/status", tags=["status"])
        self.app.include_router(cache_router, prefix="/cache", tags=["cache"])


def create_app() -> FastAPI:
    """Create the manager FastAPI app (for testing or programmatic use)."""
    config = SystemManagerConfig()
    server = SystemManagerServer(config)
    return server.app


def run() -> None:
    """Run the system-manager server (status + cache routes)."""
    config = SystemManagerConfig()
    server = SystemManagerServer(config)
    server.run()


if __name__ == "__main__":
    run()
