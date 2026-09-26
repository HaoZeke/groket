"""Docker orchestration for parallel trace evaluation sessions.

Static build assets (entrypoint, share helper, Dockerfile templates) live
under the repo ``assets/docker/`` tree and are read by
:mod:`groket.docker.resources` via :mod:`groket.assets_loader`. Profile
package lists and dynamic RUN fragments remain in
:mod:`groket.docker.base_profiles`.
"""

from __future__ import annotations

from .base_profiles import (
    DEFAULT_DOCKER_IMAGE,
    FULLY_LOADED,
    MINIMAL,
    list_profiles,
    profile_help_text,
    resolve_docker_base,
)
from .orchestrator import ContainerConfig, ContainerStatus, DockerOrchestrator

__all__ = [
    "ContainerConfig",
    "ContainerStatus",
    "DEFAULT_DOCKER_IMAGE",
    "DockerOrchestrator",
    "FULLY_LOADED",
    "MINIMAL",
    "list_profiles",
    "profile_help_text",
    "resolve_docker_base",
]
