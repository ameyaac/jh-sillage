"""Record types and validation for the Sillage rating log.

The log is five append-only tables, not one mutable one:

    perfumes.csv      immutable compositions
    combinations.csv  two perfumes worn together, by reference
    ratings.csv       ratings the user actually gave, as timestamped events
    predictions.csv   what the model said, when, and with which feature spec
    preferences.csv   taste the user *declared*, as opposed to demonstrated

`preferences.csv` is a prior, not evidence. It holds signed affinities in [-1, +1]
over families, axes, notes and mediums — deliberately not on the 1–5 rating scale,
so it can never be mistaken for a training target. It seeds the model before any
ratings exist and gets overridden by the likelihood as ratings arrive.

Ratings and predictions are polymorphic over their subject — a layered pair is
judged on the same 1–5 scale as a single perfume, so the evaluation and storage
code does not need two of everything.

The split is load-bearing. `RatingLog.training_set()` reads perfumes + ratings and
has no code path to predictions.csv, so the model cannot be trained on its own
output. The previous engine did exactly that — `e.get("actual_rating") or
e.get("predicted_rating", 0)` silently fed predictions back in as ground truth
whenever a perfume was unrated, because `""` is falsy.

Ratings are events rather than a mutable column so that re-rating a perfume months
later preserves both judgments. Taste drift is a thing we want to measure, not
overwrite.

Storage keeps note strings exactly as the user typed them. Normalization, aliasing
and family resolution belong to the feature layer; doing any of it here would make
the log lossy and the resolution cascade untestable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

SCHEMA_VERSION = 6

# The version goes up with every release, so it also names the app version.
# Versions this code can open and upgrade in place. Every upgrade so far is additive:
#   v1 -> v2 added preferences.csv; a v1 log simply has no declared palette.
#   v2 -> v3 lets perfume_type hold several ";"-joined scent families; a v2
#            single-family cell is already a valid one-element v3 list.
#   v3 -> v4 no format change: layering page, deleting entries, themes.
#   v4 -> v5 no format change: Appearance became a centred dialog.
#   v5 -> v6 no format change: Appearance dialog flows as one column.
UPGRADABLE_FROM = frozenset({1, 2, 3, 4, 5})

# Note lists share one CSV cell, joined by this character. Notes containing it are
# rejected at write time rather than silently mangled at read time.
NOTE_SEPARATOR = ";"

LAYERS = ("top", "middle", "base")

MIN_RATING = 1.0
MAX_RATING = 5.0
RATING_STEP = 0.5

PERFUME_FIELDS = [
    "perfume_id",
    "brand",
    "name",
    "perfume_type",
    "perfume_medium",
    "perfume_strength",
    "top_notes",
    "middle_notes",
    "base_notes",
    "comment",
    "logged_at",
]

COMBINATION_FIELDS = [
    "combination_id",
    "perfume_id_a",
    "perfume_id_b",
    "applied_first",
    "comment",
    "created_at",
]

# What a rating or prediction is *about*. Ratings and predictions are polymorphic
# because a layered pair is rated on the same 1–5 scale as a single perfume, and
# the evaluation code should not need two of everything to handle it.
SUBJECT_PERFUME = "perfume"
SUBJECT_COMBINATION = "combination"
SUBJECT_TYPES = (SUBJECT_PERFUME, SUBJECT_COMBINATION)

RATING_FIELDS = [
    "rating_id",
    "subject_type",
    "subject_id",
    "rating",
    "context",
    "rated_at",
]

PREDICTION_FIELDS = [
    "prediction_id",
    "subject_type",
    "subject_id",
    "value",
    "interval_low",
    "interval_high",
    "model_version",
    "spec_hash",
    "predicted_at",
]

# Which perfume went on the skin first. Layering is order-dependent: the one
# sprayed second dominates the opening, while the dry-down is close to the union
# of both bases. "" means the user didn't record it.
APPLIED_FIRST_VALUES = ("", "a", "b")

PREFERENCE_FIELDS = [
    "preference_id",
    "scope",
    "key",
    "weight",
    "declared_at",
]

# What a declared preference is *about*. Families and axes are where declarations
# are most useful, because they transfer to notes the user has never met; specific
# notes and mediums are what people find easiest to articulate.
SCOPE_FAMILY = "family"
SCOPE_AXIS = "axis"
SCOPE_NOTE = "note"
SCOPE_MEDIUM = "medium"
PREFERENCE_SCOPES = (SCOPE_FAMILY, SCOPE_AXIS, SCOPE_NOTE, SCOPE_MEDIUM)

# Signed affinity, NOT a rating. Kept off the 1–5 scale on purpose so a declared
# preference can never be confused for an observed one.
MIN_WEIGHT = -1.0
MAX_WEIGHT = 1.0

# What the UI's five-way control maps onto. Zero is a real value: it means "I
# explicitly have no feeling about this", which retracts an earlier declaration.
PREFERENCE_LEVELS = {
    "love": 1.0,
    "like": 0.5,
    "neutral": 0.0,
    "dislike": -0.5,
    "avoid": -1.0,
}

# The 14-family taxonomy from ARCHITECTURE.md §3.
FAMILIES = (
    "citrus",
    "floral",
    "white-floral",
    "fruity",
    "green",
    "aromatic",
    "spicy",
    "woody",
    "resinous",
    "amber",
    "gourmand",
    "musk",
    "animalic",
    "aquatic",
)

# The 8 perceptual axes from ARCHITECTURE.md §1.2 (block B3).
AXES = (
    "sweet",
    "fresh",
    "warm",
    "woody",
    "green",
    "spicy",
    "animalic",
    "powdery",
)


class ValidationError(ValueError):
    """A record was rejected before it reached disk."""


def now_stamp() -> str:
    """Second-resolution local ISO timestamp. Sortable as a plain string."""
    return datetime.now().isoformat(timespec="seconds")


# ── note lists ────────────────────────────────────────────────────────────────


def parse_notes(cell: str | None) -> list[str]:
    """CSV cell -> note list. Tolerant: this reads data that already exists."""
    if not cell:
        return []
    return [n.strip() for n in cell.split(NOTE_SEPARATOR) if n.strip()]


def format_notes(notes: list[str]) -> str:
    return NOTE_SEPARATOR.join(notes)


def clean_families(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Scent families -> a clean list. Accepts a list or a ``;``-joined cell.

    A perfume can sit in more than one family (a woody-spicy oriental is all three),
    so ``perfume_type`` holds a list stored in one cell, exactly like a note layer.
    A single family is just a one-element list, so logs written before multi-family
    support read back unchanged.
    """
    if value is None:
        return []
    items = parse_notes(value) if isinstance(value, str) else list(value)
    return clean_notes(items, "family")


