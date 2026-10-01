#!/usr/bin/env python3
"""Spawn-safe core gate with monotonic timing and SQLite finalizer diagnostics."""

from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import sys
import time
import unittest


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "plugin/core/src"), str(root / "test/core")]
    unraisable = []
    original_hook = sys.unraisablehook
    def record_unraisable(event):
        unraisable.append(type(event.exc_value).__name__)
        original_hook(event)
    sys.unraisablehook = record_unraisable
    started, utc = time.monotonic(), datetime.now(timezone.utc)
    suite = unittest.defaultTestLoader.discover(str(root / "test/core"))
    result = unittest.TextTestRunner(
        verbosity=2 if "--verbose" in sys.argv else 1,
        failfast="--failfast" in sys.argv,
    ).run(suite)
    gc.collect()
    print(json.dumps({
        "tests": result.testsRun, "errors": len(result.errors), "failures": len(result.failures),
        "skips": len(result.skipped), "unraisable": unraisable,
        "monotonic_seconds": time.monotonic() - started,
        "utc_seconds": (datetime.now(timezone.utc) - utc).total_seconds(),
    }))
    return 0 if result.wasSuccessful() and not unraisable else 1


if __name__ == "__main__":
    raise SystemExit(main())
