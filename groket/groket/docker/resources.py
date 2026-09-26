"""Load Docker build assets from :mod:`groket.assets_loader` (repo ``assets/docker``)."""

from __future__ import annotations

from ..assets_loader import read_asset_text


def read_text(name: str) -> str:
    """Return UTF-8 text of ``assets/docker/<name>``."""
    return read_asset_text("docker", name)


def entrypoint_sh() -> str:
    """Eval container ``entrypoint.sh``."""
    return read_text("entrypoint.sh")


def share_once_py() -> str:
    """In-container share helper script source (written into the image build context)."""
    return read_text("groket-share-once.py")


def find_primary_session_py() -> str:
    """In-container multi-turn primary session picker (never returns subagents)."""
    return read_text("groket_find_primary_session.py")


def empty_setup_sh() -> str:
    """Placeholder ``setup.sh`` when the task has no initial commands."""
    return read_text("setup-empty.sh")


def dockerfile_shared_base(*, base_image: str, profile_comment: str, tool_block: str) -> str:
    """Heavy shared base image Dockerfile body."""
    tpl = read_text("Dockerfile.shared_base")
    return tpl.format(
        base_image=base_image,
        profile_comment=profile_comment,
        tool_block=tool_block.rstrip("\n"),
    )


def dockerfile_run(*, shared_base_tag: str) -> str:
    """Thin per-run image: ``FROM`` shared base + setup + entrypoint."""
    return read_text("Dockerfile.run").format(shared_base_tag=shared_base_tag)


def dockerfile_monolithic_suffix() -> str:
    """Layers appended after the shared-base body for a single-stage Dockerfile."""
    return read_text("Dockerfile.monolithic_suffix")