def clean_notes(notes: list[str] | None, layer: str) -> list[str]:
    """Strict: strip, drop blanks, reject separators, de-duplicate case-insensitively.

    Preserves the user's original casing and order — the first spelling of a
    repeated note wins.
    """
    if not notes:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in notes:
        note = str(raw).strip()
        if not note:
            continue
        if NOTE_SEPARATOR in note:
            raise ValidationError(
                f"{layer} note {note!r} contains {NOTE_SEPARATOR!r}, "
                "which separates notes in storage"
            )
        key = note.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(note)
    return cleaned


# ── identity ──────────────────────────────────────────────────────────────────

# Concentration terms only. Flanker words that usually signal a genuinely different
# juice ("Intense", "Elixir", "Absolu") are deliberately NOT stripped — merging
# those would hide real differences. Grouping is for near-duplicates like
# "Bleu EDT" vs "Bleu EDP", which share a composition.
_CONCENTRATION_TERMS = (
    "eau de parfum",
    "eau de toilette",
    "eau de cologne",
    "eau fraiche",
    "extrait de parfum",
    "parfum",
    "cologne",
    "edp",
    "edt",
    "edc",
)

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def group_key(brand: str, name: str) -> str:
    """Identity key for grouped cross-validation.

    The same fragrance logged twice, or two concentrations of it, must not land on
    opposite sides of a train/test split — that leaks the target and flatters the
    model. Returns "" when both fields are blank, which callers treat as ungrouped.
    """
    text = f"{brand} {name}".casefold()
    text = _PUNCT.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    for term in _CONCENTRATION_TERMS:
        text = re.sub(rf"\b{re.escape(term)}\b", " ", text)
    return _SPACES.sub(" ", text).strip()


# ── records ───────────────────────────────────────────────────────────────────


