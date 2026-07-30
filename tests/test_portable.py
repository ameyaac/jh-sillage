"""Tests for CSV export and import — the backup and restore path.

Runs standalone (`python tests/test_portable.py`) or under pytest.
"""

from __future__ import annotations

import csv
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import Perfume, Prediction, RatingLog  # noqa: E402
from data.portable import (  # noqa: E402
    TABLES,
    export_dir,
    identify,
    import_files,
)


def _perfume(**kw) -> Perfume:
    base = dict(
        brand="Dior",
        name="Sauvage",
        perfume_type="Woody",
        perfume_medium="Spray Perfume (EDT)",
        top_notes=["bergamot", "pink pepper"],
        middle_notes=["lavender"],
        base_notes=["ambroxan"],
    )
    base.update(kw)
    return Perfume(**base)


def _populated(data_dir) -> RatingLog:
    log = RatingLog(data_dir)
    a = log.log_perfume(_perfume(name="Sauvage"))
    b = log.log_perfume(_perfume(brand="Chanel", name="Bleu"))
    combo = log.log_combination(a.perfume_id, b.perfume_id, applied_first="b")
    log.log_rating(a.perfume_id, 4.5, context="hot afternoon")
    log.log_rating(b.perfume_id, 3.0)
    log.log_rating(rating=4.0, combination_id=combo.combination_id)
    log.log_prediction(
        Prediction(subject_id=a.perfume_id, value=4.2, model_version="ridge-v1")
    )
    log.log_preference("family", "citrus", 1.0)
    log.log_preference("note", "Cumin", -1.0)
    return log


# ── export ────────────────────────────────────────────────────────────────────


def test_export_writes_all_tables_even_when_empty():
    # A backup missing ratings.csv is indistinguishable from having no ratings.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = RatingLog(tmp_path / "log")
        log.log_perfume(_perfume())

        dest = export_dir(log, tmp_path / "backup")
        for filename, fields, _, _ in TABLES.values():
            path = dest / filename
            assert path.exists(), f"{filename} missing from the backup"
            header = next(csv.reader(path.open()))
            assert header == fields, f"{filename} header drifted from the schema"


def test_export_preserves_awkward_text():
    # The legacy exporter wrapped fields in quotes by interpolation, so any value
    # containing a quote or comma produced an unparseable file.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = RatingLog(tmp_path / "log")
        log.log_perfume(
            _perfume(
                brand='Marc-Antoine Barrois "B683"',
                name="Ganymede, the moon",
                comment="smells like\nnothing else",
            )
        )
        dest = export_dir(log, tmp_path / "backup")

        restored = RatingLog(tmp_path / "restored")
        report = import_files([dest / "perfumes.csv"], log=restored, apply=True)
        assert report.rejected == 0

        got = restored.perfumes()[0]
        assert got.brand == 'Marc-Antoine Barrois "B683"'
        assert got.name == "Ganymede, the moon"
        assert got.comment == "smells like\nnothing else"


# ── round trip ────────────────────────────────────────────────────────────────


def test_full_round_trip_is_lossless():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        original = _populated(tmp_path / "log")
        before = original.summary()

        dest = export_dir(original, tmp_path / "backup")
        restored = RatingLog(tmp_path / "restored")
        report = import_files(
            [dest / name for name, _, _, _ in TABLES.values()], log=restored, apply=True
        )
        assert report.rejected == 0, report.render()

        after = restored.summary()
        for key in (
            "perfumes",
            "rated",
            "rating_events",
            "combinations",
            "rated_combinations",
            "predictions_logged",
            "declared_preferences",
            "preference_events",
            "mean_rating",
            "distinct_notes",
        ):
            assert before[key] == after[key], f"{key} changed across the round trip"

        # Details, not just counts.
        assert [p.name for p, _ in restored.training_set()] == [
            p.name for p, _ in original.training_set()
        ]
        combo, a, b, rating = restored.pair_training_set()[0]
        assert rating == 4.0
        assert combo.first_applied_id == b.perfume_id
        assert restored.rating_events()[0].context == "hot afternoon"
        assert restored.palette() == {
            "family": {"citrus": 1.0},
            "note": {"Cumin": -1.0},
        }


def test_appending_after_a_restore_does_not_reuse_ids():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        original = _populated(tmp_path / "log")
        dest = export_dir(original, tmp_path / "backup")

        restored = RatingLog(tmp_path / "restored")
        import_files(
            [dest / name for name, _, _, _ in TABLES.values()], log=restored, apply=True
        )
        fresh = restored.log_perfume(_perfume(name="New One"))
        assert fresh.perfume_id == 3, "ids must continue past the restored rows"


# ── identification ────────────────────────────────────────────────────────────


