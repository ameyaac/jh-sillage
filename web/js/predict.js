/* PLACEHOLDER MODEL — the legacy heuristic, kept running so the page is usable
 * while the real model is built in Python.
 *
 * Read ARCHITECTURE.md §10 before trusting anything this returns. It fits no
 * parameters, so it cannot be cross-validated, and its constants are unfalsifiable.
 * It will be replaced by an exported Bayesian ridge model; this file is the seam
 * where that swap happens.
 *
 * Two things were wrong enough to fix here rather than wait:
 *
 *   1. It learned from its own predictions. `computeLearnedPrefs` read the stored
 *      `predicted` score, so one confident guess canonised its own notes as
 *      "liked" forever, with no corrective signal. It now reads actual ratings
 *      only, and returns how many it had — the UI says so out loud.
 *   2. Unseen notes scored a constant 0.1, so any novel composition drifted toward
 *      a fixed value regardless of content. Unknown notes are now counted and
 *      reported instead of quietly nudging the score.
 *
 * Exposed as window.Sillage.Predict.
 */
(function (root) {
  'use strict';

  var Sillage = root.Sillage = root.Sillage || {};

  var MODEL_VERSION = 'legacy-heuristic-js';

  /* Seed sentiment, used until enough real ratings exist to override it.
   * In the redesigned model this becomes the prior mean of the regression rather
   * than a set-membership lookup. */
  var NOTE_PROFILE = {
    liked: ['bergamot', 'lemon', 'grapefruit', 'yuzu', 'neroli', 'petitgrain',
      'peony', 'iris', 'violet', 'lavender', 'jasmine', 'lily of the valley',
      'sandalwood', 'cedarwood', 'vetiver', 'patchouli', 'oud', 'agarwood',
      'amber', 'musk', 'white musk', 'clean musk', 'woody musk', 'vanilla',
      'tonka bean', 'benzoin', 'fig', 'pomegranate', 'black pepper', 'cardamom',
      'pink pepper', 'saffron', 'sea salt', 'driftwood', 'amberwood', 'ambergris'],
    disliked: ['civet', 'rose', 'castoreum', 'animalic', 'indole', 'butyric',
      'cumin', 'fecal', 'sweaty', 'barnyard'],
    neutral: ['lily', 'magnolia', 'tuberose', 'gardenia', 'ylang ylang', 'incense',
      'smoke', 'leather', 'tobacco', 'oakmoss', 'labdanum', 'mint', 'eucalyptus',
      'clary sage', 'pine']
  };

  var SCENT_TYPE_WEIGHTS = {
    Floral: 0.6, Woody: 0.55, Oriental: 0.45, Fresh: 0.7, Citrus: 0.75,
    Aquatic: 0.35, Gourmand: 0.25, Spicy: 0.35, 'Fougère': 0.6, Chypre: 0.4,
    Aromatic: 0.5, Musk: 0.5
  };

  function toSet(list) {
    var set = Object.create(null);
    list.forEach(function (n) { set[n.toLowerCase()] = true; });
    return set;
  }

  var SEED_LIKED = toSet(NOTE_PROFILE.liked);
  var SEED_DISLIKED = toSet(NOTE_PROFILE.disliked);
  var SEED_NEUTRAL = toSet(NOTE_PROFILE.neutral);

  /* Preferences implied by the user's ACTUAL ratings. Never predictions.
   *
   * This remains a crude rule — a note in one 4-star perfume is marked liked with
   * no support requirement and no weighting, which is why it needs replacing
   * rather than tuning. */
  function learnedPreferences(trainingSet) {
    var liked = Object.create(null), disliked = Object.create(null);
    (trainingSet || []).forEach(function (entry) {
      var notes = entry.perfume.top_notes
        .concat(entry.perfume.middle_notes, entry.perfume.base_notes);
      notes.forEach(function (note) {
        var key = note.toLowerCase();
        if (entry.rating >= 4) liked[key] = true;
        else if (entry.rating <= 2) disliked[key] = true;
      });
    });
    /* An explicit dislike wins: a note in both a loved and a hated perfume is not
     * evidence of liking it. */
    Object.keys(disliked).forEach(function (key) { delete liked[key]; });
    return { liked: liked, disliked: disliked, count: (trainingSet || []).length };
  }

  function scoreNote(note, learned) {
    var key = String(note).toLowerCase();
    if (learned.disliked[key] || SEED_DISLIKED[key]) return { score: -1, known: true };
    if (learned.liked[key] || SEED_LIKED[key]) return { score: 1, known: true };
    if (SEED_NEUTRAL[key]) return { score: 0.3, known: true };
    return { score: 0, known: false }; /* was 0.1 — an unknown note now abstains */
  }

  /* trainingSet: [{perfume, rating}] from actual ratings only. */
  function run(topNotes, midNotes, baseNotes, scentType, trainingSet) {
    var total = topNotes.length + midNotes.length + baseNotes.length;
    if (!total) return null;

    var learned = learnedPreferences(trainingSet);
    var unknown = [];

    function layerScore(notes, weight) {
      if (!notes.length) return null;
      var sum = 0;
      notes.forEach(function (note) {
        var scored = scoreNote(note, learned);
        if (!scored.known) unknown.push(note);
        sum += scored.score;
      });
      return sum / notes.length * weight;
    }

    var topScore = layerScore(topNotes, 1.0);
    var midScore = layerScore(midNotes, 1.3);   /* heart weighted highest */
    var baseScore = layerScore(baseNotes, 1.2); /* dry-down lasts longest */

    var parts = [topScore, midScore, baseScore].filter(function (s) { return s !== null; });
    var combined = parts.reduce(function (a, b) { return a + b; }, 0) / parts.length;

    var typeBonus = (scentType && SCENT_TYPE_WEIGHTS[scentType] !== undefined)
      ? SCENT_TYPE_WEIGHTS[scentType] : 0.3;

    var raw = combined + typeBonus * 0.4;
    var normalized = (raw + 1) / 2.8;
    var score = Math.max(1, Math.min(5, 1 + normalized * 4));

    var breakdown = {};
    if (topScore !== null) breakdown['Top Notes'] = rescale(topScore / 1.0);
    if (midScore !== null) breakdown['Heart Notes'] = rescale(midScore / 1.3);
    if (baseScore !== null) breakdown['Base Notes'] = rescale(baseScore / 1.2);
    if (scentType) breakdown['Scent Family'] = Math.round(typeBonus * 5 * 10) / 10;

    return {
      total: Math.round(score * 10) / 10,
      breakdown: breakdown,
      modelVersion: MODEL_VERSION,
      /* Everything the UI needs to be honest about how little it knows. */
      unknownNotes: unknown,
      knownFraction: total ? (total - unknown.length) / total : 0,
      ratingsUsed: learned.count
    };
  }

  function rescale(value) {
    return Math.round(Math.max(1, Math.min(5, (value + 1) / 2 * 5)) * 10) / 10;
  }

  var VERDICTS = {
    5: ['This one’s special — bottle it.', 'A masterpiece for your collection.',
      'You’ll reach for this every single day.'],
    4: ['Strong contender for your shelf.', 'Very likely to love this one.',
      'Definitely worth a full bottle.'],
    3: ['Comfortable middle ground — sample first.', 'Might like it, might not.',
      'Inoffensive but not exciting.'],
    2: ['Proceed with caution — sample first.', 'Probably not your thing.',
      'A few notes here might clash.'],
    1: ['This profile looks like a miss.', 'Very likely won’t suit your taste.', 'Skip it.']
  };

  /* Deterministic pick: the verdict must not reshuffle on every keystroke. */
  function verdict(score) {
    var bucket = Math.max(1, Math.min(5, Math.round(score)));
    var options = VERDICTS[bucket];
    var index = Math.abs(Math.round(score * 10)) % options.length;
    return options[index];
  }

  Sillage.Predict = {
    MODEL_VERSION: MODEL_VERSION,
    run: run,
    verdict: verdict,
    learnedPreferences: learnedPreferences
  };
})(window);
