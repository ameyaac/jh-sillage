"""Append-only store for the Sillage rating log.

Five CSV tables plus a `_meta.json` recording the schema version. Nothing is ever
rewritten in place: logging a perfume, pairing it with another, rating either,
predicting either, and declaring a taste preference are all appends. That makes the
log safe to tail, diff, and back up, and it means a crash mid-write can at worst
lose the last line rather than corrupt the file.

Location, in order of precedence:

    $SILLAGE_DATA_DIR
    <repo>/userdata/

The data directory is user data and stays out of git.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from .schema import (
    COMBINATION_FIELDS,
    PERFUME_FIELDS,
    PREDICTION_FIELDS,
    PREFERENCE_FIELDS,
    RATING_FIELDS,
    SCHEMA_VERSION,
    SUBJECT_COMBINATION,
    SUBJECT_PERFUME,
    UPGRADABLE_FROM,
    Combination,
    Perfume,
    Prediction,
    PreferenceDeclaration,
    RatingEvent,
    ValidationError,
    now_stamp,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "userdata"

PERFUMES_FILE = "perfumes.csv"
COMBINATIONS_FILE = "combinations.csv"
RATINGS_FILE = "ratings.csv"
PREDICTIONS_FILE = "predictions.csv"
PREFERENCES_FILE = "preferences.csv"
META_FILE = "_meta.json"


def resolve_data_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("SILLAGE_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_DATA_DIR


class SchemaVersionError(RuntimeError):
    """The on-disk log was written by a different version of this schema."""


class RatingLog:
    """The user's rating log. One instance per data directory."""

    def __init__(self, data_dir: str | os.PathLike[str] | None = None) -> None:
        self.dir = resolve_data_dir(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._check_meta()

    # ── paths & meta ──────────────────────────────────────────────────────────

    @property
    def perfumes_path(self) -> Path:
        return self.dir / PERFUMES_FILE

    @property
    def combinations_path(self) -> Path:
        return self.dir / COMBINATIONS_FILE

    @property
    def ratings_path(self) -> Path:
        return self.dir / RATINGS_FILE

    @property
    def predictions_path(self) -> Path:
        return self.dir / PREDICTIONS_FILE

    @property
    def preferences_path(self) -> Path:
        return self.dir / PREFERENCES_FILE

    @property
    def meta_path(self) -> Path:
        return self.dir / META_FILE

    def _check_meta(self) -> None:
        """Write the version on first use; upgrade what we can; refuse the rest.

        Reading a newer log with older code would parse some columns and silently
        drop others, which is worse than failing. Older logs are upgraded in place
        when the difference is purely additive — a v1 log has no preferences.csv,
        and an absent table reads as an empty one.
        """
        if not self.meta_path.exists():
            self._write_meta({"schema_version": SCHEMA_VERSION, "created_at": now_stamp()})
            return
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchemaVersionError(f"cannot read {self.meta_path}: {exc}") from exc

        found = meta.get("schema_version")
        if found == SCHEMA_VERSION:
            return
        if found in UPGRADABLE_FROM:
            meta["schema_version"] = SCHEMA_VERSION
            meta.setdefault("upgrades", []).append(
                {"from": found, "to": SCHEMA_VERSION, "at": now_stamp()}
            )
            self._write_meta(meta)
            return
        raise SchemaVersionError(
            f"{self.dir} holds schema v{found}, this code speaks v{SCHEMA_VERSION} "
            f"and can upgrade from {sorted(UPGRADABLE_FROM)}. Migrate it before use."
        )

    def _write_meta(self, meta: dict) -> None:
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.meta_path)

    # ── low-level csv ─────────────────────────────────────────────────────────

    @staticmethod
    def _read_rows(path: Path) -> list[dict[str, str]]:
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8") as fh:
            return [row for row in csv.DictReader(fh) if any(v for v in row.values())]

    @staticmethod
    def _append_row(path: Path, fields: list[str], row: dict[str, str]) -> None:
        new_file = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            writer.writerow(row)

    @staticmethod
    def _next_id(rows: list[dict[str, str]], key: str) -> int:
        """max + 1, never len + 1.

        The legacy store used `len(existing) + 1`, which reissues an id the moment
        any row is removed and then silently attaches ratings to the wrong perfume.
        """
        highest = 0
        for row in rows:
            try:
                highest = max(highest, int(row[key]))
            except (KeyError, TypeError, ValueError):
                continue
        return highest + 1

    # ── writes ────────────────────────────────────────────────────────────────

    def log_perfume(self, perfume: Perfume) -> Perfume:
        """Append a composition and return it with its assigned id and timestamp."""
        record = perfume.validated()
        rows = self._read_rows(self.perfumes_path)
        record.perfume_id = self._next_id(rows, "perfume_id")
        record.logged_at = record.logged_at or now_stamp()
        self._append_row(self.perfumes_path, PERFUME_FIELDS, record.to_row())
        return record

    def log_combination(
        self,
        perfume_id_a: int,
        perfume_id_b: int,
        applied_first: str = "",
        comment: str = "",
    ) -> Combination:
        """Append a layered pair, by reference to its two members.

        Re-logging the same pair returns the existing record rather than creating a
        duplicate, so ratings for a pair accumulate against one id.
        """
        record = Combination(
            perfume_id_a=perfume_id_a,
            perfume_id_b=perfume_id_b,
            applied_first=applied_first,
            comment=comment,
        ).validated()

        known = set(self.perfumes_by_id())
        missing = [i for i in record.member_ids if i not in known]
        if missing:
            raise ValidationError(f"no perfume with id {missing[0]}")

        for existing in self.combinations():
            if (
                existing.member_ids == record.member_ids
                and existing.applied_first == record.applied_first
            ):
                return existing

        record.combination_id = self._next_id(
            self._read_rows(self.combinations_path), "combination_id"
        )
        record.created_at = record.created_at or now_stamp()
        self._append_row(self.combinations_path, COMBINATION_FIELDS, record.to_row())
        return record

    def log_rating(
        self,
        perfume_id: int | None = None,
        rating: float | None = None,
        *,
        combination_id: int | None = None,
        context: str = "",
        rated_at: str = "",
    ) -> RatingEvent:
        """Append a rating event for a perfume or a combination.

            log.log_rating(perfume_id, 4.5)
            log.log_rating(rating=4.0, combination_id=combo.combination_id)

        Re-rating is a new event, not an edit. `current_ratings()` takes the latest;
        the history stays on disk for drift analysis.
        """
        if (perfume_id is None) == (combination_id is None):
            raise ValidationError("pass exactly one of perfume_id or combination_id")
        if rating is None:
            raise ValidationError("rating is required")

        if perfume_id is not None:
            subject_type, subject_id = SUBJECT_PERFUME, perfume_id
            if perfume_id not in self.perfumes_by_id():
                raise ValidationError(f"no perfume with id {perfume_id}")
        else:
            subject_type, subject_id = SUBJECT_COMBINATION, combination_id
            if combination_id not in self.combinations_by_id():
                raise ValidationError(f"no combination with id {combination_id}")

        record = RatingEvent(
            subject_id=subject_id,
            subject_type=subject_type,
            rating=rating,
            context=context,
            rated_at=rated_at,
        ).validated()
        record.rating_id = self._next_id(self._read_rows(self.ratings_path), "rating_id")
        record.rated_at = record.rated_at or now_stamp()
        self._append_row(self.ratings_path, RATING_FIELDS, record.to_row())
        return record

    def log_preference(
        self, scope: str, key: str, weight: float, declared_at: str = ""
    ) -> PreferenceDeclaration:
        """Append a declared taste preference.

        Declaring the same thing again appends rather than edits, so a change of
        mind is recorded. `palette()` reads the latest declaration per key.
        Nothing in the training path reads these — they are a prior, not evidence.
        """
        record = PreferenceDeclaration(
            scope=scope, key=key, weight=weight, declared_at=declared_at
        ).validated()
        record.preference_id = self._next_id(
            self._read_rows(self.preferences_path), "preference_id"
        )
        record.declared_at = record.declared_at or now_stamp()
        self._append_row(self.preferences_path, PREFERENCE_FIELDS, record.to_row())
        return record

    def log_prediction(self, prediction: Prediction) -> Prediction:
        """Append a prediction. Nothing in the training path ever reads these back."""
        record = prediction.validated()
        record.prediction_id = self._next_id(
            self._read_rows(self.predictions_path), "prediction_id"
        )
        record.predicted_at = record.predicted_at or now_stamp()
        self._append_row(self.predictions_path, PREDICTION_FIELDS, record.to_row())
        return record

    # ── reads ─────────────────────────────────────────────────────────────────

    def perfumes(self) -> list[Perfume]:
        return [Perfume.from_row(r) for r in self._read_rows(self.perfumes_path)]

    def perfumes_by_id(self) -> dict[int, Perfume]:
        return {p.perfume_id: p for p in self.perfumes() if p.perfume_id is not None}

    def combinations(self) -> list[Combination]:
        return [Combination.from_row(r) for r in self._read_rows(self.combinations_path)]

    def combinations_by_id(self) -> dict[int, Combination]:
        return {
            c.combination_id: c
            for c in self.combinations()
            if c.combination_id is not None
        }

    def rating_events(self, subject_type: str | None = None) -> list[RatingEvent]:
        """Every rating ever given, oldest first. Optionally one subject type only."""
        events = [RatingEvent.from_row(r) for r in self._read_rows(self.ratings_path)]
        if subject_type is not None:
            events = [e for e in events if e.subject_type == subject_type]
        events.sort(key=lambda e: (e.rated_at, e.rating_id or 0))
        return events

    def predictions(self, subject_type: str | None = None) -> list[Prediction]:
        records = [Prediction.from_row(r) for r in self._read_rows(self.predictions_path)]
        if subject_type is not None:
            records = [p for p in records if p.subject_type == subject_type]
        return records

    def current_ratings(
        self, subject_type: str = SUBJECT_PERFUME
    ) -> dict[int, RatingEvent]:
        """Latest rating per subject. Later events overwrite earlier ones."""
        latest: dict[int, RatingEvent] = {}
        for event in self.rating_events(subject_type):  # already chronological
            latest[event.subject_id] = event
        return latest

    @staticmethod
    def _latest_per_subject(
        pairs: list[tuple[int, object, float]],
    ) -> list[tuple[object, float]]:
        """Keep the last rating per subject, at that subject's most recent position."""
        seen: set[int] = set()
        out: list[tuple[object, float]] = []
        for subject_id, subject, rating in reversed(pairs):
            if subject_id in seen:
                continue
            seen.add(subject_id)
            out.append((subject, rating))
        out.reverse()
        return out

    def training_set(self) -> list[tuple[Perfume, float]]:
        """Rated perfumes with their current rating, in the order they were rated.

        Chronological order is not incidental — prefix evaluation (train on the
        first k, test on k+1) needs it to detect taste drift, which shuffled
        cross-validation hides.

        Single perfumes only. Layered pairs are a different target with a different
        model (a correction on top of this one), so mixing them in here would
        quietly contaminate the composition-level fit.
        """
        perfumes = self.perfumes_by_id()
        rows: list[tuple[int, object, float]] = []
        for event in self.rating_events(SUBJECT_PERFUME):
            perfume = perfumes.get(event.subject_id)
            if perfume is None:  # rating for a perfume no longer in the log
                continue
            rows.append((event.subject_id, perfume, event.rating))
        return self._latest_per_subject(rows)  # type: ignore[return-value]

    def pair_training_set(self) -> list[tuple[Combination, Perfume, Perfume, float]]:
        """Rated combinations with both members resolved, oldest rating first.

        Feeds the layering residual model: the target there is the *correction* to
        the blended-composition prediction, not the pair rating itself.
        """
        perfumes = self.perfumes_by_id()
        combos = self.combinations_by_id()
        rows: list[tuple[int, object, float]] = []
        for event in self.rating_events(SUBJECT_COMBINATION):
            combo = combos.get(event.subject_id)
            if combo is None:
                continue
            a, b = perfumes.get(combo.perfume_id_a), perfumes.get(combo.perfume_id_b)
            if a is None or b is None:  # a member left the log
                continue
            rows.append((event.subject_id, (combo, a, b), event.rating))
        return [
            (combo, a, b, rating)
            for (combo, a, b), rating in self._latest_per_subject(rows)  # type: ignore[misc]
        ]

    def preference_events(self) -> list[PreferenceDeclaration]:
        """Every declaration ever made, oldest first."""
        events = [
            PreferenceDeclaration.from_row(r)
            for r in self._read_rows(self.preferences_path)
        ]
        events.sort(key=lambda p: (p.declared_at, p.preference_id or 0))
        return events

    def palette(self, include_retracted: bool = False) -> dict[str, dict[str, float]]:
        """The user's declared smell palette: {scope: {key: weight}}.

        Latest declaration per (scope, key) wins. Retractions (weight 0) drop out
        unless asked for, so an empty palette and a fully-retracted one look the
        same to the model — which is correct, since both mean "no prior".

        This is optional by design. An empty palette is a valid, complete answer;
        the model then starts from the population/neutral prior instead.
        """
        latest: dict[tuple[str, str], PreferenceDeclaration] = {}
        for event in self.preference_events():  # already chronological
            latest[(event.scope, event.key)] = event

        out: dict[str, dict[str, float]] = {}
        for (scope, key), event in latest.items():
            if event.is_retraction and not include_retracted:
                continue
            out.setdefault(scope, {})[key] = event.weight
        return out

    def has_palette(self) -> bool:
        return any(self.palette().values())

    def unrated(self) -> list[Perfume]:
        rated = set(self.current_ratings(SUBJECT_PERFUME))
        return [p for p in self.perfumes() if p.perfume_id not in rated]

    def unrated_combinations(self) -> list[Combination]:
        rated = set(self.current_ratings(SUBJECT_COMBINATION))
        return [c for c in self.combinations() if c.combination_id not in rated]

    def groups(self) -> dict[int, str]:
        """perfume_id -> grouping key for grouped cross-validation.

        Perfumes with no brand or name fall back to their own id, i.e. their own
        group, which is the conservative choice.
        """
        out: dict[int, str] = {}
        for perfume in self.perfumes():
            if perfume.perfume_id is None:
                continue
            out[perfume.perfume_id] = perfume.group_key or f"id:{perfume.perfume_id}"
        return out

    def combination_groups(self) -> dict[int, str]:
        """combination_id -> grouping key, built from the members' own group keys.

        So "Sauvage EDT over Bleu EDP" and "Sauvage EDP over Bleu EDT" are one
        group. Application order is ignored here: both orders of the same two
        juices are near-duplicates as far as split leakage is concerned.
        """
        perfume_groups = self.groups()
        out: dict[int, str] = {}
        for combo in self.combinations():
            if combo.combination_id is None:
                continue
            members = sorted(
                perfume_groups.get(i, f"id:{i}") for i in combo.member_ids
            )
            out[combo.combination_id] = " + ".join(members)
        return out

    def summary(self) -> dict[str, object]:
        training = self.training_set()
        pairs = self.pair_training_set()
        ratings = [r for _, r in training]
        groups = self.groups()
        combo_groups = self.combination_groups()
        return {
            "data_dir": str(self.dir),
            "schema_version": SCHEMA_VERSION,
            "perfumes": len(self.perfumes()),
            "rated": len(training),
            "rating_events": len(self.rating_events()),
            "distinct_groups": len({groups[p.perfume_id] for p, _ in training}),
            "combinations": len(self.combinations()),
            "rated_combinations": len(pairs),
            "distinct_combination_groups": len(
                {combo_groups[c.combination_id] for c, _, _, _ in pairs}
            ),
            "predictions_logged": len(self.predictions()),
            "declared_preferences": sum(len(v) for v in self.palette().values()),
            "preference_events": len(self.preference_events()),
            "mean_rating": round(sum(ratings) / len(ratings), 3) if ratings else None,
            "distinct_notes": len(
                {n.casefold() for p in self.perfumes() for n in p.all_notes}
            ),
        }