def test_files_are_identified_by_header_not_filename():
    # Browsers rename repeat downloads to "perfumes (1).csv".
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log = _populated(tmp_path / "log")
        dest = export_dir(log, tmp_path / "backup")

        renamed = tmp_path / "perfumes (1).csv"
        shutil.copy(dest / "perfumes.csv", renamed)
        assert identify(renamed) == "perfumes"

        stranger = tmp_path / "shopping.csv"
        stranger.write_text("item,qty\nmilk,2\n", encoding="utf-8")
        assert identify(stranger) is None


def test_unrecognised_files_are_reported_not_imported():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        stranger = tmp_path / "shopping.csv"
        stranger.write_text("item,qty\nmilk,2\n", encoding="utf-8")

        report = import_files([stranger], data_dir=tmp_path / "log", apply=True)
        assert report.unrecognised == [str(stranger)]
        assert report.accepted == 0


def test_missing_file_raises():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            import_files([Path(tmp) / "nope.csv"], data_dir=Path(tmp) / "log")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("a missing upload must not look like an empty import")


# ── validation on import ──────────────────────────────────────────────────────


def _write(path: Path, table: str, rows: list[dict]) -> Path:
    _, fields, _, _ = TABLES[table]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_orphan_ratings_are_rejected():
    # A rating pointing at a perfume the import doesn't contain would silently
    # become a training example with no features.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        perfumes = _write(tmp_path / "perfumes.csv", "perfumes", [
            {"perfume_id": "1", "brand": "Dior", "name": "Sauvage",
             "perfume_type": "Woody", "perfume_medium": "", "perfume_strength": "",
             "top_notes": "bergamot", "middle_notes": "", "base_notes": "",
             "comment": "", "logged_at": "2026-01-01T00:00:00"},
        ])
        ratings = _write(tmp_path / "ratings.csv", "ratings", [
            {"rating_id": "1", "subject_type": "perfume", "subject_id": "1",
             "rating": "4.5", "context": "", "rated_at": "2026-01-02T00:00:00"},
            {"rating_id": "2", "subject_type": "perfume", "subject_id": "99",
             "rating": "5", "context": "", "rated_at": "2026-01-03T00:00:00"},
        ])

        log = RatingLog(tmp_path / "log")
        report = import_files([perfumes, ratings], log=log, apply=True)
        assert report.tables["ratings"].accepted == 1
        assert "perfume 99 not in the import" in report.tables["ratings"].rejected[0]
        assert len(log.training_set()) == 1


def test_bad_rows_are_rejected_individually():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        rows = [
            # valid
            {"perfume_id": "1", "brand": "A", "name": "Good", "perfume_type": "",
             "perfume_medium": "", "perfume_strength": "", "top_notes": "bergamot",
             "middle_notes": "", "base_notes": "", "comment": "", "logged_at": ""},
            # no notes at all
            {"perfume_id": "2", "brand": "B", "name": "Empty", "perfume_type": "",
             "perfume_medium": "", "perfume_strength": "", "top_notes": "",
             "middle_notes": "", "base_notes": "", "comment": "", "logged_at": ""},
            # duplicate id
            {"perfume_id": "1", "brand": "C", "name": "Clash", "perfume_type": "",
             "perfume_medium": "", "perfume_strength": "", "top_notes": "lemon",
             "middle_notes": "", "base_notes": "", "comment": "", "logged_at": ""},
        ]
        path = _write(tmp_path / "perfumes.csv", "perfumes", rows)

        log = RatingLog(tmp_path / "log")
        report = import_files([path], log=log, apply=True)
        assert report.tables["perfumes"].accepted == 1
        assert len(report.tables["perfumes"].rejected) == 2
        assert any("duplicate perfume_id" in r for r in report.tables["perfumes"].rejected)
        assert [p.name for p in log.perfumes()] == ["Good"]


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source = _populated(tmp_path / "log")
        dest = export_dir(source, tmp_path / "backup")

        target = RatingLog(tmp_path / "restored")
        report = import_files(
            [dest / "perfumes.csv"], log=target, apply=False
        )
        assert report.accepted == 2
        assert target.perfumes() == []


def test_import_into_a_populated_log_needs_replace():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source = _populated(tmp_path / "log")
        dest = export_dir(source, tmp_path / "backup")

        target = RatingLog(tmp_path / "target")
        target.log_perfume(_perfume(name="Already here"))

        try:
            import_files([dest / "perfumes.csv"], log=target, apply=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("importing over existing data must be opt-in")

        report = import_files(
            [dest / "perfumes.csv"], log=target, apply=True, replace=True
        )
        assert report.accepted == 2
        assert [p.name for p in target.perfumes()] == ["Sauvage", "Bleu"]


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 — standalone runner
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
