import json, os, random

PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smell-profile.json")

_DEFAULT_LIKED = {
    "bergamot",
    "lemon",
    "grapefruit",
    "yuzu",
    "neroli",
    "petitgrain",
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
    "Black pepper",
    "cardamom",
    "pink pepper",
    "saffron",
    "sea salt",
    "driftwood",
    "amberwood",
    "ambergris",
}
_DEFAULT_DISLIKED = {
    "civet",
    "rose",
    "castoreum",
    "animalic",
    "indole",
    "butyric",
    "cumin",
    "fecal",
    "sweaty",
    "barnyard"
}
_DEFAULT_NEUTRAL = {
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
    "pine"
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
    "Musk": 0.5
}
MEDIUM_MULTIPLIERS = {
    "Spray Perfume (EDP)": 1.0,
    "Spray Perfume (EDT)": 0.9
}