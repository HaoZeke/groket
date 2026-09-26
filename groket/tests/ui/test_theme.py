"""Brand Textual themes."""

from __future__ import annotations

from groket.ui.theme import GROKET, GROKET_LIGHT, register_brand_themes


def test_groket_theme_uses_brand_hex() -> None:
    assert GROKET.name == "groket"
    assert GROKET.dark is True
    assert GROKET.background == "#282828"
    assert GROKET.foreground == "#FBF1C7"
    assert GROKET.success == "#98971A"
    assert GROKET.error == "#CC241D"
    assert GROKET.warning == "#D79921"
    assert GROKET.primary != "#0178D4"


def test_groket_light_is_cream_paper() -> None:
    assert GROKET_LIGHT.dark is False
    assert GROKET_LIGHT.background == "#FBF1C7"
    assert GROKET_LIGHT.foreground == "#282828"


def test_register_brand_themes() -> None:
    seen: list[str] = []

    class _App:
        def register_theme(self, theme: object) -> None:
            seen.append(getattr(theme, "name", ""))

    register_brand_themes(_App())
    assert seen == ["groket", "groket-light"]
