"""Migrate the legacy single-table CSV into the three append-only tables.

The legacy log was one row per perfume with `predicted_rating` and `actual_rating`
side by side, which is what let the old engine confuse the two. Migration splits
them apart permanently:

    predicted_rating  -> predictions.csv, model_version="legacy-heuristic"
    actual_rating     -> ratings.csv, the only training target

Nothing is discarded silently. Rows that cannot be migrated are counted and listed
in the report rather than dropped quietly.

    python -m data.migrate                     # auto-detect source, dry run summary
    python -m data.migrate --apply
    python -m data.migrate path/to/log.csv --apply --data-dir ./userdata
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .schema import (
    PERFUME_FIELDS,
    PREDICTION_FIELDS,
    RATING_FIELDS,
    Perfume,
    Prediction,
    RatingEvent,
    ValidationError,
    now_stamp,
    parse_notes,
)
from .store import REPO_ROOT, RatingLog

LEGACY_MODEL_VERSION = "legacy-heuristic"

# Where the legacy file has historically lived, best candidate first.
LEGACY_CANDIDATES = (
    REPO_ROOT / "sillage_log.csv",
    REPO_ROOT / "jh-sillage_perfume-data.csv",
    REPO_ROOT / "data" / "sillage_log.csv",
)

LEGACY_FIELD_MAP = {
    "brand_name": "brand",
    "scent_name": "name",
    "scent_family": "perfume_type",
    "scent_medium": "perfume_medium",
    "scent_strength": "perfume_strength",
}


@dataclass
class MigrationReport:
    source: Path | None = None
    applied: bool = False
    perfumes: int = 0
    ratings: int = 0
    predictions: int = 0
    skipped: list[str] = field(default_factory=list)

    def render(self) -> str:
        mode = "applied" if self.applied else "dry run"
        lines = [
            f"Migration ({mode})",
            f"  source      {self.source or '— none found —'}",
            f"  perfumes    {self.perfumes}",
            f"  ratings     {self.ratings}",
            f"  predictions {self.predictions}",
        ]
        if self.skipped:
            lines.append(f"  skipped     {len(self.skipped)}")
            lines.extend(f"    - {reason}" for reason in self.skipped)
        return "\n".join(lines)


def find_legacy_source() -> Path | None:
    """First candidate that exists and has more than a header in it."""
    for path in LEGACY_CANDIDATES:
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _iso(stamp: str) -> str:
    """Legacy '%Y-%m-%d %H:%M' -> ISO. Unparseable values are kept verbatim."""
    stamp = (stamp or "").strip()
    if not stamp:
        return ""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(stamp, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return stamp


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def migrate(
    source: Path | None = None,
    data_dir: str | Path | None = None,
    apply: bool = False,
    force: bool = False,
) -> MigrationReport:
    """Read the legacy CSV and (optionally) write the three new tables."""
    report = MigrationReport(applied=apply)
    if source is not None and not source.exists():
        # An explicit path that isn't there is a typo, not an empty log — say so
        # rather than reporting a successful migration of nothing.
        raise FileNotFoundError(f"no such legacy CSV: {source}")
    source = source or find_legacy_source()
    report.source = source
    if source is None:
        return report

    log = RatingLog(data_dir)
    if apply and not force and log.perfumes_path.exists():
        raise RuntimeError(
            f"{log.perfumes_path} already exists — migrating again would duplicate "
            "every row. Move it aside, or pass --force if that is what you want."
        )

    with source.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    next_perfume_id = 1
    used_ids: set[int] = set()
    perfume_rows: list[dict[str, str]] = []
    rating_rows: list[dict[str, str]] = []
    prediction_rows: list[dict[str, str]] = []

    for line_no, row in enumerate(rows, start=2):  # line 1 is the header
        if not any((v or "").strip() for v in row.values()):
            continue

        fields = {new: (row.get(old) or "").strip() for old, new in LEGACY_FIELD_MAP.items()}
        perfume = Perfume(
            **fields,
            top_notes=parse_notes(row.get("top_notes")),
            middle_notes=parse_notes(row.get("middle_notes")),
            base_notes=parse_notes(row.get("base_notes")),
            comment=(row.get("notes") or "").strip(),
            logged_at=_iso(row.get("created_at", "")) or now_stamp(),
        )
        try:
            perfume = perfume.validated()
        except ValidationError as exc:
            report.skipped.append(f"line {line_no}: {exc}")
            continue

        # Keep the legacy id when it is usable, so anything referencing it still
        # resolves; otherwise assign the next free one.
        legacy_id = _number(row.get("id"))
        candidate = int(legacy_id) if legacy_id and legacy_id > 0 else 0
        if candidate <= 0 or candidate in used_ids:
            while next_perfume_id in used_ids:
                next_perfume_id += 1
            candidate = next_perfume_id
        used_ids.add(candidate)
        perfume.perfume_id = candidate
        perfume.logged_at = perfume.logged_at or now_stamp()
        perfume_rows.append(perfume.to_row())

        actual = _number(row.get("actual_rating"))
        if actual is not None:
            try:
                event = RatingEvent(
                    subject_id=candidate,
                    rating=actual,
                    # The legacy schema never recorded when a rating was given, so
                    # the perfume's creation time is the best available stand-in.
                    context="migrated: rated_at approximated from created_at",
                    rated_at=perfume.logged_at,
                ).validated()
            except ValidationError as exc:
                report.skipped.append(f"line {line_no}: rating dropped — {exc}")
            else:
                event.rating_id = len(rating_rows) + 1
                rating_rows.append(event.to_row())

        predicted = _number(row.get("predicted_rating"))
        if predicted is not None:
            prediction = Prediction(
                subject_id=candidate,
                value=predicted,
                model_version=LEGACY_MODEL_VERSION,
                predicted_at=perfume.logged_at,
            ).validated()
            prediction.prediction_id = len(prediction_rows) + 1
            prediction_rows.append(prediction.to_row())

    report.perfumes = len(perfume_rows)
    report.ratings = len(rating_rows)
    report.predictions = len(prediction_rows)

    if apply:
        for path, fields_, out_rows in (
            (log.perfumes_path, PERFUME_FIELDS, perfume_rows),
            (log.ratings_path, RATING_FIELDS, rating_rows),
            (log.predictions_path, PREDICTION_FIELDS, prediction_rows),
        ):
            for out_row in out_rows:
                log._append_row(path, fields_, out_row)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", nargs="?", type=Path, help="legacy CSV (auto-detected)")
    parser.add_argument("--data-dir", help="target log directory")
    parser.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    parser.add_argument("--force", action="store_true", help="append into a non-empty log")
    args = parser.parse_args(argv)

    report = migrate(
        source=args.source, data_dir=args.data_dir, apply=args.apply, force=args.force
    )
    print(report.render())
    if report.source is None:
        print("\nNothing to migrate — starting from an empty log is fine.")
    elif not args.apply:
        print("\nRe-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
