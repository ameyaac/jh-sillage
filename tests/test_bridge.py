"""The cross-language contract: CSVs the browser downloads must import into Python.

This is the test that actually protects the architecture. Python owns the schema,
but `web/js/store.js` re-implements it for the standalone page, and the two only
stay compatible by agreement — nothing at runtime enforces it. Field names, column
order, the `;` note separator, timestamp format and number formatting all have to
match, and a drift in any one of them silently breaks the user's backup.

Skips cleanly if node is unavailable.

    python tests/test_bridge.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import RatingLog  # noqa: E402
from data.portable import TABLES, export_dir, identify, import_files  # noqa: E402
from data.schema import (  # noqa: E402
    COMBINATION_FIELDS,
    PERFUME_FIELDS,
    PREDICTION_FIELDS,
    PREFERENCE_FIELDS,
    RATING_FIELDS,
)

REPO = Path(__file__).resolve().parent.parent
WEB_STORE = REPO / "web" / "js" / "store.js"
NODE = shutil.which("node")


class SkipTest(Exception):
    pass


def _emit_browser_export(dest: Path) -> dict:
    """Run the browser store under node and have it write a real export."""
    if NODE is None:
        raise SkipTest("node not installed")
    result = subprocess.run(
        [NODE, str(REPO / "tests" / "test_web.js"), "--emit", str(dest)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


# ── the contract ──────────────────────────────────────────────────────────────


def test_js_field_lists_match_python_exactly():
    # Column order matters: both sides write headers in this order, and a reordered
    # header would still import (identify() sorts) but would be confusing to diff.
    source = WEB_STORE.read_text(encoding="utf-8")
    expected = {
        "PERFUME_FIELDS": PERFUME_FIELDS,
        "COMBINATION_FIELDS": COMBINATION_FIELDS,
        "RATING_FIELDS": RATING_FIELDS,
        "PREDICTION_FIELDS": PREDICTION_FIELDS,
        "PREFERENCE_FIELDS": PREFERENCE_FIELDS,
    }
    for name, fields in expected.items():
        start = source.index("var " + name)
        end = source.index("];", start)
        block = source[start:end]
        found = [
            token.strip().strip("'\"")
            for token in block[block.index("[") + 1 :].split(",")
            if token.strip()
        ]
        assert found == fields, f"{name} drifted: JS has {found}, Python has {fields}"


def test_browser_export_imports_into_python():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        downloads = tmp_path / "downloads"
        js_summary = _emit_browser_export(downloads)

        # Every file the browser produced must be recognised by the Python importer.
        for filename, _, _, _ in TABLES.values():
            path = downloads / filename
            assert path.exists(), f"the browser did not export {filename}"
            table = identify(path)
            assert table is not None, f"Python cannot identify {filename}"

        log = RatingLog(tmp_path / "userdata")
        report = import_files(
            [downloads / name for name, _, _, _ in TABLES.values()],
            log=log,
            apply=True,
        )
        assert report.rejected == 0, report.render()

        summary = log.summary()
        assert summary["perfumes"] == js_summary["perfumes"]
        assert summary["rated"] == js_summary["rated"]
        assert summary["rating_events"] == js_summary["rating_events"]
        assert summary["combinations"] == js_summary["combinations"]
        assert summary["rated_combinations"] == js_summary["rated_combinations"]
        assert summary["predictions_logged"] == js_summary["predictions_logged"]
        assert summary["declared_preferences"] == js_summary["declared_preferences"]
        assert summary["preference_events"] == js_summary["preference_events"]
        assert summary["mean_rating"] == js_summary["mean_rating"]
        assert summary["distinct_notes"] == js_summary["distinct_notes"]


def test_browser_export_survives_awkward_text_and_notes():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        downloads = tmp_path / "downloads"
        _emit_browser_export(downloads)

        log = RatingLog(tmp_path / "userdata")
        import_files(
            [downloads / name for name, _, _, _ in TABLES.values()], log=log, apply=True
        )

        ganymede = next(p for p in log.perfumes() if "Ganymede" in p.name)
        assert ganymede.brand == 'Marc-Antoine Barrois "B683"'
        assert ganymede.name == "Ganymede, the moon"
        assert ganymede.top_notes == ["Mandarin", "Violet Leaf"]
        assert ganymede.base_notes == ["Suede", "Ambroxan"]
        # Several scent families share one cell and come back as a list, in order.
        assert ganymede.perfume_types == ["Woody", "Spicy", "Fresh"]

        # Rating context and pair order have to cross the boundary intact.
        assert any(e.context == "hot afternoon" for e in log.rating_events())
        combo, a, b, rating = log.pair_training_set()[0]
        assert rating == 4.0
        assert combo.first_applied_id == b.perfume_id

        # The browser's prediction is a prediction, not a training target.
        assert [p.model_version for p in log.predictions()] == ["legacy-heuristic-js"]
        assert all(rating != 4.2 for _, rating in log.training_set())

        # The declared palette crosses the boundary intact, and stays a prior: the
        # signed weights must not have been read as ratings anywhere.
        palette = log.palette()
        assert palette["family"] == {"citrus": 1.0, "animalic": -1.0}
        assert palette["note"] == {"Ambroxan": 0.5}
        assert palette["medium"] == {"Spray Perfume (EDP)": 1.0}
        assert all(1.0 <= rating <= 5.0 for _, rating in log.training_set())


def test_python_export_imports_back_into_the_browser():
    # The other direction: restore a Python backup into the page.
    if NODE is None:
        raise SkipTest("node not installed")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        downloads = tmp_path / "downloads"
        _emit_browser_export(downloads)

        log = RatingLog(tmp_path / "userdata")
        import_files(
            [downloads / name for name, _, _, _ in TABLES.values()], log=log, apply=True
        )
        # Add something only Python knows about, then export for the browser.
        extra = log.log_perfume(
            type(log.perfumes()[0])(
                brand="Amouage", name="Interlude 53", top_notes=["oregano"]
            )
        )
        log.log_rating(extra.perfume_id, 5.0)
        backup = export_dir(log, tmp_path / "backup")

        script = f"""
        const fs = require('fs'), path = require('path'), vm = require('vm');
        const data = new Map();
        const win = {{ localStorage: {{
            getItem: k => data.has(k) ? data.get(k) : null,
            setItem: (k, v) => data.set(k, String(v)),
            removeItem: k => data.delete(k)
        }}, console }};
        win.window = win;
        const ctx = vm.createContext(win);
        for (const f of ['csv.js', 'store.js', 'predict.js']) {{
            const p = path.join({str(REPO / "web" / "js")!r}, f);
            vm.runInContext(fs.readFileSync(p, 'utf8'), ctx, {{ filename: p }});
        }}
        const files = fs.readdirSync({str(backup)!r})
            .filter(n => n.endsWith('.csv'))
            .map(n => ({{ name: n, text: fs.readFileSync(path.join({str(backup)!r}, n), 'utf8') }}));
        const report = win.Sillage.Store.prepareImport(files);
        win.Sillage.Store.applyImport(report);
        process.stdout.write(JSON.stringify({{
            rejected: report.rejected,
            summary: win.Sillage.Store.summary()
        }}));
        """
        result = subprocess.run(
            [NODE, "-e", script], capture_output=True, text=True, check=True
        )
        payload = json.loads(result.stdout)
        assert payload["rejected"] == 0, result.stdout
        assert payload["summary"]["perfumes"] == log.summary()["perfumes"]
        assert payload["summary"]["rated"] == log.summary()["rated"]
        assert payload["summary"]["mean_rating"] == log.summary()["mean_rating"]


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = skipped = 0
    for test in tests:
        try:
            test()
        except SkipTest as exc:
            skipped += 1
            print(f"skip {test.__name__}: {exc}")
        except subprocess.CalledProcessError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: node exited {exc.returncode}\n{exc.stderr}")
        except Exception as exc:  # noqa: BLE001 — standalone runner
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    passed = len(tests) - failed - skipped
    print(f"\n{passed}/{len(tests)} passed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
