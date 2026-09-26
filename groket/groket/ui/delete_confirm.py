"""Double-press delete confirmation (sessions, configs, personas).

First ``x`` arms a pending key set; second ``x`` with the same targets commits.
Changing the selection / cursor clears the pending set on the next arm.
"""

from __future__ import annotations


def second_press_armed(
    pending: set[str] | list[str] | None,
    now: set[str] | list[str],
) -> tuple[bool, list[str]]:
    """Return ``(commit, pending_ids)``.

    If *pending* does not match *now*, returns ``(False, list(now))`` — caller
    should store that list and show the “press x again” toast. If it matches,
    returns ``(True, [])`` — caller should clear pending and delete.
    """
    now_set = {str(x) for x in now}
    pending_set = {str(x) for x in (pending or [])}
    if pending_set != now_set:
        return False, sorted(now_set)
    return True, []
