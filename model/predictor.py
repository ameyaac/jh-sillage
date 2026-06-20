import json, os, random

PROFILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "taste_profile.json"
)

_BUILTIN_LIKED = {
    "bergamot",
    "lemon",
    "grapefruit",
    "yuzu",
    "neroli",
    "petitgrain",
    "rose",
    "peony",
    "iris",
    "violet",
    "lavender",
    "jasmine",
    "lily of the valley",
    "sandalwood",
    "cedarwood",
    "vetiver",
    "patchouli",
    "oud",
    "agarwood",
    "amber",
    "musk",
    "white musk",
    "clean musk",
    "woody musk",
    "vanilla",
    "tonka bean",
    "benzoin",
    "fig",
    "pomegranate",
    "black pepper",
    "cardamom",
    "pink pepper",
    "saffron",
    "sea salt",
    "driftwood",
    "amberwood",
    "ambergris",
}
_BUILTIN_DISLIKED = {
    "civet",
    "castoreum",
    "animalic",
    "indole",
    "butyric",
    "cumin",
    "fecal",
    "sweaty",
    "barnyard",
}
_BUILTIN_NEUTRAL = {
    "lily",
    "magnolia",
    "tuberose",
    "gardenia",
    "ylang ylang",
    "incense",
    "smoke",
    "leather",
    "tobacco",
    "oakmoss",
    "labdanum",
    "mint",
    "eucalyptus",
    "clary sage",
    "pine",
}

SCENT_FAMILY_WEIGHTS = {
    "Floral": 0.6,
    "Woody": 0.55,
    "Oriental": 0.45,
    "Fresh": 0.7,
    "Citrus": 0.75,
    "Aquatic": 0.35,
    "Gourmand": 0.25,
    "Spicy": 0.35,
    "Fougere": 0.6,
    "Chypre": 0.4,
    "Aromatic": 0.5,
    "Musk": 0.5,
}
MEDIUM_MULTIPLIERS = {
    "Spray Perfume (EDP)": 1.0,
    "Spray Perfume (EDT)": 0.9,
    "Spray Perfume (EDC)": 0.75,
    "Roll-on": 0.8,
    "Body Mist": 0.65,
    "Perfume Oil": 1.1,
    "Wax Perfume (Solid)": 0.7,
    "Attar": 1.15,
}
STRENGTH_WEIGHTS = {
    "Sillage (Intense)": 1.0,
    "Heavy": 0.9,
    "Medium": 0.75,
    "Light": 0.6,
    "Subtle / Skin Scent": 0.5,
}


def load_profile():
    path = os.path.normpath(PROFILE_PATH)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_profile(data):
    path = os.path.normpath(PROFILE_PATH)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _profile_note_sets(profile):
    liked = set(_BUILTIN_LIKED)
    disliked = set(_BUILTIN_DISLIKED)
    for n in profile.get("loved_notes", []):
        liked.add(n.lower())
        disliked.discard(n.lower())
    for n in profile.get("disliked_notes", []):
        disliked.add(n.lower())
        liked.discard(n.lower())
    fam_weights = dict(SCENT_FAMILY_WEIGHTS)
    for fam in profile.get("loved_families", []):
        if fam in fam_weights:
            fam_weights[fam] = min(1.0, fam_weights[fam] + 0.2)
    for fam in profile.get("disliked_families", []):
        if fam in fam_weights:
            fam_weights[fam] = max(0.0, fam_weights[fam] - 0.25)
    return liked, disliked, fam_weights


def _score_note(note, liked, disliked):
    n = note.lower().strip()
    if n in disliked:
        return -1.0
    if n in liked:
        return 1.0
    if n in _BUILTIN_NEUTRAL:
        return 0.3
    return 0.1


def compute_learned_prefs(log_entries):
    liked, disliked = set(), set()
    for e in log_entries:
        score = e.get("actual_rating") or e.get("predicted_rating", 0)
        try:
            score = float(score)
        except (ValueError, TypeError):
            continue
        notes = (
            [n.strip() for n in e.get("top_notes", "").split(";") if n.strip()]
            + [n.strip() for n in e.get("middle_notes", "").split(";") if n.strip()]
            + [n.strip() for n in e.get("base_notes", "").split(";") if n.strip()]
        )
        for note in notes:
            if score >= 4.0:
                liked.add(note.lower())
            elif score <= 2.0:
                disliked.add(note.lower())
    return liked, disliked


def predict(
    top_notes,
    mid_notes,
    base_notes,
    scent_family="",
    scent_medium="",
    scent_strength="",
    log_entries=None,
):
    if not top_notes and not mid_notes and not base_notes:
        return None

    profile = load_profile()
    prof_liked, prof_disliked, fam_weights = _profile_note_sets(profile)
    log_liked, log_disliked = compute_learned_prefs(log_entries or [])

    liked = prof_liked | log_liked
    disliked = prof_disliked | log_disliked
    liked -= disliked

    def avg(notes, weight):
        if not notes:
            return None
        return sum(_score_note(n, liked, disliked) for n in notes) / len(notes) * weight

    top_s = avg(top_notes, 1.0)
    mid_s = avg(mid_notes, 1.3)
    base_s = avg(base_notes, 1.2)

    scores = [s for s in [top_s, mid_s, base_s] if s is not None]
    combined = sum(scores) / len(scores)

    family_bonus = fam_weights.get(scent_family, 0.3) * 0.35
    medium_mult = MEDIUM_MULTIPLIERS.get(scent_medium, 1.0)
    strength_w = STRENGTH_WEIGHTS.get(scent_strength, 0.75)

    if scent_medium in profile.get("preferred_mediums", []):
        medium_mult = min(medium_mult + 0.1, 1.25)

    raw = (combined + family_bonus) * medium_mult * strength_w
    normalized = (raw + 1.2) / 3.0
    score = round(max(1.0, min(5.0, 1.0 + normalized * 4.0)), 1)

    breakdown = {}
    if top_s is not None:
        breakdown["Top Notes"] = round(
            max(1, min(5, (top_s / 1.0 + 1.2) / 3.0 * 4 + 1)), 1
        )
    if mid_s is not None:
        breakdown["Heart Notes"] = round(
            max(1, min(5, (mid_s / 1.3 + 1.2) / 3.0 * 4 + 1)), 1
        )
    if base_s is not None:
        breakdown["Base Notes"] = round(
            max(1, min(5, (base_s / 1.2 + 1.2) / 3.0 * 4 + 1)), 1
        )
    if scent_medium:
        breakdown["Medium Fit"] = round(medium_mult * 3.5, 1)
    if scent_strength:
        breakdown["Strength Fit"] = round(strength_w * 4.5, 1)

    return {"total": score, "breakdown": breakdown}


VERDICTS = {
    5: [
        "This one's special - bottle it.",
        "A masterpiece for your collection.",
        "You'll reach for this every single day.",
    ],
    4: [
        "Strong contender for your shelf.",
        "Very likely to love this one.",
        "Definitely worth a full bottle.",
    ],
    3: [
        "Comfortable middle ground - sample first.",
        "Might like it, might not.",
        "Inoffensive but not exciting.",
    ],
    2: [
        "Proceed with caution - sample first.",
        "Probably not your thing.",
        "A few notes here might clash.",
    ],
    1: [
        "This profile looks like a miss.",
        "Very likely won't suit your taste.",
        "Skip it.",
    ],
}


def get_verdict(score):
    return random.choice(VERDICTS[max(1, min(5, round(score)))])
