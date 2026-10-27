"""Clothing taste: a fashion image-text model on the CPU (plan 2026-10-27-clothing-taste-mvp).

Needs the ``style`` extra (``onnxruntime``, ``tokenizers``, ``pillow``, ``numpy``): ``encoder`` and
``cache`` import it at the top, so check ``available()`` before importing them; ``weights``
needs only the core.
"""

import importlib.util

EXTRA = "style"
_MODULES = ("onnxruntime", "tokenizers", "PIL", "numpy")


def available() -> bool:
    """The ``style`` extra is installed."""
    return all(importlib.util.find_spec(name) for name in _MODULES)
