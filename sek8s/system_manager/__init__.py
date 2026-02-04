"""System manager: status and cache submodules."""

from sek8s.system_manager.cache.router import router as cache_router
from sek8s.system_manager.status.router import router as status_router

__all__ = ["cache_router", "status_router"]
