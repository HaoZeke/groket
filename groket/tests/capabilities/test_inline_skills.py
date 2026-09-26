"""Inline run skills staging."""

from __future__ import annotations

from pathlib import Path

from groket.capabilities import apply
from groket.runs.personas import Persona


def test_write_inline_skills_writes_skill_md(tmp_path: Path) -> None:
    written = apply.write_inline_skills(
        tmp_path / "skills",
        [("run-hint", "---\nname: run-hint\ndescription: d\n---\n\n# Hint\nDo the thing.\n")],
    )
    assert written == ["run-hint"]
    text = (tmp_path / "skills" / "run-hint" / "SKILL.md").read_text(encoding="utf-8")
    assert "Do the thing" in text


def test_prepare_persona_skills_dir_inline_only(tmp_path: Path) -> None:
    dest = apply.prepare_persona_skills_dir(
        tmp_path / "out",
        None,
        inline_skills=[("only-inline", "---\nname: only-inline\ndescription: x\n---\n\n# X\n")],
    )
    assert dest is not None
    assert (dest / "only-inline" / "SKILL.md").is_file()


def test_prepare_persona_skills_dir_named_plus_inline(tmp_path: Path) -> None:
    skill_root = tmp_path / "catalog" / "named"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("---\nname: named\n---\n", encoding="utf-8")
    p = Persona(persona_id="p", skills=["named"])
    import groket.capabilities.apply as apply_mod

    orig = apply_mod.resolve_skill_path

    def fake_resolve(name, *, work_dir=None):
        return skill_root if name == "named" else None

    apply_mod.resolve_skill_path = fake_resolve  # type: ignore[assignment]  # test override
    try:
        dest = apply.prepare_persona_skills_dir(
            tmp_path / "out2",
            p,
            inline_skills=[("extra", "---\nname: extra\n---\n\n# E\n")],
        )
    finally:
        apply_mod.resolve_skill_path = orig
    assert dest is not None
    assert (dest / "named" / "SKILL.md").is_file()
    assert (dest / "extra" / "SKILL.md").is_file()
