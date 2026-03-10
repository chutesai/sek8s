"""Common image reference parsing utilities.

Single source of truth for extracting registry and parsing OCI image references.
Used by validators (registry, cosign) and system-manager images module.
"""

from __future__ import annotations


def normalize_registry_hostname(image_ref: str) -> str:
    """Lowercase registry hostname so ctr matches registries.yaml (case-sensitive lookup)."""
    if "/" not in image_ref:
        return image_ref
    registry, rest = image_ref.split("/", 1)
    return f"{registry.lower()}/{rest}"


def extract_registry(image_ref: str) -> str:
    """Extract registry host:port from image reference.

    Examples:
        localhost:30500/org/image:tag -> localhost:30500
        docker.io/library/nginx:latest -> docker.io
        nginx:latest -> docker.io
    """
    if "/" not in image_ref:
        return "docker.io"

    parts = image_ref.split("/")
    first = parts[0]

    if "." in first or ":" in first or first == "localhost":
        return first
    return "docker.io"


def parse_image_reference(image: str) -> tuple[str, str, str, str]:
    """Parse image reference into (registry, organization, repository, tag_or_digest).

    Examples:
        nginx:latest -> (docker.io, library, nginx, latest)
        parachutes/chutes-agent:k3s -> (docker.io, parachutes, chutes-agent, k3s)
        gcr.io/distroless/base:latest -> (gcr.io, distroless, base, latest)
        localhost:30500/org/image@sha256:abc -> (localhost:30500, org, image, @sha256:abc)
    """
    original = image

    if "@" in image:
        image, digest = image.split("@", 1)
        tag_or_digest = f"@{digest}"
    elif ":" in image.split("/")[-1]:
        image, tag = image.rsplit(":", 1)
        tag_or_digest = tag
    else:
        tag_or_digest = "latest"

    if "/" not in image:
        return ("docker.io", "library", image, tag_or_digest)

    parts = image.split("/")
    first = parts[0]

    if "." in first or ":" in first or first == "localhost":
        registry = first
        remaining = parts[1:]
        if len(remaining) == 0:
            raise ValueError(f"Invalid image reference: {original}")
        if len(remaining) == 1:
            return (registry, "library", remaining[0], tag_or_digest)
        return (registry, remaining[0], "/".join(remaining[1:]), tag_or_digest)

    registry = "docker.io"
    if len(parts) == 1:
        return (registry, "library", parts[0], tag_or_digest)
    return (registry, parts[0], "/".join(parts[1:]), tag_or_digest)
