"""Resolve non-Python assets (repo ``assets/`` or ``groket/_embedded_assets``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def assets_root() -> Path:
    """Return assets root containing ``docker/`` and ``config/``.

    :raises FileNotFoundError: If neither checkout nor embedded layout exists.
    """
    pkg = Path(__file__).resolve().parent
    if (repo := pkg.parent / "assets").is_dir():
        return repo
    if (emb := pkg / "_embedded_assets").is_dir() and any(emb.iterdir()):
        return emb
    raise FileNotFoundError(
        "groket assets not found (expected repo assets/ or groket/_embedded_assets/)"
    )


def asset_path(*parts: str) -> Path:
    """Path under :func:`assets_root`."""
    return assets_root().joinpath(*parts)


def read_asset_text(*parts: str) -> str:
    """Read UTF-8 asset file contents."""
    return asset_path(*parts).read_text(encoding="utf-8")
