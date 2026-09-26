"""Synthetic fat session trees for high-load catalog / inspect checks.

Default product tree lives under ``/tmp/groket-highload`` (objective). In-repo
tests can call :func:`write_fat_session` on a tiny tmp_path set.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

HIGHLOAD_ROOT = Path("/tmp/groket-highload")
DEFAULT_COUNT = 1000
TURNS_MIN = 50
TURNS_MAX = 100


def work_dir(root: Path = HIGHLOAD_ROOT) -> Path:
    return Path(root) / "work"


def traces_dir(root: Path = HIGHLOAD_ROOT) -> Path:
    return work_dir(root) / "runs" / "traces"


def write_fat_session(
    traces: Path,
    name: str,
    *,
    turns: int = 75,
    title: str | None = None,
) -> Path:
    """Write one session with *turns* start/end markers and user/agent updates."""
    n = max(1, int(turns))
    sd = Path(traces) / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": title or f"Fat session {name}",
                "num_messages": n * 2,
            }
        ),
        encoding="utf-8",
    )
    ev_lines: list[str] = []
    up_lines: list[str] = []
    for i in range(n):
        ev_lines.append(json.dumps({"ts": i * 2, "type": "turn_started", "turn_number": i}))
        ev_lines.append(json.dumps({"ts": i * 2 + 1, "type": "turn_ended", "outcome": "completed"}))
        up_lines.append(
            json.dumps(
                {
                    "timestamp": i * 2,
                    "params": {
                        "update": {
                            "sessionUpdate": "user_message_chunk",
                            "content": {"type": "text", "text": f"ask {i} {name}"},
                            "_meta": {"promptIndex": i + 1},
                        }
                    },
                }
            )
        )
        up_lines.append(
            json.dumps(
                {
                    "timestamp": i * 2 + 1,
                    "params": {
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": f"ok {i}"},
                        }
                    },
                }
            )
        )
    (sd / "events.jsonl").write_text("\n".join(ev_lines) + "\n", encoding="utf-8")
    (sd / "updates.jsonl").write_text("\n".join(up_lines) + "\n", encoding="utf-8")
    return sd


def generate_highload_tree(
    root: Path = HIGHLOAD_ROOT,
    *,
    count: int = DEFAULT_COUNT,
    turns_min: int = TURNS_MIN,
    turns_max: int = TURNS_MAX,
    seed: int = 1,
) -> Path:
    """Create *count* fat sessions under *root*/work/runs/traces. Idempotent."""
    traces = traces_dir(root)
    traces.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    existing = {p.name for p in traces.iterdir() if p.is_dir()}
    for i in range(count):
        name = f"fat-{i:04d}"
        if name in existing:
            continue
        write_fat_session(
            traces,
            name,
            turns=rng.randint(turns_min, turns_max),
            title=f"Fat load {i}",
        )
    return traces


def inspectable_session_id(count: int = DEFAULT_COUNT) -> str:
    """A stable session id in the generated tree (first row)."""
    return "fat-0000"
