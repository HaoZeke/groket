"""Unit tests for Pilot wait helpers (no full app required for failure path)."""

from __future__ import annotations

import pytest

from .pilot_helpers import wait_until


class _FakePilot:
    async def pause(self, delay: float | None = None) -> None:
        return None


@pytest.mark.asyncio
async def test_wait_until_succeeds_when_pred_true() -> None:
    flag = {"n": 0}

    def pred() -> bool:
        flag["n"] += 1
        return flag["n"] >= 2

    await wait_until(_FakePilot(), pred, attempts=5, description="flag")  # type: ignore[arg-type]  # stub for test
    assert flag["n"] >= 2


@pytest.mark.asyncio
async def test_wait_until_fails_clearly() -> None:
    with pytest.raises(AssertionError, match="never"):
        await wait_until(_FakePilot(), lambda: False, attempts=3, description="never")  # type: ignore[arg-type]  # stub for test
