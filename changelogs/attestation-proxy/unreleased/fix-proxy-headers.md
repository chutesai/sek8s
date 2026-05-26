### Fixed
- Strip upstream `Server` response header in `proxy_request` so Uvicorn's own header is the only one sent to clients. Forwarding the backend's `Server` header alongside Uvicorn's own produced a duplicate that aiohttp 3.13.4+ rejects with `Duplicate 'Server' header found`.