@dataclass
class Perfume:
    """One logged composition. Immutable once written."""

    brand: str = ""
    name: str = ""
    # One or more scent families, ``;``-joined like a note layer. Use
    # ``perfume_types`` for the list form.
    perfume_type: str = ""
    perfume_medium: str = ""
    # Optional. Carried through from the legacy schema's `scent_strength` so
    # migration is lossless; the feature layer may project it as an ordinal.
    perfume_strength: str = ""
    top_notes: list[str] = field(default_factory=list)
    middle_notes: list[str] = field(default_factory=list)
    base_notes: list[str] = field(default_factory=list)
    # Free-text journaling. Never a model input — it exists so the log can hold
    # what the user wrote without the feature layer being tempted to parse it.
    comment: str = ""
    perfume_id: int | None = None
    logged_at: str = ""

    def validated(self) -> "Perfume":
        top = clean_notes(self.top_notes, "top")
        middle = clean_notes(self.middle_notes, "middle")
        base = clean_notes(self.base_notes, "base")
        if not (top or middle or base):
            raise ValidationError("a perfume needs at least one note")
        return Perfume(
            brand=self.brand.strip(),
            name=self.name.strip(),
            perfume_type=format_notes(clean_families(self.perfume_type)),
            perfume_medium=self.perfume_medium.strip(),
            perfume_strength=self.perfume_strength.strip(),
            top_notes=top,
            middle_notes=middle,
            base_notes=base,
            comment=self.comment.strip(),
            perfume_id=self.perfume_id,
            logged_at=self.logged_at,
        )

    @property
    def perfume_types(self) -> list[str]:
        return parse_notes(self.perfume_type)

    @property
    def all_notes(self) -> list[str]:
        return self.top_notes + self.middle_notes + self.base_notes

    @property
    def layers(self) -> dict[str, list[str]]:
        return {
            "top": self.top_notes,
            "middle": self.middle_notes,
            "base": self.base_notes,
        }

    @property
    def group_key(self) -> str:
        return group_key(self.brand, self.name)

    @property
    def label(self) -> str:
        return " ".join(p for p in (self.brand, self.name) if p) or f"#{self.perfume_id}"

    def to_row(self) -> dict[str, str]:
        return {
            "perfume_id": str(self.perfume_id),
            "brand": self.brand,
            "name": self.name,
            "perfume_type": self.perfume_type,
            "perfume_medium": self.perfume_medium,
            "perfume_strength": self.perfume_strength,
            "top_notes": format_notes(self.top_notes),
            "middle_notes": format_notes(self.middle_notes),
            "base_notes": format_notes(self.base_notes),
            "comment": self.comment,
            "logged_at": self.logged_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Perfume":
        return cls(
            perfume_id=int(row["perfume_id"]),
            brand=row.get("brand", ""),
            name=row.get("name", ""),
            perfume_type=row.get("perfume_type", ""),
            perfume_medium=row.get("perfume_medium", ""),
            perfume_strength=row.get("perfume_strength", ""),
            top_notes=parse_notes(row.get("top_notes")),
            middle_notes=parse_notes(row.get("middle_notes")),
            base_notes=parse_notes(row.get("base_notes")),
            comment=row.get("comment", ""),
            logged_at=row.get("logged_at", ""),
        )


@dataclass
class Combination:
    """Two perfumes worn together. A layered pair, in composition terms.

    Stored as a reference to its members rather than as a flattened note list. The
    blend is *derived* — mixing two compositions is a function of the two, and
    recomputing it means a lexicon improvement propagates to every pair for free
    instead of leaving stale copies on disk.

    Member ids are stored in ascending order so that A+B and B+A are one record;
    `applied_first` carries the order that actually matters perceptually.
    """

    perfume_id_a: int
    perfume_id_b: int
    applied_first: str = ""
    comment: str = ""
    combination_id: int | None = None
    created_at: str = ""

    def validated(self) -> "Combination":
        a, b = int(self.perfume_id_a), int(self.perfume_id_b)
        if a == b:
            raise ValidationError(f"a combination needs two different perfumes (got {a} twice)")
        applied = (self.applied_first or "").strip().lower()
        if applied not in APPLIED_FIRST_VALUES:
            raise ValidationError(
                f"applied_first must be one of {APPLIED_FIRST_VALUES}, got {applied!r}"
            )
        # Canonical order, with applied_first following the swap so it keeps
        # pointing at the same physical perfume.
        if a > b:
            a, b = b, a
            applied = {"a": "b", "b": "a"}.get(applied, applied)
        return Combination(
            perfume_id_a=a,
            perfume_id_b=b,
            applied_first=applied,
            comment=self.comment.strip(),
            combination_id=self.combination_id,
            created_at=self.created_at,
        )

    @property
    def member_ids(self) -> tuple[int, int]:
        return (self.perfume_id_a, self.perfume_id_b)

    @property
    def first_applied_id(self) -> int | None:
        """The perfume that went on the skin first, if recorded."""
        if self.applied_first == "a":
            return self.perfume_id_a
        if self.applied_first == "b":
            return self.perfume_id_b
        return None

    @property
    def pair_key(self) -> str:
        return f"{self.perfume_id_a}+{self.perfume_id_b}"

    def to_row(self) -> dict[str, str]:
        return {
            "combination_id": str(self.combination_id),
            "perfume_id_a": str(self.perfume_id_a),
            "perfume_id_b": str(self.perfume_id_b),
            "applied_first": self.applied_first,
            "comment": self.comment,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Combination":
        return cls(
            combination_id=int(row["combination_id"]),
            perfume_id_a=int(row["perfume_id_a"]),
            perfume_id_b=int(row["perfume_id_b"]),
            applied_first=row.get("applied_first", ""),
            comment=row.get("comment", ""),
            created_at=row.get("created_at", ""),
        )


@dataclass
class PreferenceDeclaration:
    """Something the user said about their taste, before or alongside rating anything.

    This is a *prior*, not an observation. It is the honest expression of the one
    good idea in the legacy engine — that onboarding-declared taste should seed
    predictions — except that here it sets prior means on family coefficients which
    the likelihood can override, rather than hardcoding a permanent liked/disliked
    set that no amount of contrary evidence could revise.

    Declarations are append-only events, so changing your mind is recorded rather
    than erased. A weight of exactly 0 is meaningful: it retracts a declaration.
    """

    scope: str
    key: str
    weight: float
    preference_id: int | None = None
    declared_at: str = ""

    def validated(self) -> "PreferenceDeclaration":
        scope = (self.scope or "").strip().lower()
        if scope not in PREFERENCE_SCOPES:
            raise ValidationError(
                f"scope must be one of {PREFERENCE_SCOPES}, got {self.scope!r}"
            )
        key = (self.key or "").strip()
        if not key:
            raise ValidationError("a preference needs a key")
        try:
            weight = float(self.weight)
        except (TypeError, ValueError):
            raise ValidationError(f"weight {self.weight!r} is not a number") from None
        if not MIN_WEIGHT <= weight <= MAX_WEIGHT:
            raise ValidationError(
                f"weight {weight} outside {MIN_WEIGHT}–{MAX_WEIGHT}. This is a signed "
                "affinity, not a 1–5 rating."
            )
        # Families and axes come from a closed vocabulary; notes and mediums are
        # open, because the point of declaring a note is often that it's unusual.
        if scope == SCOPE_FAMILY and key.lower() not in FAMILIES:
            raise ValidationError(f"unknown family {key!r}")
        if scope == SCOPE_AXIS and key.lower() not in AXES:
            raise ValidationError(f"unknown axis {key!r}")
        return PreferenceDeclaration(
            scope=scope,
            key=key.lower() if scope in (SCOPE_FAMILY, SCOPE_AXIS) else key,
            weight=round(weight, 3),
            preference_id=self.preference_id,
            declared_at=self.declared_at,
        )

    @property
    def is_retraction(self) -> bool:
        return self.weight == 0.0

    def to_row(self) -> dict[str, str]:
        return {
            "preference_id": str(self.preference_id),
            "scope": self.scope,
            "key": self.key,
            "weight": f"{self.weight:g}",
            "declared_at": self.declared_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "PreferenceDeclaration":
        return cls(
            preference_id=int(row["preference_id"]),
            scope=row.get("scope", ""),
            key=row.get("key", ""),
            weight=float(row["weight"]),
            declared_at=row.get("declared_at", ""),
        )


def _validate_subject(subject_type: str, subject_id: int) -> tuple[str, int]:
    kind = (subject_type or "").strip().lower()
    if kind not in SUBJECT_TYPES:
        raise ValidationError(
            f"subject_type must be one of {SUBJECT_TYPES}, got {subject_type!r}"
        )
    try:
        return kind, int(subject_id)
    except (TypeError, ValueError):
        raise ValidationError(f"subject_id {subject_id!r} is not an id") from None


@dataclass
class RatingEvent:
    """A rating the user gave. The only source of training targets.

    The subject is either a perfume or a combination; both use the same 1–5 scale.
    """

    subject_id: int
    rating: float
    subject_type: str = SUBJECT_PERFUME
    context: str = ""
    rating_id: int | None = None
    rated_at: str = ""

    def validated(self) -> "RatingEvent":
        kind, subject_id = _validate_subject(self.subject_type, self.subject_id)
        try:
            value = float(self.rating)
        except (TypeError, ValueError):
            raise ValidationError(f"rating {self.rating!r} is not a number") from None
        if not MIN_RATING <= value <= MAX_RATING:
            raise ValidationError(
                f"rating {value} outside {MIN_RATING}–{MAX_RATING}"
            )
        if abs(value / RATING_STEP - round(value / RATING_STEP)) > 1e-9:
            raise ValidationError(f"rating {value} is not a multiple of {RATING_STEP}")
        return RatingEvent(
            subject_id=subject_id,
            rating=round(value, 1),
            subject_type=kind,
            context=self.context.strip(),
            rating_id=self.rating_id,
            rated_at=self.rated_at,
        )

    @property
    def is_perfume(self) -> bool:
        return self.subject_type == SUBJECT_PERFUME

    @property
    def perfume_id(self) -> int | None:
        """Convenience for the common case. None when this rates a combination."""
        return self.subject_id if self.is_perfume else None

    @property
    def combination_id(self) -> int | None:
        return self.subject_id if self.subject_type == SUBJECT_COMBINATION else None

    def to_row(self) -> dict[str, str]:
        return {
            "rating_id": str(self.rating_id),
            "subject_type": self.subject_type,
            "subject_id": str(self.subject_id),
            "rating": f"{self.rating:g}",
            "context": self.context,
            "rated_at": self.rated_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "RatingEvent":
        return cls(
            rating_id=int(row["rating_id"]),
            subject_type=row.get("subject_type", SUBJECT_PERFUME),
            subject_id=int(row["subject_id"]),
            rating=float(row["rating"]),
            context=row.get("context", ""),
            rated_at=row.get("rated_at", ""),
        )


@dataclass
class Prediction:
    """A prediction the model made. Never read back as a training target.

    Kept so that predicted-vs-actual can be charted honestly, and so a model
    version can be held accountable for what it claimed.
    """

    subject_id: int
    value: float
    subject_type: str = SUBJECT_PERFUME
    interval_low: float | None = None
    interval_high: float | None = None
    model_version: str = ""
    spec_hash: str = ""
    prediction_id: int | None = None
    predicted_at: str = ""

    def validated(self) -> "Prediction":
        kind, subject_id = _validate_subject(self.subject_type, self.subject_id)
        try:
            value = float(self.value)
        except (TypeError, ValueError):
            raise ValidationError(f"prediction {self.value!r} is not a number") from None
        return Prediction(
            subject_id=subject_id,
            subject_type=kind,
            value=round(value, 3),
            interval_low=None if self.interval_low is None else float(self.interval_low),
            interval_high=(
                None if self.interval_high is None else float(self.interval_high)
            ),
            model_version=self.model_version.strip(),
            spec_hash=self.spec_hash.strip(),
            prediction_id=self.prediction_id,
            predicted_at=self.predicted_at,
        )

    @property
    def perfume_id(self) -> int | None:
        return self.subject_id if self.subject_type == SUBJECT_PERFUME else None

    @property
    def combination_id(self) -> int | None:
        return self.subject_id if self.subject_type == SUBJECT_COMBINATION else None

    def to_row(self) -> dict[str, str]:
        return {
            "prediction_id": str(self.prediction_id),
            "subject_type": self.subject_type,
            "subject_id": str(self.subject_id),
            "value": f"{self.value:g}",
            "interval_low": "" if self.interval_low is None else f"{self.interval_low:g}",
            "interval_high": (
                "" if self.interval_high is None else f"{self.interval_high:g}"
            ),
            "model_version": self.model_version,
            "spec_hash": self.spec_hash,
            "predicted_at": self.predicted_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Prediction":
        def opt(key: str) -> float | None:
            raw = row.get(key, "")
            return float(raw) if raw not in ("", None) else None

        return cls(
            prediction_id=int(row["prediction_id"]),
            subject_type=row.get("subject_type", SUBJECT_PERFUME),
            subject_id=int(row["subject_id"]),
            value=float(row["value"]),
            interval_low=opt("interval_low"),
            interval_high=opt("interval_high"),
            model_version=row.get("model_version", ""),
            spec_hash=row.get("spec_hash", ""),
            predicted_at=row.get("predicted_at", ""),
        )
