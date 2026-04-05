import hashlib
import os
import ssl
from abc import abstractmethod
from typing import FrozenSet, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.applications import AppType
from fastapi.responses import ORJSONResponse
from loguru import logger
from sek8s_common.config import ServerConfig
from starlette.requests import Request
from starlette.types import Lifespan


class WebServer:
    """Async web server for admission webhook using FastAPI."""

    def __init__(
        self, config: ServerConfig, lifespan: Optional[Lifespan[AppType]] = None
    ):
        self.config = config
        self.app = FastAPI(
            debug=config.debug, default_response_class=ORJSONResponse, lifespan=lifespan  # type: ignore[arg-type]
        )
        self._add_body_sha256_middleware()
        self._setup_routes()

    body_hash_skip_paths: FrozenSet[str] = frozenset()
    """Paths to skip body SHA256 hashing for (set by subclasses)."""

    def _add_body_sha256_middleware(self) -> None:
        """Set request.state.body_sha256 for POST/PUT/PATCH so authorize() can verify payload signatures."""
        skip = self.body_hash_skip_paths

        @self.app.middleware("http")
        async def add_body_sha256(request: Request, call_next):
            if (
                request.method in ("POST", "PUT", "PATCH")
                and request.url.path not in skip
            ):
                body = await request.body()
                request.state.body_sha256 = (
                    hashlib.sha256(body).hexdigest() if body else None
                )
            else:
                request.state.body_sha256 = None
            return await call_next(request)

    @abstractmethod
    def _setup_routes(self):
        """
        Setup web routes.
        Example:
        self.app.add_api_route('/route', self.handle_route, methods=["GET"])
        """
        raise NotImplementedError()

    def run(self):
        """Run the webhook server."""
        uvicorn_kwargs = {}

        if self.config.uds_path:
            logger.info(f"Starting server on Unix socket {self.config.uds_path}")
            uvicorn_kwargs["uds"] = self.config.uds_path
        else:
            logger.info(
                f"Starting server on {self.config.bind_address}:{self.config.port}"
            )
            uvicorn_kwargs["host"] = self.config.bind_address
            uvicorn_kwargs["port"] = self.config.port

            if self.config.tls_cert_path and self.config.tls_key_path:
                uvicorn_kwargs["ssl_certfile"] = self.config.tls_cert_path
                uvicorn_kwargs["ssl_keyfile"] = self.config.tls_key_path
                logger.info("TLS enabled")

                if self.config.mtls_required:
                    if not self.config.client_ca_path or not os.path.exists(
                        self.config.client_ca_path
                    ):
                        raise ValueError(
                            f"mTLS requires valid client CA certificate: {self.config.client_ca_path}"
                        )

                    uvicorn_kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
                    uvicorn_kwargs["ssl_ca_certs"] = self.config.client_ca_path
                    logger.info(f"mTLS enabled with CA: {self.config.client_ca_path}")
                else:
                    logger.info("mTLS disabled - no client certificate verification")
            elif self.config.require_tls:
                raise ValueError(
                    "TLS certificate and key are required for TCP connections"
                )
            else:
                logger.warning(
                    "Starting server without TLS; intended for controlled environments only"
                )

        uvicorn.run(
            self.app,
            log_level="debug" if self.config.debug else "info",
            **uvicorn_kwargs,
        )
