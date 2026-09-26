"""groket — Trace evaluation for hunting bad model behaviors.

* Root: ``models``, ``parser``, ``paths``, ``constants``, ``utils``, ``flags``, ``cli``
* ``runs/`` — personas, run configs, batch, background manager, shares, log services
* ``session/`` — usage stats and workspace diffs
* ``analysis/``, ``engine/``, ``capabilities/``, ``docker/``
* ``extensions/`` — ``groket gen`` scaffolding
* ``ui/`` — Textual presentation

Data flow: models/parser → runs|session|analysis|engine → ui.
"""

from __future__ import annotations

__version__ = "0.2.0"
