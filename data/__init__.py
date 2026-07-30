"""Storage layer: the append-only rating log and its schema.

    from data import RatingLog, Perfume

    log = RatingLog()
    p = log.log_perfume(Perfume(brand="Dior", name="Sauvage",
                                top_notes=["bergamot"], base_notes=["ambroxan"]))
    log.log_rating(p.perfume_id, 4.5)
    log.training_set()   # [(Perfume, 4.5)]

Layered pairs live in the same log, by reference to their members:

    combo = log.log_combination(a.perfume_id, b.perfume_id, applied_first="b")
    log.log_rating(rating=4.0, combination_id=combo.combination_id)
    log.pair_training_set()   # [(Combination, Perfume, Perfume, 4.0)]

Declared taste — optional, and a prior rather than evidence:

    log.log_preference("family", "citrus", +1.0)
    log.log_preference("family", "animalic", -1.0)
    log.palette()             # {'family': {'citrus': 1.0, 'animalic': -1.0}}
"""

from .schema import (
    AXES,
    FAMILIES,
    PREFERENCE_LEVELS,
    PREFERENCE_SCOPES,
    SCHEMA_VERSION,
    SUBJECT_COMBINATION,
    SUBJECT_PERFUME,
    Combination,
    Perfume,
    Prediction,
    PreferenceDeclaration,
    RatingEvent,
    ValidationError,
    group_key,
)
from .store import RatingLog, SchemaVersionError, resolve_data_dir

__all__ = [
    "AXES",
    "FAMILIES",
    "PREFERENCE_LEVELS",
    "PREFERENCE_SCOPES",
    "SCHEMA_VERSION",
    "SUBJECT_COMBINATION",
    "SUBJECT_PERFUME",
    "Combination",
    "Perfume",
    "Prediction",
    "PreferenceDeclaration",
    "RatingEvent",
    "RatingLog",
    "SchemaVersionError",
    "ValidationError",
    "group_key",
    "resolve_data_dir",
]
