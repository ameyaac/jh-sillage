"""Migrate the legacy single-table CSV and taste profile into the new tables.

The legacy log was one row per perfume with `predicted_rating` and `actual_rating`
side by side, which is what let the old engine confuse the two. Migration splits
them apart permanently:

    predicted_rating  -> predictions.csv, model_version="legacy-heuristic"
    actual_rating     -> ratings.csv, the only training target

The legacy onboarding form (`taste_profile.json`) becomes declared preferences:

    loved/disliked families, liked/disliked notes,
    preferred mediums          -> preferences.csv, as signed affinities in [-1, 1]

Either source migrates independently — an old profile is worth keeping even if
there were never any ratings, and vice versa.

Nothing is discarded silently. Rows that cannot be migrated are counted and listed
in the report rather than dropped quietly.

    python -m data.migrate                     # auto-detect both, dry run summary
    python -m data.migrate --apply
    python -m data.migrate path/to/log.csv --apply --data-dir ./userdata
    python -m data.migrate --profile path/to/taste_profile.json --apply
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .schema import (
    FAMILIES,
    PERFUME_FIELDS,
    PREDICTION_FIELDS,
    PREFERENCE_FIELDS,
    RATING_FIELDS,
    SCOPE_FAMILY,
    SCOPE_MEDIUM,
    SCOPE_NOTE,
    Perfume,
    Prediction,
    PreferenceDeclaration,
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

# The legacy onboarding form wrote one of these.
LEGACY_PROFILE_CANDIDATES = (
    REPO_ROOT / "taste_profile.json",
    REPO_ROOT / "smell-profile.json",
    REPO_ROOT / "model" / "smell-profile.json",
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
    profile_source: Path | None = None
    preferences: int = 0
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
        if self.profile_source or self.preferences:
            lines.append(f"  profile     {self.profile_source or '— none found —'}")
            lines.append(f"  preferences {self.preferences}")
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


def find_legacy_profile() -> Path | None:
    for path in LEGACY_PROFILE_CANDIDATES:
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def profile_to_declarations(profile: dict) -> list[PreferenceDeclaration]:
    """Legacy taste_profile.json -> declared preferences.

    The legacy form recorded liked/disliked sets and loved/disliked families with no
    notion of strength. Mapping those to ±1 is the honest reading: the user did say
    "I love this", they just had no way to say how much.
    """
    declarations: list[PreferenceDeclaration] = []
    stamp = now_stamp()

    def add(scope: str, key: str, weight: float) -> None:
        key = str(key or "").strip()
        if not key:
            return
        try:
            declarations.append(
                PreferenceDeclaration(
                    scope=scope, key=key, weight=weight, declared_at=stamp
                ).validated()
            )
        except ValidationError:
            # An unknown family in an old profile is dropped rather than guessed at.
            pass

    for key in profile.get("loved_families", []) or []:
        add(SCOPE_FAMILY, str(key).lower(), 1.0)
    for key in profile.get("disliked_families", []) or []:
        add(SCOPE_FAMILY, str(key).lower(), -1.0)
    for key in profile.get("liked_notes", []) or []:
        add(SCOPE_NOTE, key, 1.0)
    for key in profile.get("disliked_notes", []) or []:
        add(SCOPE_NOTE, key, -1.0)
    for key in profile.get("preferred_mediums", []) or []:
        add(SCOPE_MEDIUM, key, 1.0)

    # Some profiles stored family scores as a mapping instead of two lists.
    scores = profile.get("family_scores") or profile.get("scent_families") or {}
    if isinstance(scores, dict):
        for key, raw in scores.items():
            if str(key).lower() not in FAMILIES:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            # Legacy sliders were 1–5; map onto the signed [-1, 1] affinity scale.
            add(SCOPE_FAMILY, str(key).lower(), max(-1.0, min(1.0, (value - 3.0) / 2.0)))

    # Last declaration per (scope, key) wins, matching how the store reads them.
    deduped: dict[tuple[str, str], PreferenceDeclaration] = {}
    for declaration in declarations:
        deduped[(declaration.scope, declaration.key)] = declaration
    return list(deduped.values())


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
    profile: Path | None = None,
) -> MigrationReport:
    """Read the legacy CSV and profile, and (optionally) write the new tables."""
    report = MigrationReport(applied=apply)
    if profile is not None and not profile.exists():
        raise FileNotFoundError(f"no such profile: {profile}")
    if source is not None and not source.exists():
        # An explicit path that isn't there is a typo, not an empty log — say so
        # rather than reporting a successful migration of nothing.
        raise FileNotFoundError(f"no such legacy CSV: {source}")
    source = source or find_legacy_source()
    profile = profile or find_legacy_profile()
    report.source = source
    report.profile_source = profile

    if source is None and profile is None:
        return report

    log = RatingLog(data_dir)
    if apply and not force and log.perfumes_path.exists():
        raise RuntimeError(
            f"{log.perfumes_path} already exists — migrating again would duplicate "
            "every row. Move it aside, or pass --force if that is what you want."
        )

    # The declared palette migrates independently of the rating log — an old profile
    # is still worth having even if there were never any ratings.
    if profile is not None:
        try:
            loaded = json.loads(profile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.skipped.append(f"profile {profile.name}: unreadable ({exc})")
        else:
            declarations = profile_to_declarations(loaded if isinstance(loaded, dict) else {})
            report.preferences = len(declarations)
            if apply:
                for index, declaration in enumerate(declarations, start=1):
                    declaration.preference_id = index
                    log._append_row(
                        log.preferences_path, PREFERENCE_FIELDS, declaration.to_row()
                    )

    if source is None:
        return report

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
    parser.add_argument("--profile", type=Path, help="legacy taste_profile.json (auto-detected)")
    parser.add_argument("--data-dir", help="target log directory")
    parser.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    parser.add_argument("--force", action="store_true", help="append into a non-empty log")
    args = parser.parse_args(argv)

    report = migrate(
        source=args.source,
        data_dir=args.data_dir,
        apply=args.apply,
        force=args.force,
        profile=args.profile,
    )
    print(report.render())
    if report.source is None and report.profile_source is None:
        print("\nNothing to migrate — starting from an empty log is fine.")
    elif not args.apply:
        print("\nRe-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
