from __future__ import annotations

from groket.engine.detectors import clear_detectors, detector, get_all_detectors, get_detector
from groket.engine.models import Match


def test_register_and_get():
    clear_detectors()

    @detector("t_reg")
    def _d(tool_calls, messages, params):
        return [Match()]

    assert "t_reg" in get_all_detectors()
    assert get_detector("t_reg") is not None
    clear_detectors()
