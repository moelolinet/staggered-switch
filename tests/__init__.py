"""Add ``custom_components`` to ``sys.path`` for the unit tests.

Home Assistant loads integrations from ``custom_components`` as a namespace
package, so the folder itself must not contain an ``__init__.py``.  The tests
therefore add the repository root instead of relying on a package file.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
