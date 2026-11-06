"""Tests for the storage layer.

Runs standalone (`python tests/test_data.py`) or under pytest if it's installed.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import (  # noqa: E402
    SUBJECT_COMBINATION,
    Combination,
    Perfume,
    Prediction,
    RatingLog,
    ValidationError,
    group_key,
)
from data.migrate import LEGACY_MODEL_VERSION, migrate  # noqa: E402
from data.schema import PREDICTION_FIELDS, SCHEMA_VERSION  # noqa: E402
from data.migrate import profile_to_declarations  # noqa: E402
from data.store import SchemaVersionError  # noqa: E402


def _log(tmp: str) -> RatingLog:
    return RatingLog(tmp)


def _perfume(**kw) -> Perfume:
    base = dict(
        brand="Dior",
        name="Sauvage",
        perfume_type="Woody",
        perfume_medium="Spray Perfume (EDT)",
        top_notes=["bergamot", "pink pepper"],
        middle_notes=["lavender"],
        base_notes=["ambroxan", "vetiver"],
    )
    base.update(kw)
    return Perfume(**base)


# ── round trip ────────────────────────────────────────────────────────────────


def test_round_trip_preserves_composition():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        saved = log.log_perfume(_perfume())
        assert saved.perfume_id == 1
        assert saved.logged_at, "logged_at should be stamped on write"

        loaded = log.perfumes()[0]
        assert loaded.top_notes == ["bergamot", "pink pepper"]
        assert loaded.base_notes == ["ambroxan", "vetiver"]
        assert loaded.perfume_medium == "Spray Perfume (EDT)"
        assert loaded.layers["middle"] == ["lavender"]


def test_note_casing_is_preserved_and_duplicates_collapse():
    # Normalization belongs to the feature layer; storage keeps what was typed.
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        log.log_perfume(_perfume(top_notes=["Black Pepper", "black pepper", " Yuzu "]))
        assert log.perfumes()[0].top_notes == ["Black Pepper", "Yuzu"]


def test_multiple_scent_families_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        # A list or a ";"-joined string both work; blanks and repeats drop out.
        log.log_perfume(_perfume(perfume_type=["Woody", "Spicy", " woody ", ""]))
        log.log_perfume(_perfume(name="Other", perfume_type="Citrus; Fresh"))
        log.log_perfume(_perfume(name="Single", perfume_type="Floral"))
        log.log_perfume(_perfume(name="None", perfume_type=""))

        first, second, single, none = RatingLog(tmp).perfumes()
        assert first.perfume_types == ["Woody", "Spicy"]
        assert first.perfume_type == "Woody;Spicy"
        assert second.perfume_types == ["Citrus", "Fresh"]
        # A one-family log written before multi-family support reads back unchanged.
        assert single.perfume_type == "Floral"
        assert single.perfume_types == ["Floral"]
        assert none.perfume_types == []


def test_meta_written_and_version_enforced():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        meta = json.loads(log.meta_path.read_text())
        assert meta["schema_version"] == SCHEMA_VERSION

        log.meta_path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1}))
        try:
            RatingLog(tmp)
        except SchemaVersionError:
            pass
        else:
            raise AssertionError("a future schema version must be refused, not parsed")


# ── ids ───────────────────────────────────────────────────────────────────────


def test_ids_are_max_plus_one_not_length_plus_one():
    # The legacy store used len+1, which reissues an id after any removal and then
    # attaches new ratings to the wrong perfume.
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        for i in range(3):
            log.log_perfume(_perfume(name=f"P{i}"))

        rows = list(csv.DictReader(log.perfumes_path.open()))
        kept = [r for r in rows if r["perfume_id"] != "2"]  # simulate a removal
        with log.perfumes_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(kept)

        assert log.log_perfume(_perfume(name="P3")).perfume_id == 4


# ── ratings vs predictions ────────────────────────────────────────────────────


def test_training_set_ignores_predictions_entirely():
    # The core separation: a logged prediction must never become a target.
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        rated = log.log_perfume(_perfume(name="Rated"))
        unrated = log.log_perfume(_perfume(name="Unrated"))

        log.log_rating(rated.perfume_id, 4.5)
        log.log_prediction(Prediction(subject_id=unrated.perfume_id, value=4.9))
        log.log_prediction(Prediction(subject_id=rated.perfume_id, value=2.0))

        training = log.training_set()
        assert [(p.name, r) for p, r in training] == [("Rated", 4.5)]
        assert [p.name for p in log.unrated()] == ["Unrated"]
        assert len(log.predictions()) == 2


def test_prediction_interval_and_provenance_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        p = log.log_perfume(_perfume())
        log.log_prediction(
            Prediction(
                subject_id=p.perfume_id,
                value=4.13,
                interval_low=3.7,
                interval_high=4.6,
                model_version="ridge-v1",
                spec_hash="abc123",
            )
        )
        got = log.predictions()[0]
        assert (got.value, got.interval_low, got.interval_high) == (4.13, 3.7, 4.6)
        assert got.model_version == "ridge-v1" and got.spec_hash == "abc123"
        assert got.predicted_at


def test_missing_interval_stays_none_not_zero():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        p = log.log_perfume(_perfume())
        log.log_prediction(Prediction(subject_id=p.perfume_id, value=3.0))
        got = log.predictions()[0]
        assert got.interval_low is None and got.interval_high is None


# ── rating events ─────────────────────────────────────────────────────────────


def test_rerating_appends_and_latest_wins():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        p = log.log_perfume(_perfume())
        log.log_rating(p.perfume_id, 3.0, rated_at="2026-01-01T10:00:00")
        log.log_rating(p.perfume_id, 4.5, rated_at="2026-06-01T10:00:00")

        assert len(log.rating_events()) == 2, "history must survive a re-rating"
        assert log.current_ratings()[p.perfume_id].rating == 4.5
        assert log.training_set() == [(log.perfumes()[0], 4.5)]


def test_training_set_is_chronological():
    # Prefix evaluation depends on this order to detect taste drift.
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a = log.log_perfume(_perfume(name="A"))
        b = log.log_perfume(_perfume(name="B"))
        log.log_rating(b.perfume_id, 2.0, rated_at="2026-01-01T00:00:00")
        log.log_rating(a.perfume_id, 5.0, rated_at="2026-02-01T00:00:00")
        assert [p.name for p, _ in log.training_set()] == ["B", "A"]


def test_rating_for_unknown_perfume_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        try:
            log.log_rating(99, 4.0)
        except ValidationError:
            pass
        else:
            raise AssertionError("orphan ratings must be refused at write time")


# ── validation ────────────────────────────────────────────────────────────────


def test_invalid_records_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        p = log.log_perfume(_perfume())

        cases = [
            ("no notes at all", lambda: log.log_perfume(_perfume(
                top_notes=[], middle_notes=[], base_notes=[]))),
            ("separator inside a note", lambda: log.log_perfume(_perfume(
                top_notes=["bergamot; lemon"]))),
            ("rating above range", lambda: log.log_rating(p.perfume_id, 6.0)),
            ("rating below range", lambda: log.log_rating(p.perfume_id, 0.0)),
            ("off-step rating", lambda: log.log_rating(p.perfume_id, 4.3)),
            ("non-numeric rating", lambda: log.log_rating(p.perfume_id, "great")),
        ]
        for label, call in cases:
            try:
                call()
            except ValidationError:
                continue
            raise AssertionError(f"{label} should have been refused")

        assert len(log.perfumes()) == 1 and log.rating_events() == []


def test_half_star_ratings_are_accepted():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        p = log.log_perfume(_perfume())
        assert log.log_rating(p.perfume_id, 4.5).rating == 4.5


# ── grouping ──────────────────────────────────────────────────────────────────


def test_group_key_merges_concentrations_but_not_flankers():
    assert group_key("Chanel", "Bleu de Chanel EDT") == group_key(
        "Chanel", "Bleu de Chanel EDP"
    )
    assert group_key("Chanel", "Bleu de Chanel Parfum") == group_key(
        "Chanel", "bleu  de   chanel!"
    )
    # "Intense" is usually a different juice, so it must stay its own group.
    assert group_key("Dior", "Sauvage") != group_key("Dior", "Sauvage Intense")
    assert group_key("Dior", "Sauvage") != group_key("Guerlain", "Sauvage")


def test_groups_fall_back_to_own_id_when_unnamed():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        p = log.log_perfume(_perfume(brand="", name=""))
        assert log.groups()[p.perfume_id] == f"id:{p.perfume_id}"


def test_summary_counts_groups_not_rows():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        edt = log.log_perfume(_perfume(name="Sauvage EDT"))
        edp = log.log_perfume(_perfume(name="Sauvage EDP"))
        log.log_rating(edt.perfume_id, 4.0)
        log.log_rating(edp.perfume_id, 4.5)
        summary = log.summary()
        assert summary["rated"] == 2
        assert summary["distinct_groups"] == 1, "two concentrations are one group"
        assert summary["mean_rating"] == 4.25


# ── combinations (layered pairs) ──────────────────────────────────────────────


def _pair(log: RatingLog) -> tuple[Perfume, Perfume]:
    return (
        log.log_perfume(_perfume(name="Sauvage")),
        log.log_perfume(_perfume(name="Bleu", brand="Chanel")),
    )


def test_combination_round_trip_and_order():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        combo = log.log_combination(a.perfume_id, b.perfume_id, applied_first="b")
        assert combo.combination_id == 1
        assert combo.member_ids == (a.perfume_id, b.perfume_id)
        assert combo.first_applied_id == b.perfume_id

        loaded = log.combinations()[0]
        assert loaded.member_ids == combo.member_ids
        assert loaded.first_applied_id == b.perfume_id
        assert loaded.created_at


def test_combination_members_are_canonically_ordered():
    # A+B and B+A are one pair; applied_first follows the swap so it still points
    # at the same physical perfume.
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        combo = log.log_combination(b.perfume_id, a.perfume_id, applied_first="a")
        assert combo.member_ids == (a.perfume_id, b.perfume_id)
        assert combo.first_applied_id == b.perfume_id, "order must survive the swap"


def test_relogging_a_pair_reuses_its_id():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        first = log.log_combination(a.perfume_id, b.perfume_id, applied_first="b")
        again = log.log_combination(b.perfume_id, a.perfume_id, applied_first="a")
        assert again.combination_id == first.combination_id
        assert len(log.combinations()) == 1, "ratings for a pair must share one id"


def test_invalid_combinations_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        cases = [
            ("a perfume layered with itself", lambda: log.log_combination(
                a.perfume_id, a.perfume_id)),
            ("unknown member", lambda: log.log_combination(a.perfume_id, 99)),
            ("bad applied_first", lambda: log.log_combination(
                a.perfume_id, b.perfume_id, applied_first="first")),
        ]
        for label, call in cases:
            try:
                call()
            except ValidationError:
                continue
            raise AssertionError(f"{label} should have been refused")
        assert log.combinations() == []


def test_pair_ratings_stay_out_of_the_single_perfume_training_set():
    # Layered pairs are a different target with a different model. Mixing them into
    # training_set() would contaminate the composition-level fit.
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        combo = log.log_combination(a.perfume_id, b.perfume_id, applied_first="b")
        log.log_rating(a.perfume_id, 4.0)
        log.log_rating(rating=2.5, combination_id=combo.combination_id)

        assert [(p.name, r) for p, r in log.training_set()] == [("Sauvage", 4.0)]

        pairs = log.pair_training_set()
        assert len(pairs) == 1
        got_combo, got_a, got_b, rating = pairs[0]
        assert rating == 2.5
        assert (got_a.name, got_b.name) == ("Sauvage", "Bleu")
        assert got_combo.combination_id == combo.combination_id


def test_rerating_a_pair_takes_the_latest():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        combo = log.log_combination(a.perfume_id, b.perfume_id)
        log.log_rating(rating=2.0, combination_id=combo.combination_id,
                       rated_at="2026-01-01T00:00:00")
        log.log_rating(rating=4.5, combination_id=combo.combination_id,
                       rated_at="2026-05-01T00:00:00")
        assert [r for *_, r in log.pair_training_set()] == [4.5]
        assert len(log.rating_events(SUBJECT_COMBINATION)) == 2


def test_rating_requires_exactly_one_subject():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        combo = log.log_combination(a.perfume_id, b.perfume_id)
        for label, call in [
            ("neither subject", lambda: log.log_rating(rating=4.0)),
            ("both subjects", lambda: log.log_rating(
                a.perfume_id, 4.0, combination_id=combo.combination_id)),
            ("no rating", lambda: log.log_rating(a.perfume_id)),
            ("unknown combination", lambda: log.log_rating(
                rating=4.0, combination_id=99)),
        ]:
            try:
                call()
            except ValidationError:
                continue
            raise AssertionError(f"{label} should have been refused")


def test_combination_groups_merge_member_concentrations():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a_edt = log.log_perfume(_perfume(name="Sauvage EDT"))
        a_edp = log.log_perfume(_perfume(name="Sauvage EDP"))
        b_edt = log.log_perfume(_perfume(brand="Chanel", name="Bleu EDT"))
        b_edp = log.log_perfume(_perfume(brand="Chanel", name="Bleu EDP"))

        one = log.log_combination(a_edt.perfume_id, b_edp.perfume_id)
        two = log.log_combination(a_edp.perfume_id, b_edt.perfume_id)
        groups = log.combination_groups()
        assert groups[one.combination_id] == groups[two.combination_id]


def test_combination_predictions_are_kept_apart_from_perfume_ones():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        combo = log.log_combination(a.perfume_id, b.perfume_id)
        log.log_prediction(Prediction(subject_id=a.perfume_id, value=4.0))
        log.log_prediction(
            Prediction(
                subject_id=combo.combination_id,
                subject_type=SUBJECT_COMBINATION,
                value=3.2,
            )
        )
        assert [p.value for p in log.predictions("perfume")] == [4.0]
        pair_predictions = log.predictions(SUBJECT_COMBINATION)
        assert [p.value for p in pair_predictions] == [3.2]
        assert pair_predictions[0].combination_id == combo.combination_id
        assert pair_predictions[0].perfume_id is None


def test_unknown_subject_type_is_refused():
    try:
        Combination(perfume_id_a=1, perfume_id_b=2).validated()
    except ValidationError:
        raise AssertionError("a valid combination must not be refused") from None
    try:
        Prediction(subject_id=1, value=3.0, subject_type="mixture").validated()
    except ValidationError:
        pass
    else:
        raise AssertionError("an unknown subject_type must be refused")


def test_summary_reports_pairs():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        a, b = _pair(log)
        combo = log.log_combination(a.perfume_id, b.perfume_id)
        log.log_rating(a.perfume_id, 4.0)
        log.log_rating(rating=3.0, combination_id=combo.combination_id)
        summary = log.summary()
        assert summary["rated"] == 1
        assert summary["combinations"] == 1
        assert summary["rated_combinations"] == 1
        assert summary["mean_rating"] == 4.0, "pair ratings must not move this"


# ── declared preferences ──────────────────────────────────────────────────────


def test_palette_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        log.log_preference("family", "citrus", 1.0)
        log.log_preference("family", "animalic", -1.0)
        log.log_preference("note", "Vetiver", 0.5)
        log.log_preference("medium", "Spray Perfume (EDP)", 1.0)
        log.log_preference("axis", "powdery", -0.5)

        palette = log.palette()
        assert palette["family"] == {"citrus": 1.0, "animalic": -1.0}
        assert palette["note"] == {"Vetiver": 0.5}
        assert palette["medium"] == {"Spray Perfume (EDP)": 1.0}
        assert palette["axis"] == {"powdery": -0.5}
        assert log.has_palette()


def test_declared_preferences_never_reach_the_training_set():
    # A declaration is a prior, not an observation. This is the same separation as
    # predictions, for the same reason.
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        p = log.log_perfume(_perfume())
        log.log_preference("family", "citrus", 1.0)
        log.log_preference("note", "bergamot", 1.0)
        assert log.training_set() == []

        log.log_rating(p.perfume_id, 4.0)
        assert [r for _, r in log.training_set()] == [4.0]
        assert log.summary()["mean_rating"] == 4.0, "declarations must not move this"


def test_redeclaring_appends_and_latest_wins():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        log.log_preference("family", "woody", 0.5, declared_at="2026-01-01T00:00:00")
        log.log_preference("family", "woody", -1.0, declared_at="2026-06-01T00:00:00")
        assert log.palette()["family"] == {"woody": -1.0}
        assert len(log.preference_events()) == 2, "history must survive"


def test_retraction_removes_from_the_palette_but_stays_on_disk():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        log.log_preference("family", "gourmand", 1.0, declared_at="2026-01-01T00:00:00")
        log.log_preference("family", "gourmand", 0.0, declared_at="2026-02-01T00:00:00")

        assert log.palette() == {}, "a retracted palette must look like no palette"
        assert not log.has_palette()
        assert log.palette(include_retracted=True)["family"] == {"gourmand": 0.0}
        assert len(log.preference_events()) == 2


def test_invalid_preferences_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        cases = [
            ("unknown scope", lambda: log.log_preference("vibe", "citrus", 1.0)),
            ("unknown family", lambda: log.log_preference("family", "petrichor", 1.0)),
            ("unknown axis", lambda: log.log_preference("axis", "loud", 1.0)),
            ("empty key", lambda: log.log_preference("note", "   ", 1.0)),
            ("weight above range", lambda: log.log_preference("family", "citrus", 2.0)),
            # 4.5 would be a plausible *rating*, which is exactly the confusion the
            # signed scale exists to prevent.
            ("rating-shaped weight", lambda: log.log_preference("family", "citrus", 4.5)),
            ("non-numeric weight", lambda: log.log_preference("family", "citrus", "lots")),
        ]
        for label, call in cases:
            try:
                call()
            except ValidationError:
                continue
            raise AssertionError(f"{label} should have been refused")
        assert log.preference_events() == []


def test_family_and_axis_keys_are_normalised():
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        log.log_preference("FAMILY", "Citrus", 1.0)
        assert log.palette()["family"] == {"citrus": 1.0}
        # Note keys keep their casing, since notes are user vocabulary.
        log.log_preference("note", "Ambroxan", 1.0)
        assert log.palette()["note"] == {"Ambroxan": 1.0}


def test_a_v1_log_is_upgraded_in_place():
    # v1 -> v2 added preferences.csv, which is purely additive: an absent table reads
    # as an empty one, so refusing to open would be over-strict. (It lands on the
    # current version, v3, in one step.)
    with tempfile.TemporaryDirectory() as tmp:
        log = _log(tmp)
        log.log_perfume(_perfume())
        log.meta_path.write_text(json.dumps({"schema_version": 1}))

        reopened = RatingLog(tmp)
        assert len(reopened.perfumes()) == 1
        assert reopened.palette() == {}
        meta = json.loads(reopened.meta_path.read_text())
        assert meta["schema_version"] == SCHEMA_VERSION
        assert meta["upgrades"][0]["from"] == 1


def test_v2_and_v3_logs_are_upgraded_to_the_current_version_in_place():
    # v2 -> v3 lets perfume_type hold several families; a v2 single family is
    # already a valid one-element list. v3 -> v4 changed no format. Either way the
    # upgrade only rewrites the version.
    assert SCHEMA_VERSION == 6
    for old in (2, 3, 4, 5):
        with tempfile.TemporaryDirectory() as tmp:
            log = _log(tmp)
            log.log_perfume(_perfume(perfume_type="Woody;Spicy" if old >= 3 else "Woody"))
            log.meta_path.write_text(json.dumps({"schema_version": old}))

            reopened = RatingLog(tmp)
            assert reopened.perfumes()[0].perfume_types[0] == "Woody"
            meta = json.loads(reopened.meta_path.read_text())
            assert meta["schema_version"] == SCHEMA_VERSION
            assert meta["upgrades"][0] == {**meta["upgrades"][0], "from": old, "to": SCHEMA_VERSION}


# ── legacy taste profile ──────────────────────────────────────────────────────


def test_legacy_profile_becomes_declarations():
    declarations = profile_to_declarations({
        "loved_families": ["Woody", "citrus"],
        "disliked_families": ["Animalic"],
        "liked_notes": ["Vetiver", "Ambroxan"],
        "disliked_notes": ["Cumin"],
        "preferred_mediums": ["Spray Perfume (EDP)"],
    })
    found = {(d.scope, d.key): d.weight for d in declarations}
    assert found[("family", "woody")] == 1.0
    assert found[("family", "citrus")] == 1.0
    assert found[("family", "animalic")] == -1.0
    assert found[("note", "Vetiver")] == 1.0
    assert found[("note", "Cumin")] == -1.0
    assert found[("medium", "Spray Perfume (EDP)")] == 1.0


def test_legacy_profile_slider_scores_are_rescaled():
    # Legacy sliders were 1–5; 3 is neutral on the signed scale.
    declarations = profile_to_declarations({
        "family_scores": {"citrus": 5, "woody": 3, "animalic": 1, "nonsense": 5}
    })
    found = {d.key: d.weight for d in declarations}
    assert found["citrus"] == 1.0
    assert found["woody"] == 0.0
    assert found["animalic"] == -1.0
    assert "nonsense" not in found, "unknown families are dropped, not guessed at"


def test_legacy_profile_ignores_unknown_families():
    declarations = profile_to_declarations({"loved_families": ["Petrichor", "Woody"]})
    assert [d.key for d in declarations] == ["woody"]


# ── migration ─────────────────────────────────────────────────────────────────

LEGACY_HEADER = [
    "id", "brand_name", "scent_name", "scent_medium", "scent_family",
    "scent_strength", "top_notes", "middle_notes", "base_notes",
    "predicted_rating", "actual_rating", "notes", "created_at",
]

LEGACY_ROWS = [
    # rated: contributes a target and a prediction
    ["1", "Dior", "Sauvage", "Spray Perfume (EDT)", "Woody", "Strong",
     "bergamot;pink pepper", "lavender", "ambroxan", "4.2", "4.5",
     "loved it in summer", "2026-06-19 05:27"],
    # unrated: prediction only — this is the row the old engine trained on
    ["2", "Chanel", "Bleu", "Spray Perfume (EDP)", "Aromatic", "Moderate",
     "grapefruit", "ginger", "sandalwood", "3.8", "", "", "2026-06-20 09:10"],
    # unsalvageable: no notes
    ["3", "Nobody", "Nothing", "", "", "", "", "", "", "", "3.0", "", ""],
]


def _legacy_csv(tmp: Path) -> Path:
    path = tmp / "sillage_log.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(LEGACY_HEADER)
        writer.writerows(LEGACY_ROWS)
    return path


def test_migration_splits_predictions_from_ratings():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source = _legacy_csv(tmp_path)
        target = tmp_path / "userdata"

        report = migrate(source=source, data_dir=target, apply=True)
        assert (report.perfumes, report.ratings, report.predictions) == (2, 1, 2)
        assert len(report.skipped) == 1 and "line 4" in report.skipped[0]

        log = RatingLog(target)
        # Only the genuinely rated perfume becomes a training example.
        assert [(p.name, r) for p, r in log.training_set()] == [("Sauvage", 4.5)]
        # Both legacy predictions are preserved, and marked as legacy output.
        assert {p.value for p in log.predictions()} == {4.2, 3.8}
        assert {p.model_version for p in log.predictions()} == {LEGACY_MODEL_VERSION}

        sauvage = log.perfumes_by_id()[1]
        assert sauvage.perfume_type == "Woody"          # was scent_family
        assert sauvage.perfume_medium == "Spray Perfume (EDT)"
        assert sauvage.perfume_strength == "Strong"     # legacy field, not dropped
        assert sauvage.comment == "loved it in summer"  # free text, not dropped
        assert sauvage.logged_at == "2026-06-19T05:27:00"


def test_migration_refuses_to_run_twice():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source = _legacy_csv(tmp_path)
        target = tmp_path / "userdata"
        migrate(source=source, data_dir=target, apply=True)
        try:
            migrate(source=source, data_dir=target, apply=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("re-migrating would duplicate every row")


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source = _legacy_csv(tmp_path)
        target = tmp_path / "userdata"
        report = migrate(source=source, data_dir=target, apply=False)
        assert report.perfumes == 2
        assert not (target / "perfumes.csv").exists()


def test_explicit_missing_source_raises_but_absent_legacy_log_does_not():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            migrate(source=tmp_path / "nope.csv", data_dir=tmp_path / "u")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("a mistyped source path must not look like success")

        # Auto-detect finding nothing is the normal first-run case, not an error.
        import data.migrate as mig

        original = mig.LEGACY_CANDIDATES
        mig.LEGACY_CANDIDATES = (tmp_path / "absent.csv",)
        try:
            report = migrate(data_dir=tmp_path / "u")
        finally:
            mig.LEGACY_CANDIDATES = original
        assert report.source is None and report.perfumes == 0


def test_blank_prediction_columns_never_become_zero():
    # A prediction row whose value is empty must be skipped, not read as 0.0.
    with tempfile.TemporaryDirectory() as tmp:
        log = RatingLog(tmp)
        p = log.log_perfume(_perfume())
        log._append_row(
            log.predictions_path,
            PREDICTION_FIELDS,
            {"prediction_id": "1", "subject_type": "perfume",
             "subject_id": str(p.perfume_id), "value": "3.0",
             "interval_low": "", "interval_high": "", "model_version": "",
             "spec_hash": "", "predicted_at": ""},
        )
        assert log.predictions()[0].interval_low is None


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
