"""Shared toolbar / filter chrome — exclusive filters use Select.

Standard pattern (sessions list, Timeline **Filter**, Report **Filter**):

- Horizontal bar with classes ``filter-bar`` + bold ``filter-view-label``
- One or more **Select** widgets for exclusive modes (All / …) — shared styling
  in ``app.tcss`` (``#timeline-view-select``, ``#report-view-select``,
  ``#session-model-select``, ``.field-select``)
- Optional trailing ``Input`` (timeline search only)

Form field booleans still use full-width ``Checkbox`` (persona editor, analysis
settings). Do **not** use Button chips or ad-hoc multi-checkbox rows for
section filters.
"""

from __future__ import annotations

FILTER_BAR_CLASS = "filter-bar"
FILTER_LABEL_CLASS = "filter-view-label"
