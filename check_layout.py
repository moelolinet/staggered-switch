"""Local replacement for the removed CI workflow.

Run:  python check_layout.py
"""

from __future__ import annotations

import json
import pathlib
import sys


def main() -> int:
    """Validate the HACS layout and manifest without any network access."""
    root = pathlib.Path(__file__).resolve().parent
    integration_root = root / "custom_components"
    problems: list[str] = []

    integrations = [
        p for p in integration_root.iterdir() if p.is_dir() and not p.name.startswith("__")
    ]
    if len(integrations) != 1:
        problems.append(f"expected exactly one integration, found {[p.name for p in integrations]}")

    if not integrations:
        print("\n".join(problems))
        return 1

    pkg = integrations[0]
    manifest_path = pkg / "manifest.json"
    if not manifest_path.exists():
        problems.append("manifest.json is missing")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("domain", "documentation", "issue_tracker", "codeowners", "name", "version"):
            if key not in manifest:
                problems.append(f"manifest.json is missing {key}")
        if "YOUR_GITHUB_USERNAME" in json.dumps(manifest):
            problems.append("manifest.json still contains the placeholder repository URL")
        if manifest.get("domain") != pkg.name:
            problems.append("manifest domain does not match the folder name")

    for required in ("__init__.py", "config_flow.py", "strings.json", "translations"):
        if not (pkg / required).exists():
            problems.append(f"{required} is missing")

    if not (root / "brand" / "icon.png").exists():
        problems.append("brand/icon.png is required by HACS")
    if (integration_root / "__init__.py").exists():
        problems.append("custom_components/__init__.py must not exist")

    strings_path = pkg / "strings.json"
    if strings_path.exists():
        strings = json.loads(strings_path.read_text(encoding="utf-8"))
        for section in ("config", "options"):
            if section not in strings:
                problems.append(f"strings.json is missing the {section} section")

    if problems:
        print("layout check failed:")
        for item in problems:
            print(f"  - {item}")
        return 1

    print("layout ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
