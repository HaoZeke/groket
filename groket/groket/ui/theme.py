"""Textual themes that use the groket brand palette.

Ink / cream for chrome. Caps for success / error / warning. No cyan.
"""

from __future__ import annotations

from textual.theme import Theme

# Same hex as brand/build.py.
INK = "#282828"
CREAM = "#FBF1C7"
COMPLETE = "#98971A"
FAILED = "#CC241D"
RUNNING = "#D79921"
CANCELLED = "#928374"
# Lifted ink for panels (gruvbox bg1 / bg2).
INK_LIFT = "#3c3836"
INK_BOOST = "#504945"
CREAM_DIM = "#ebdbb2"
CREAM_LIFT = "#f2e5bc"

GROKET = Theme(
    name="groket",
    primary=COMPLETE,
    secondary=CANCELLED,
    accent=RUNNING,
    warning=RUNNING,
    error=FAILED,
    success=COMPLETE,
    foreground=CREAM,
    background=INK,
    surface=INK_LIFT,
    panel=INK_LIFT,
    boost=INK_BOOST,
    dark=True,
    variables={
        "block-cursor-foreground": INK,
        "button-color-foreground": INK,
        "footer-background": INK_LIFT,
        "footer-key-foreground": CREAM,
        "footer-description-foreground": CANCELLED,
    },
)

GROKET_LIGHT = Theme(
    name="groket-light",
    primary=COMPLETE,
    secondary=CANCELLED,
    accent=RUNNING,
    warning=RUNNING,
    error=FAILED,
    success=COMPLETE,
    foreground=INK,
    background=CREAM,
    surface=CREAM_LIFT,
    panel=CREAM_DIM,
    boost="#d5c4a1",
    dark=False,
    variables={
        "block-cursor-foreground": CREAM,
        "button-color-foreground": CREAM,
        "footer-background": CREAM_DIM,
        "footer-key-foreground": INK,
        "footer-description-foreground": CANCELLED,
    },
)


def register_brand_themes(app: object) -> None:
    """Register groket themes on a Textual app.

    :param app: ``App`` with ``register_theme``.
    """
    register = getattr(app, "register_theme", None)
    if not callable(register):
        return
    register(GROKET)
    register(GROKET_LIGHT)
