"""Pure-logic tests can also be run without pytest.

``python -m tests.selfcheck`` executes every ``test_*`` function in
``tests/test_schedule.py`` with a tiny runner, so the schedule arithmetic can be
validated even on a machine without the pytest Home Assistant plugin.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import sys
import traceback
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _stub_integration_package() -> None:
    """Import ``schedule`` without pulling in Home Assistant."""
    package = types.ModuleType("custom_components")
    package.__path__ = [str(ROOT / "custom_components")]
    sys.modules.setdefault("custom_components", package)

    integration = types.ModuleType("custom_components.staggered_switch")
    integration.__path__ = [str(ROOT / "custom_components" / "staggered_switch")]
    sys.modules.setdefault("custom_components.staggered_switch", integration)


def main() -> int:
    """Run the schedule tests and return a process exit code."""
    _stub_integration_package()
    sys.path.insert(0, str(ROOT))

    tests_package = types.ModuleType("ha_tests")
    tests_package.__path__ = [str(ROOT / "tests")]
    sys.modules["ha_tests"] = tests_package

    module = importlib.import_module("ha_tests.test_schedule")

    failures = 0
    for name, function in sorted(inspect.getmembers(module, inspect.isfunction)):
        if not name.startswith("test_"):
            continue
        try:
            function()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001 - a failing test is reported, not raised
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()

    print()
    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
