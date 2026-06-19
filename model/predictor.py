import json, os, random

SCENT_PROFILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "scent-profile.json"
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


def load_scent_profile():
    path = os.path.normpath(SCENT_PROFILE_PATH)
    if os.path.exists(path):
        with open(path, "r") as file:
            return json.load(file)
    return {}

def save_scent_profile(data):
    path = os.path.normpath(SCENT_PROFILE_PATH)
    with open(path, "w") as file: 
        json.dump(data, file, indent=4)

def _profile_note_sets(profile):
    liked = set(_BUILTIN_LIKED)
    disliked = set(_BUILTIN_DISLIKED)
    # Notes
    for note in profile.get("liked_notes", []):
        liked.add(note.lower()); disliked.discard(note.lower())
    for note in profile.get("disliked_notes", []):
        disliked.add(note.lower()); liked.discard(note.lower())
    # Families
    scent_family_weights = dict(SCENT_FAMILY_WEIGHTS)
    for family in profile.get("liked_families", []):
        if family in scent_family_weights:
            scent_family_weights[family] = min(1.0, scent_family_weights[family] + 0.2)
    for family in profile.get("disliked_families", []):
        if family in scent_family_weights:
            scent_family_weights[family] = max(0.0, scent_family_weights[family] - 0.25)

def _note_score(note, liked, disliked):
    note = note.lower().strip()
    if note in liked: return 1.0
    if note in disliked: return -1.0
    if note in _BUILTIN_NEUTRAL: return 0.3
    return 0.1

def compute_learned_preference(log_entries):
    liked, disliked = set(), set()
    for entry in log_entries:
        score = entry.get("actual_rating") or entry.get("predicted_rating", 0)
        try:
            score = float(score)
        except (ValueError, TypeError):
            continue
        notes = (
            [
                note.strip() for note in entry.get("top_notes", "").split(";") if note.strip()
            ] + [
                note.strip() for note in entry.get("middle_notes", "").split(";") if note.strip()
            ] + [
                note.strip() for note in entry.get("base_notes", "").split(";") if note.strip()
            ]
        )
        for note in notes:
            if score >= 4.0:
                liked.add(note.lower())
                # disliked.discard(note.lower())
            elif score <= 2.0:
                disliked.add(note.lower())
                # liked.discard(note.lower())
    return liked, disliked

def predict(top_notes, middle_notes, base_notes, scent_family="", scent_medium="", scent_strength="", log_entries=None):
    if not top_notes and not middle_notes and not base_notes:
        return None  # No notes to evaluate
    profile = load_scent_profile()
    profile_liked, profile_disliked, family_weights = _profile_note_sets(profile)
    logged_liked, logged_disliked = compute_learned_preference(log_entries or [])

    liked = profile_liked | logged_liked
    disliked = profile_disliked | logged_disliked
    liked -= disliked  # Remove any overlaps

    def average_note_score(notes, weight):
        if not notes: return None
        return sum(_note_score(note, liked, disliked) for note in notes) / len(notes) * weight
    
    top_note_score = average_note_score(top_notes, 1.0)
    middle_note_score = average_note_score(middle_notes, 1.3)
    base_note_score = average_note_score(base_notes, 1.2)

    scores = [s for s in [top_note_score, middle_note_score, base_note_score] if s is not None]
    combined = sum(scores) / len(scores)

    family_bonus = family_weights.get(scent_family, 0.3) * 0.35
    medium_multiplier = MEDIUM_MULTIPLIERS.get(scent_medium, 1.0)
    strength_multiplier = STRENGTH_WEIGHTS.get(scent_strength, 0.75)

    if scent_medium in  profile.get("preferred_mediums", []):
        medium_multiplier = min(medium_multiplier * 0.1, 1.25)
        
    raw_score = combined + family_bonus * medium_multiplier * strength_multiplier
    normalized_score = (raw_score + 1.2) / 3.0
    final_score = round(max(1.0, min(5.0, 1.0 + normalized_score * 4.0)), 1)

    breakdown = {}

    if top_note_score is not None:
        breakdown["top_note_score"] = round(max(1, min(5, (top_note_score / 1.0 + 1.2) / 3.0 * 4.0 + 1)), 1)
    if middle_note_score is not None:
        breakdown["middle_note_score"] = round(max(1, min(5, (middle_note_score / 1.3 + 1.2) / 3.0 * 4.0 + 1)), 1)
    if base_note_score is not None:
        breakdown["base_note_score"] = round(max(1, min(5, (base_note_score / 1.2 + 1.2) / 3.0 * 4.0 + 1)), 1)
    if scent_medium:
        breakdown["Medium Fit"] = round(medium_multiplier * 3.5, 1)
    if scent_strength:
        breakdown["Strength Fit"] = round(strength_multiplier * 4.5, 1)

VERDICTS = {
    5: [
        "Absolutely perfect! This scent is a dream come true.",
        "A masterpiece! I can't get enough of this fragrance.",
        "This scent is heavenly! I would wear it every day.",
        "An olfactory delight! I am in love with this perfume.",
        "This fragrance is a true gem! I can't stop thinking about it.",
        "This one's special - bottle it.",
        "A masterpiece for your collection.",
        "You'll reach for this every single day.",
    ],
    4: [
        "Strong contender for your shelf.",
        "Very likely to love this one.",
        "Definitely worth a full bottle.",
        "This scent is quite appealing.",
        "A fragrance that could become a favorite.",
        "This perfume has a lot to offer.",
        "A solid choice for your collection.",
    ],
    3: [
        "Comfortable middle ground - sample first.",
        "Might like it, might not.",
        "Inoffensive but not exciting.",
        "A safe choice, but not a standout.",
        "This fragrance is decent, but not remarkable.",
        "A fragrance that is okay, but not memorable.",
        "This scent is average - sample before committing.",
    ],
    2: [
        "Proceed with caution - sample first.",
        "Probably not your thing.",
        "A few notes here might clash.",
        "This fragrance may not suit your taste.",
        "Not a great fit for your preferences.",
        "This scent might not be for you.",
        "This perfume may not align with your likes.",
    ],
    1: [
        "This profile looks like a miss.",
        "Very likely won't suit your taste.",
        "Skip it.",
        "This fragrance is probably not for you.",
        "This scent is likely to be a disappointment.",
        "This perfume is not recommended based on your preferences.",
        "This is not the right fragrance for you."
    ],
}

def get_verdict(score):
    return random.choice(VERDICTS[max(1, min(5, round(score)))])
