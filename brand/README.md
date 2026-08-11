# groket identity 1.2

Three-bar rocket for the groket product. Each bar is a turn; the cap is
that turn’s status. The nose sits on the complete (green) bar so the
rocket flies toward success. The short bar is running (yellow); the
other long bar is failed (red). The **small mark** is that rocket without
needles: a 7×3 character grid so a terminal and a browser tab match.
Open **[guidelines.html](guidelines.html)** for colour, type, sizes, and
which file to use.

The approved still is `source/approved.jpg`. The shippable mark is the
SVG drawn by `build.py` — not a tracing of the still.

## Quick use

| Job | File |
|-----|------|
| README / site header | `png/groket-lockup-horizontal.png` |
| Poster / merch | `png/groket-lockup-stacked.png` |
| Dock / HUD app | `png/groket-app-icon-1024.png` |
| Dark dock | `png/groket-app-icon-dark-1024.png` |
| HUD search bar (colour) | `png/groket-mark.png` |
| HUD search bar (dark) | `png/groket-mark-reverse.png` |
| Favicon (square tab) | `png/groket-favicon-32.png` |
| HUD system tray | `png/groket-favicon-32.png` |
| TUI header | wordmark + folders when wide + activity |
| TUI help | three equal slats with status tips |
| CLI (3 rows) | `small.txt` |
| One-line caps | `caps.txt` |
| One-colour print | `png/groket-mark-mono.png` |

Wordmark type is **Fira Sans ExtraBold 800**, tracking −0.04 em,
lowercase `groket`. Files and SIL license in `fonts/` (same family as
the HUD UI face).

Rebuild (fonttools, pillow from the `brand` uv group; `rsvg-convert` from
librsvg):

```bash
make brand
# or: uv run --group brand python brand/build.py
```
