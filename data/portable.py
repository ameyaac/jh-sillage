"""Export and import the log as plain CSV files.

This is the bridge between Python and the browser app. `index.html` keeps its data
in the browser and can download exactly these four files; this module reads them
back. Round-tripping is the backup story: the log is the one thing in the project
that cannot be regenerated.

    python -m data.portable export ~/Backups/sillage-2026-07-30
    python -m data.portable import ~/Downloads/*.csv --replace
    python -m data.portable check ~/Downloads/perfumes.csv

Import validates every row through the same record types the store uses, and
checks referential integrity: a rating pointing at a perfume that isn't in the
import is reported, not written. Files are identified by their header row rather
than their filename, so a browser download renamed to `perfumes (1).csv` still
lands in the right table.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path

from .schema import (
    COMBINATION_FIELDS,
    PERFUME_FIELDS,
    PREDICTION_FIELDS,
    RATING_FIELDS,
    SUBJECT_COMBINATION,
    SUBJECT_PERFUME,
    Combination,
    Perfume,
    Prediction,
    RatingEvent,
    ValidationError,
)
from .store import (
    COMBINATIONS_FILE,
    PERFUMES_FILE,
    PREDICTIONS_FILE,
    RATINGS_FILE,
    RatingLog,
)

# table name -> (filename, field list, record type, id column)
TABLES: dict[str, tuple[str, list[str], type, str]] = {
    "perfumes": (PERFUMES_FILE, PERFUME_FIELDS, Perfume, "perfume_id"),
    "combinations": (COMBINATIONS_FILE, COMBINATION_FIELDS, Combination, "combination_id"),
    "ratings": (RATINGS_FILE, RATING_FIELDS, RatingEvent, "rating_id"),
    "predictions": (PREDICTIONS_FILE, PREDICTION_FIELDS, Prediction, "prediction_id"),
}

# Dependency order: a row may only reference something imported before it.
IMPORT_ORDER = ("perfumes", "combinations", "ratings", "predictions")


@dataclass
class TableReport:
    accepted: int = 0
    rejected: list[str] = field(default_factory=list)


@dataclass
class TransferReport:
    applied: bool = False
    replaced: bool = False
    tables: dict[str, TableReport] = field(default_factory=dict)
    unrecognised: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> int:
        return sum(t.accepted for t in self.tables.values())

    @property
    def rejected(self) -> int:
        return sum(len(t.rejected) for t in self.tables.values())

    def render(self) -> str:
        mode = "applied" if self.applied else "dry run"
        if self.applied and self.replaced:
            mode += ", replaced existing log"
        lines = [f"Transfer ({mode})"]
        for name in IMPORT_ORDER:
            report = self.tables.get(name)
            if report is None:
                continue
            lines.append(f"  {name:<13} {report.accepted} accepted")
            for reason in report.rejected:
                lines.append(f"      rejected: {reason}")
        for path in self.unrecognised:
            lines.append(f"  unrecognised  {path}")
        if not self.tables and not self.unrecognised:
            lines.append("  nothing to do")
        return "\n".join(lines)


# ── export ────────────────────────────────────────────────────────────────────


def export_dir(log: RatingLog, dest: str | Path) -> Path:
    """Write all four tables to `dest`, headers included even when empty.

    Empty files are written on purpose: a backup that silently omits
    `ratings.csv` is indistinguishable from one where you had no ratings.
    """
    dest_path = Path(dest).expanduser()
    dest_path.mkdir(parents=True, exist_ok=True)
    for filename, fields, _, _ in TABLES.values():
        source = log.dir / filename
        rows = RatingLog._read_rows(source)
        with (dest_path / filename).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return dest_path


# ── import ────────────────────────────────────────────────────────────────────


def identify(path: Path) -> str | None:
    """Which table a CSV belongs to, by its header row. None if unrecognised."""
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh), None)
    except OSError:
        return None
    if not header:
        return None
    found = {h.strip() for h in header}
    for name, (_, fields, _, _) in TABLES.items():
        if found == set(fields):
            return name
    return None


def _read_table(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [row for row in csv.DictReader(fh) if any(v for v in row.values())]


def import_files(
    paths: list[str | Path],
    log: RatingLog | None = None,
    data_dir: str | Path | None = None,
    apply: bool = False,
    replace: bool = False,
) -> TransferReport:
    """Validate and (optionally) write CSVs produced by export or the browser app.

    `replace` overwrites the target tables — the restore-a-backup case. Without it,
    importing into a non-empty log is refused rather than merged: merging would
    need id remapping, and a half-correct remap silently attaches ratings to the
    wrong perfume.
    """
    report = TransferReport(applied=apply, replaced=replace)
    log = log or RatingLog(data_dir)

    by_table: dict[str, list[dict[str, str]]] = {}
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"no such file: {path}")
        table = identify(path)
        if table is None:
            report.unrecognised.append(str(path))
            continue
        by_table.setdefault(table, []).extend(_read_table(path))

    if not by_table:
        return report

    if apply and not replace:
        existing = any(
            (log.dir / filename).exists() and (log.dir / filename).stat().st_size > 0
            for filename, _, _, _ in TABLES.values()
        )
        if existing:
            raise RuntimeError(
                f"{log.dir} already holds data. Pass --replace to overwrite it, or "
                "import into an empty directory with --into."
            )

    # Ids that will exist after this import, for referential checks.
    known: dict[str, set[int]] = {"perfumes": set(), "combinations": set()}
    validated: dict[str, list[dict[str, str]]] = {}

    for name in IMPORT_ORDER:
        rows = by_table.get(name)
        if rows is None:
            continue
        _, fields, record_type, id_column = TABLES[name]
        table_report = TableReport()
        seen_ids: set[int] = set()
        out_rows: list[dict[str, str]] = []

        for line_no, row in enumerate(rows, start=2):
            try:
                record = record_type.from_row(row).validated()
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                table_report.rejected.append(f"{name} line {line_no}: {exc}")
                continue

            record_id = getattr(record, id_column)
            if record_id in seen_ids:
                table_report.rejected.append(
                    f"{name} line {line_no}: duplicate {id_column} {record_id}"
                )
                continue

            problem = _referential_problem(name, record, known)
            if problem:
                table_report.rejected.append(f"{name} line {line_no}: {problem}")
                continue

            seen_ids.add(record_id)
            if name in known:
                known[name].add(record_id)
            out_rows.append(record.to_row())

        table_report.accepted = len(out_rows)
        report.tables[name] = table_report
        validated[name] = out_rows

    if apply:
        for name in IMPORT_ORDER:
            if name not in validated:
                continue
            filename, fields, _, _ = TABLES[name]
            target = log.dir / filename
            with target.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(validated[name])

    return report


def _referential_problem(
    table: str, record: object, known: dict[str, set[int]]
) -> str | None:
    """Reject rows pointing at things the import doesn't contain."""
    if table == "combinations":
        missing = [i for i in record.member_ids if i not in known["perfumes"]]  # type: ignore[attr-defined]
        if missing:
            return f"member perfume {missing[0]} not in the import"
        return None

    if table in ("ratings", "predictions"):
        subject_type = record.subject_type  # type: ignore[attr-defined]
        subject_id = record.subject_id  # type: ignore[attr-defined]
        bucket = "perfumes" if subject_type == SUBJECT_PERFUME else "combinations"
        if subject_type not in (SUBJECT_PERFUME, SUBJECT_COMBINATION):
            return f"unknown subject_type {subject_type!r}"
        if subject_id not in known[bucket]:
            return f"{subject_type} {subject_id} not in the import"
    return None


