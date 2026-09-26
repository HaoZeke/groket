"""Optional Limited API extension build (groket._listwalk).

Set ``GROKET_BUILD_EXT=1`` so install compiles ``native/listwalk.c``.
Without it, setuptools ships the pure-Python package only.
"""

from __future__ import annotations

import os

from setuptools import Extension, setup

_ext: list[Extension] = []
if os.environ.get("GROKET_BUILD_EXT") == "1":
    _ext = [
        Extension(
            "groket._listwalk",
            sources=["native/listwalk.c"],
            py_limited_api=True,
            define_macros=[("Py_LIMITED_API", "0x030D0000")],
            optional=True,
        )
    ]

setup(ext_modules=_ext)
