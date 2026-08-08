"""HUD control-plane launch behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from groket.hud import app as hud_app


def test_run_hud_show_starts_visible_and_notifies_existing_hud(tmp_path: Path) -> None:
    socket_path = tmp_path / "control.sock"
    show = AsyncMock()

    with (
        patch.object(hud_app, "launch_tauri_hud", return_value=0) as launch,
        patch.object(hud_app, "_show", show),
    ):
        code = hud_app.run_hud(
            socket_path=socket_path,
            auto_serve=False,
            show=True,
        )

    assert code == 0
    assert launch.call_args.kwargs["extra_env"] == {"GROKET_HUD_SHOW_ON_START": "1"}
    show.assert_awaited_once_with(socket_path)