# ── cli ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up and restore the rating log.")
    parser.add_argument("--data-dir", help="the log directory to read or write")
    sub = parser.add_subparsers(dest="command", required=True)

    exporter = sub.add_parser("export", help="write the four CSVs to a directory")
    exporter.add_argument("dest", type=Path)

    importer = sub.add_parser("import", help="read CSVs back into the log")
    importer.add_argument("paths", nargs="+", type=Path)
    importer.add_argument("--into", help="target log directory (overrides --data-dir)")
    importer.add_argument("--replace", action="store_true", help="overwrite the log")
    importer.add_argument(
        "--dry-run", action="store_true", help="validate without writing"
    )

    checker = sub.add_parser("check", help="report which table each CSV belongs to")
    checker.add_argument("paths", nargs="+", type=Path)

    args = parser.parse_args(argv)

    if args.command == "export":
        log = RatingLog(args.data_dir)
        dest = export_dir(log, args.dest)
        print(f"Exported {log.dir} -> {dest}")
        for name in IMPORT_ORDER:
            filename = TABLES[name][0]
            rows = max(0, sum(1 for _ in (dest / filename).open()) - 1)
            print(f"  {filename:<18} {rows} rows")
        return 0

    if args.command == "check":
        for path in args.paths:
            table = identify(Path(path))
            print(f"  {path}: {table or 'unrecognised'}")
        return 0

    report = import_files(
        args.paths,
        data_dir=args.into or args.data_dir,
        apply=not args.dry_run,
        replace=args.replace,
    )
    print(report.render())
    if args.dry_run:
        print("\nRe-run without --dry-run to write.")
    return 1 if report.rejected and not report.accepted else 0


if __name__ == "__main__":
    raise SystemExit(main())
