"""Status submodule: health, services, overview, disk, shutdown."""

from sek8s.system_manager.status.router import get_config, router

__all__ = ["get_config", "router"]
