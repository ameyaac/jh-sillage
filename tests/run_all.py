"""Run every suite: the Python data layer, the browser modules, and the bridge.

    python tests/run_all.py

The browser suite needs node; it is reported as skipped rather than failed when
node is missing, since the Python side does not depend on it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent

SUITES = [
    ("data layer", [sys.executable, str(TESTS / "test_data.py")], None),
    ("backup / restore", [sys.executable, str(TESTS / "test_portable.py")], None),
    ("browser modules", ["node", str(TESTS / "test_web.js")], "node"),
    ("python ↔ browser bridge", [sys.executable, str(TESTS / "test_bridge.py")], "node"),
]


def main() -> int:
    failures: list[str] = []
    skipped: list[str] = []

    for name, command, requires in SUITES:
        if requires and shutil.which(requires) is None:
            skipped.append(name)
            print(f"\n── {name} ── skipped ({requires} not installed)")
            continue

        print(f"\n── {name} ──")
        result = subprocess.run(command, cwd=REPO)
        if result.returncode != 0:
            failures.append(name)

    print("\n" + "═" * 60)
    if failures:
        print("FAILED: " + ", ".join(failures))
    else:
        print("All suites passed." + (f" Skipped: {', '.join(skipped)}" if skipped else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
