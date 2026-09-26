from __future__ import annotations

from groket.ui.widgets.controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS


def test_filter_classes():
    assert FILTER_BAR_CLASS == "filter-bar"
    assert FILTER_LABEL_CLASS == "filter-view-label"
