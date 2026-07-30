/* Tests for the browser-side CSV, store and predictor.
 *
 *   node tests/test_web.js
 *   node tests/test_web.js --emit DIR    write export CSVs for the bridge test
 *
 * The modules are loaded into a vm context with a localStorage stub, so they are
 * exercised exactly as the page loads them — no build step, no DOM.
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB_JS = path.join(__dirname, '..', 'web', 'js');
const FILES = ['csv.js', 'store.js', 'predict.js'];

/* ── harness ──────────────────────────────────────────────────────────────── */

function makeLocalStorage(seed) {
  const data = new Map(seed || []);
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
    _data: data
  };
}

/* A fresh page load sharing the given storage — this is how "does it survive a
 * refresh?" gets tested. */
const QUIET_CONSOLE = { log() {}, warn() {}, error() {} };

function boot(localStorage, options) {
  const opts = options || {};
  const win = {
    localStorage: localStorage || makeLocalStorage(),
    console: opts.quiet ? QUIET_CONSOLE : console
  };
  win.window = win;
  const context = vm.createContext(win);
  for (const name of FILES) {
    const file = path.join(WEB_JS, name);
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
  }
  return win.Sillage;
}

const PERFUME = {
  brand: 'Dior',
  name: 'Sauvage',
  perfume_type: 'Woody',
  perfume_medium: 'Spray Perfume (EDT)',
  top_notes: ['bergamot', 'pink pepper'],
  middle_notes: ['lavender'],
  base_notes: ['ambroxan']
};

function withPerfume(overrides) {
  return Object.assign({}, PERFUME, overrides || {});
}

/* Values built inside the vm realm have their own Array/Object prototypes, so
 * deepStrictEqual would reject them as "same structure but not reference-equal".
 * Normalising through JSON compares what we actually care about. */
function eq(actual, expected, label) {
  assert.deepStrictEqual(JSON.parse(JSON.stringify(actual)), expected, label);
}

function throws(fn, label) {
  try {
    fn();
  } catch (err) {
    assert.strictEqual(err.name, 'ValidationError', `${label}: wrong error ${err.name}: ${err.message}`);
    return;
  }
  assert.fail(`${label} should have been refused`);
}

/* ── csv ──────────────────────────────────────────────────────────────────── */

const tests = {};

tests['csv survives quotes, commas and newlines'] = () => {
  const { Csv } = boot();
  const fields = ['a', 'b', 'c'];
  const rows = [{ a: 'Marc "B683"', b: 'Ganymede, the moon', c: 'two\nlines' }];
  const text = Csv.toCsv(fields, rows);
  const parsed = Csv.parseObjects(text);
  eq(parsed.header, fields);
  eq(parsed.rows, rows);
};

tests['csv strips a BOM and accepts LF or CRLF'] = () => {
  const { Csv } = boot();
  const lf = Csv.parseObjects('﻿a,b\n1,2\n');
  const crlf = Csv.parseObjects('a,b\r\n1,2\r\n');
  eq(lf.rows, [{ a: '1', b: '2' }]);
  eq(crlf.rows, [{ a: '1', b: '2' }]);
};

tests['csv skips blank lines'] = () => {
  const { Csv } = boot();
  assert.strictEqual(Csv.parseObjects('a,b\n1,2\n\n\n3,4\n').rows.length, 2);
};

/* ── store: writes and validation ─────────────────────────────────────────── */

tests['perfume round trip keeps notes and casing'] = () => {
  const { Store } = boot();
  const saved = Store.logPerfume(withPerfume({ top_notes: ['Black Pepper', 'black pepper', ' Yuzu '] }));
  assert.strictEqual(saved.perfume_id, 1);
  assert.ok(saved.logged_at, 'logged_at should be stamped');
  eq(Store.perfumes()[0].top_notes, ['Black Pepper', 'Yuzu']);
};

tests['invalid records are refused'] = () => {
  const { Store } = boot();
  const p = Store.logPerfume(withPerfume());
  throws(() => Store.logPerfume(withPerfume({ top_notes: [], middle_notes: [], base_notes: [] })), 'no notes');
  throws(() => Store.logPerfume(withPerfume({ top_notes: ['bergamot; lemon'] })), 'separator in a note');
  throws(() => Store.logRating({ perfumeId: p.perfume_id, rating: 6 }), 'rating above range');
  throws(() => Store.logRating({ perfumeId: p.perfume_id, rating: 0 }), 'rating below range');
  throws(() => Store.logRating({ perfumeId: p.perfume_id, rating: 4.3 }), 'off-step rating');
  throws(() => Store.logRating({ perfumeId: p.perfume_id, rating: 'great' }), 'non-numeric rating');
  throws(() => Store.logRating({ perfumeId: 99, rating: 4 }), 'orphan rating');
  throws(() => Store.logRating({ rating: 4 }), 'no subject');
  assert.strictEqual(Store.perfumes().length, 1);
  assert.strictEqual(Store.ratingEvents().length, 0);
};

tests['ids are max plus one, not length plus one'] = () => {
  const { Store } = boot();
  Store.logPerfume(withPerfume({ name: 'A' }));
  Store.logPerfume(withPerfume({ name: 'B' }));
  Store.logPerfume(withPerfume({ name: 'C' }));

  /* Restore a backup with the middle row missing — the situation where the legacy
   * length+1 scheme reissued an id and attached new ratings to the wrong perfume. */
  const exported = Store.exportFiles()[0].text;
  const lines = exported.split('\r\n').filter((line) => !/^2,/.test(line));
  const report = Store.prepareImport([{ name: 'perfumes.csv', text: lines.join('\r\n') }]);
  assert.strictEqual(report.tables.perfumes.accepted, 2);
  Store.applyImport(report);

  assert.strictEqual(Store.logPerfume(withPerfume({ name: 'D' })).perfume_id, 4);
};

/* ── store: the predictions/ratings separation ────────────────────────────── */

tests['training set never contains predictions'] = () => {
  const { Store } = boot();
  const rated = Store.logPerfume(withPerfume({ name: 'Rated' }));
  const unrated = Store.logPerfume(withPerfume({ name: 'Unrated' }));
  Store.logRating({ perfumeId: rated.perfume_id, rating: 4.5 });
  Store.logPrediction({ subjectId: unrated.perfume_id, value: 4.9, modelVersion: 'x' });
  Store.logPrediction({ subjectId: rated.perfume_id, value: 2.0, modelVersion: 'x' });

  eq(
    Store.trainingSet().map((t) => [t.perfume.name, t.rating]),
    [['Rated', 4.5]]
  );
  eq(Store.unrated().map((p) => p.name), ['Unrated']);
  assert.strictEqual(Store.predictions().length, 2);
};

tests['re-rating appends and the latest wins'] = () => {
  const { Store } = boot();
  const p = Store.logPerfume(withPerfume());
  Store.logRating({ perfumeId: p.perfume_id, rating: 3.0, ratedAt: '2026-01-01T10:00:00' });
  Store.logRating({ perfumeId: p.perfume_id, rating: 4.5, ratedAt: '2026-06-01T10:00:00' });
  assert.strictEqual(Store.ratingEvents().length, 2, 'history must survive');
  assert.strictEqual(Store.currentRatings()[p.perfume_id].rating, 4.5);
  eq(Store.trainingSet().map((t) => t.rating), [4.5]);
};

/* ── store: persistence ───────────────────────────────────────────────────── */

tests['the log survives a page reload'] = () => {
  const storage = makeLocalStorage();
  const first = boot(storage);
  const p = first.Store.logPerfume(withPerfume({ name: 'Persisted' }));
  first.Store.logRating({ perfumeId: p.perfume_id, rating: 4.0 });

  const second = boot(storage); // fresh "page load", same browser storage
  eq(second.Store.perfumes().map((x) => x.name), ['Persisted']);
  eq(second.Store.trainingSet().map((t) => t.rating), [4.0]);
};

tests['a log from another schema version is ignored, not misread'] = () => {
  const storage = makeLocalStorage([['sillage.log.v1', JSON.stringify({
    schema_version: 99, perfumes: [{ perfume_id: 1, name: 'Future' }]
  })]]);
  const { Store } = boot(storage, { quiet: true });
  eq(Store.perfumes(), []);
};

tests['corrupt storage does not break the page'] = () => {
  const storage = makeLocalStorage([['sillage.log.v1', '{not json']]);
  const { Store } = boot(storage, { quiet: true });
  eq(Store.perfumes(), []);
};

/* ── store: combinations ──────────────────────────────────────────────────── */

function pair(Store) {
  return [
    Store.logPerfume(withPerfume({ name: 'Sauvage' })),
    Store.logPerfume(withPerfume({ brand: 'Chanel', name: 'Bleu' }))
  ];
}

tests['combination members are canonically ordered'] = () => {
  const { Store } = boot();
  const [a, b] = pair(Store);
  const combo = Store.logCombination(b.perfume_id, a.perfume_id, 'a');
  eq([combo.perfume_id_a, combo.perfume_id_b], [a.perfume_id, b.perfume_id]);
  assert.strictEqual(combo.applied_first, 'b', 'order must follow the swap');
};

tests['re-logging a pair reuses its id'] = () => {
  const { Store } = boot();
  const [a, b] = pair(Store);
  const first = Store.logCombination(a.perfume_id, b.perfume_id, 'b');
  const again = Store.logCombination(b.perfume_id, a.perfume_id, 'a');
  assert.strictEqual(again.combination_id, first.combination_id);
  assert.strictEqual(Store.combinations().length, 1);
};

tests['pair ratings stay out of the single-perfume training set'] = () => {
  const { Store } = boot();
  const [a, b] = pair(Store);
  const combo = Store.logCombination(a.perfume_id, b.perfume_id, 'b');
  Store.logRating({ perfumeId: a.perfume_id, rating: 4.0 });
  Store.logRating({ combinationId: combo.combination_id, rating: 2.5 });

  eq(Store.trainingSet().map((t) => t.rating), [4.0]);
  const pairs = Store.pairTrainingSet();
  assert.strictEqual(pairs.length, 1);
  assert.strictEqual(pairs[0].rating, 2.5);
  eq([pairs[0].a.name, pairs[0].b.name], ['Sauvage', 'Bleu']);
  assert.strictEqual(Store.summary().mean_rating, 4.0, 'pair ratings must not move this');
};

tests['invalid combinations are refused'] = () => {
  const { Store } = boot();
  const [a, b] = pair(Store);
  throws(() => Store.logCombination(a.perfume_id, a.perfume_id), 'self-layering');
  throws(() => Store.logCombination(a.perfume_id, 99), 'unknown member');
  throws(() => Store.logCombination(a.perfume_id, b.perfume_id, 'first'), 'bad applied_first');
  assert.strictEqual(Store.combinations().length, 0);
};

/* ── store: grouping ──────────────────────────────────────────────────────── */

tests['group key merges concentrations but not flankers'] = () => {
  const { Store } = boot();
  assert.strictEqual(
    Store.groupKey('Chanel', 'Bleu de Chanel EDT'),
    Store.groupKey('Chanel', 'Bleu de Chanel EDP')
  );
  assert.strictEqual(
    Store.groupKey('Chanel', 'Bleu de Chanel Parfum'),
    Store.groupKey('Chanel', 'bleu  de   chanel!')
  );
  assert.notStrictEqual(Store.groupKey('Dior', 'Sauvage'), Store.groupKey('Dior', 'Sauvage Intense'));
  assert.notStrictEqual(Store.groupKey('Dior', 'Sauvage'), Store.groupKey('Guerlain', 'Sauvage'));
};

/* ── store: export and import ─────────────────────────────────────────────── */

tests['export writes all five files even when empty'] = () => {
  const { Store } = boot();
  Store.logPerfume(withPerfume());
  const files = Store.exportFiles();
  eq(
    files.map((f) => f.name),
    ['perfumes.csv', 'combinations.csv', 'ratings.csv', 'predictions.csv',
      'preferences.csv']
  );
  files.forEach((f) => assert.ok(f.text.split('\r\n')[0].length, `${f.name} needs a header`));
};

tests['export and import round trip is lossless'] = () => {
  const { Store } = boot();
  const [a, b] = pair(Store);
  const combo = Store.logCombination(a.perfume_id, b.perfume_id, 'b');
  Store.logRating({ perfumeId: a.perfume_id, rating: 4.5, context: 'hot afternoon' });
  Store.logRating({ perfumeId: b.perfume_id, rating: 3.0 });
  Store.logRating({ combinationId: combo.combination_id, rating: 4.0 });
  Store.logPrediction({ subjectId: a.perfume_id, value: 4.2, modelVersion: 'ridge-v1' });
  const before = Store.summary();

  const files = Store.exportFiles();
  const fresh = boot().Store;
  const report = fresh.prepareImport(files);
  assert.strictEqual(report.rejected, 0, JSON.stringify(report.tables));
  fresh.applyImport(report);

  const after = fresh.summary();
  ['perfumes', 'rated', 'rating_events', 'combinations', 'rated_combinations',
    'predictions_logged', 'mean_rating', 'distinct_notes'].forEach((key) => {
    assert.strictEqual(after[key], before[key], `${key} changed across the round trip`);
  });
  assert.strictEqual(fresh.ratingEvents()[0].context, 'hot afternoon');
  assert.strictEqual(fresh.pairTrainingSet()[0].combination.applied_first, 'b');
};

tests['files are identified by header, not name'] = () => {
  const { Store } = boot();
  Store.logPerfume(withPerfume());
  const perfumes = Store.exportFiles()[0];
  const report = Store.prepareImport([{ name: 'perfumes (1).csv', text: perfumes.text }]);
  assert.strictEqual(report.tables.perfumes.accepted, 1);
  eq(report.unrecognised, []);
};

tests['unrelated csvs are reported, not imported'] = () => {
  const { Store } = boot();
  const report = Store.prepareImport([{ name: 'shopping.csv', text: 'item,qty\nmilk,2\n' }]);
  eq(report.unrecognised, ['shopping.csv']);
  assert.strictEqual(report.accepted, 0);
};

tests['orphan and duplicate rows are rejected individually'] = () => {
  const { Store } = boot();
  const perfumes = 'perfume_id,brand,name,perfume_type,perfume_medium,perfume_strength,' +
    'top_notes,middle_notes,base_notes,comment,logged_at\r\n' +
    '1,Dior,Sauvage,Woody,,,bergamot,,,,2026-01-01T00:00:00\r\n' +
    '1,Dior,Clash,Woody,,,lemon,,,,2026-01-01T00:00:00\r\n' +
    '2,Nobody,Nothing,,,,,,,,\r\n';
  const ratings = 'rating_id,subject_type,subject_id,rating,context,rated_at\r\n' +
    '1,perfume,1,4.5,,2026-01-02T00:00:00\r\n' +
    '2,perfume,99,5,,2026-01-03T00:00:00\r\n';

  const report = Store.prepareImport([
    { name: 'perfumes.csv', text: perfumes },
    { name: 'ratings.csv', text: ratings }
  ]);
  assert.strictEqual(report.tables.perfumes.accepted, 1);
  assert.strictEqual(report.tables.perfumes.rejected.length, 2);
  assert.ok(report.tables.perfumes.rejected.some((r) => /duplicate perfume_id/.test(r)));
  assert.strictEqual(report.tables.ratings.accepted, 1);
  assert.ok(/perfume 99 not in the import/.test(report.tables.ratings.rejected[0]));
};

tests['a rejected import leaves the existing log untouched'] = () => {
  const { Store } = boot();
  Store.logPerfume(withPerfume({ name: 'Mine' }));
  const report = Store.prepareImport([{ name: 'shopping.csv', text: 'item,qty\nmilk,2\n' }]);
  assert.strictEqual(report.accepted, 0);
  eq(Store.perfumes().map((p) => p.name), ['Mine']);
};

/* ── store: declared preferences ──────────────────────────────────────────── */

tests['palette round trip'] = () => {
  const { Store } = boot();
  Store.logPreference('family', 'citrus', 1.0);
  Store.logPreference('family', 'animalic', -1.0);
  Store.logPreference('note', 'Vetiver', 0.5);
  Store.logPreference('medium', 'Spray Perfume (EDP)', 1.0);
  Store.logPreference('axis', 'powdery', -0.5);

  const palette = Store.palette();
  eq(palette.family, { citrus: 1.0, animalic: -1.0 });
  eq(palette.note, { Vetiver: 0.5 });
  eq(palette.medium, { 'Spray Perfume (EDP)': 1.0 });
  eq(palette.axis, { powdery: -0.5 });
  assert.ok(Store.hasPalette());
};

tests['declared preferences never reach the training set'] = () => {
  const { Store } = boot();
  const p = Store.logPerfume(withPerfume());
  Store.logPreference('family', 'citrus', 1.0);
  Store.logPreference('note', 'bergamot', 1.0);
  eq(Store.trainingSet(), []);

  Store.logRating({ perfumeId: p.perfume_id, rating: 4.0 });
  eq(Store.trainingSet().map((t) => t.rating), [4.0]);
  assert.strictEqual(Store.summary().mean_rating, 4.0, 'declarations must not move this');
};

tests['redeclaring appends and the latest wins'] = () => {
  const { Store } = boot();
  Store.logPreference('family', 'woody', 0.5, '2026-01-01T00:00:00');
  Store.logPreference('family', 'woody', -1.0, '2026-06-01T00:00:00');
  eq(Store.palette().family, { woody: -1.0 });
  assert.strictEqual(Store.preferenceEvents().length, 2, 'history must survive');
};

tests['a retraction leaves the palette empty but stays on disk'] = () => {
  const { Store } = boot();
  Store.logPreference('family', 'gourmand', 1.0, '2026-01-01T00:00:00');
  Store.logPreference('family', 'gourmand', 0.0, '2026-02-01T00:00:00');
  eq(Store.palette(), {});
  assert.ok(!Store.hasPalette());
  eq(Store.palette(true).family, { gourmand: 0.0 });
  assert.strictEqual(Store.preferenceEvents().length, 2);
};

tests['invalid preferences are refused'] = () => {
  const { Store } = boot();
  throws(() => Store.logPreference('vibe', 'citrus', 1.0), 'unknown scope');
  throws(() => Store.logPreference('family', 'petrichor', 1.0), 'unknown family');
  throws(() => Store.logPreference('axis', 'loud', 1.0), 'unknown axis');
  throws(() => Store.logPreference('note', '   ', 1.0), 'empty key');
  throws(() => Store.logPreference('family', 'citrus', 2.0), 'weight above range');
  /* 4.5 is a plausible rating, which is the confusion the signed scale prevents. */
  throws(() => Store.logPreference('family', 'citrus', 4.5), 'rating-shaped weight');
  throws(() => Store.logPreference('family', 'citrus', 'lots'), 'non-numeric weight');
  eq(Store.preferenceEvents(), []);
};

tests['setPalette skips no-op writes'] = () => {
  const { Store } = boot();
  Store.logPreference('family', 'citrus', 1.0);
  /* Unchanged value, and a zero for something never declared: neither is a change. */
  const written = Store.setPalette([
    { scope: 'family', key: 'citrus', weight: 1.0 },
    { scope: 'family', key: 'woody', weight: 0.0 },
    { scope: 'family', key: 'amber', weight: -0.5 }
  ]);
  assert.strictEqual(written.length, 1);
  eq(Store.palette().family, { citrus: 1.0, amber: -0.5 });
  assert.strictEqual(Store.preferenceEvents().length, 2);
};

tests['a v1 log in localStorage is upgraded, not discarded'] = () => {
  const storage = makeLocalStorage([['sillage.log.v1', JSON.stringify({
    schema_version: 1,
    perfumes: [{
      perfume_id: 1, brand: 'Dior', name: 'Sauvage', perfume_type: 'Woody',
      perfume_medium: '', perfume_strength: '', top_notes: ['bergamot'],
      middle_notes: [], base_notes: [], comment: '', logged_at: '2026-01-01T00:00:00'
    }],
    combinations: [],
    ratings: [{
      rating_id: 1, subject_type: 'perfume', subject_id: 1, rating: 4.5,
      context: '', rated_at: '2026-01-02T00:00:00'
    }],
    predictions: []
  })]]);
  const { Store } = boot(storage, { quiet: true });
  eq(Store.perfumes().map((p) => p.name), ['Sauvage']);
  eq(Store.trainingSet().map((t) => t.rating), [4.5]);
  eq(Store.palette(), {});
  /* Persisted under the new key, with the old one left as an accidental backup. */
  assert.ok(storage.getItem('sillage.log.v2'), 'should be saved under the v2 key');
  assert.strictEqual(JSON.parse(storage.getItem('sillage.log.v2')).schema_version, 2);
};

tests['preferences survive the export/import round trip'] = () => {
  const { Store } = boot();
  Store.logPreference('family', 'citrus', 1.0);
  Store.logPreference('note', 'Cumin', -1.0);
  const files = Store.exportFiles();
  assert.ok(files.some((f) => f.name === 'preferences.csv'));

  const fresh = boot().Store;
  const report = fresh.prepareImport(files);
  assert.strictEqual(report.rejected, 0, JSON.stringify(report.tables));
  fresh.applyImport(report);
  eq(fresh.palette().family, { citrus: 1.0 });
  eq(fresh.palette().note, { Cumin: -1.0 });
};

/* ── predictor ────────────────────────────────────────────────────────────── */

tests['predictor learns from actual ratings only'] = () => {
  const { Store, Predict } = boot();
  const p = Store.logPerfume(withPerfume({ top_notes: ['zzzfictional'] }));
  Store.logPrediction({ subjectId: p.perfume_id, value: 5.0, modelVersion: 'x' });

  /* A logged prediction of 5.0 must not make its notes count as liked. */
  const beforeLearned = Predict.learnedPreferences(Store.trainingSet());
  assert.strictEqual(beforeLearned.count, 0);
  eq(Object.keys(beforeLearned.liked), []);

  Store.logRating({ perfumeId: p.perfume_id, rating: 5.0 });
  const afterLearned = Predict.learnedPreferences(Store.trainingSet());
  assert.strictEqual(afterLearned.count, 1);
  assert.ok(afterLearned.liked.zzzfictional, 'an actual 5-star rating should teach it');
};

tests['a dislike beats a like for the same note'] = () => {
  const { Predict } = boot();
  const learned = Predict.learnedPreferences([
    { perfume: { top_notes: ['cumin'], middle_notes: [], base_notes: [] }, rating: 5 },
    { perfume: { top_notes: ['cumin'], middle_notes: [], base_notes: [] }, rating: 1 }
  ]);
  assert.ok(learned.disliked.cumin);
  assert.ok(!learned.liked.cumin, 'a note in a hated perfume is not evidence of liking it');
};

tests['a declared note preference beats the shipped seed list'] = () => {
  const { Predict } = boot();
  /* "rose" is in the built-in disliked seed. Declaring you love it must win. */
  const seeded = Predict.run(['rose'], [], [], '', [], null, '');
  const declared = Predict.run(['rose'], [], [], '', [], { note: { rose: 1.0 } }, '');
  assert.ok(declared.total > seeded.total, 'a declaration should override the seed');
  assert.strictEqual(declared.declarationsUsed, 1);
};

tests['a real rating beats a declared preference'] = () => {
  const { Predict } = boot();
  const palette = { note: { zzzfictional: 1.0 } };
  const withDeclaration = Predict.run(['zzzfictional'], [], [], '', [], palette, '');
  const contradicted = Predict.run(['zzzfictional'], [], [], '', [
    { perfume: { top_notes: ['zzzfictional'], middle_notes: [], base_notes: [] }, rating: 1 }
  ], palette, '');
  assert.ok(contradicted.total < withDeclaration.total,
    'evidence must override declaration');
};

tests['a declared family preference applies only via a mapped scent family'] = () => {
  const { Predict } = boot();
  const palette = { family: { woody: -1.0 } };
  const base = Predict.run(['bergamot'], [], [], 'Woody', [], null, '');
  const disliked = Predict.run(['bergamot'], [], [], 'Woody', [], palette, '');
  assert.ok(disliked.total < base.total);
  assert.strictEqual(disliked.declaredFamily, 'woody');

  /* Chypre has no single family in the taxonomy, so nothing is applied. */
  const unmapped = Predict.run(['bergamot'], [], [], 'Chypre', [], palette, '');
  assert.strictEqual(unmapped.declaredFamily, null);
};

tests['a declared medium preference applies on exact match'] = () => {
  const { Predict } = boot();
  const palette = { medium: { 'Attar / Oil': 1.0 } };
  const matched = Predict.run(['bergamot'], [], [], '', [], palette, 'Attar / Oil');
  const other = Predict.run(['bergamot'], [], [], '', [], palette, 'Body Mist');
  assert.strictEqual(matched.declaredMedium, 'Attar / Oil');
  assert.strictEqual(other.declaredMedium, null);
  assert.ok(matched.total > other.total);
};

tests['no palette is a valid complete answer'] = () => {
  const { Predict } = boot();
  const none = Predict.run(['bergamot'], [], [], 'Citrus', [], null, '');
  const empty = Predict.run(['bergamot'], [], [], 'Citrus', [], {}, '');
  assert.strictEqual(none.total, empty.total);
  assert.strictEqual(empty.declarationsUsed, 0);
};

tests['unknown notes are reported instead of nudging the score'] = () => {
  const { Predict } = boot();
  const result = Predict.run(['zzzunknownnote'], [], [], '', []);
  eq(result.unknownNotes, ['zzzunknownnote']);
  assert.strictEqual(result.knownFraction, 0);
  assert.strictEqual(result.ratingsUsed, 0);
};

tests['empty input predicts nothing'] = () => {
  const { Predict } = boot();
  assert.strictEqual(Predict.run([], [], [], 'Woody', []), null);
};

tests['scores stay inside 1..5 and the verdict is stable'] = () => {
  const { Predict } = boot();
  const loved = Predict.run(['bergamot'], ['jasmine'], ['vanilla'], 'Citrus', []);
  const hated = Predict.run(['civet'], ['indole'], ['castoreum'], 'Gourmand', []);
  [loved, hated].forEach((r) => {
    assert.ok(r.total >= 1 && r.total <= 5, `score ${r.total} out of range`);
  });
  assert.ok(loved.total > hated.total, 'liked notes should outscore disliked ones');
  assert.strictEqual(Predict.verdict(loved.total), Predict.verdict(loved.total));
};

tests['a palette-only log is still worth backing up'] = () => {
  /* The download guard used to check "has the user logged a perfume", which made a
   * declared palette with no perfumes yet — the normal onboarding state —
   * unbackupable. */
  const { Store } = boot();
  assert.ok(Store.isEmpty(), 'a fresh log should be empty');

  Store.logPreference('family', 'citrus', 1.0);
  assert.ok(!Store.isEmpty(), 'a declared palette is something to back up');

  const files = Store.exportFiles();
  const preferences = files.filter((f) => f.name === 'preferences.csv')[0];
  assert.ok(/citrus/.test(preferences.text), 'the declaration must be in the CSV');

  /* And all five files are still written, so the backup stays complete. */
  assert.strictEqual(files.length, 5);
  files.forEach((f) => assert.ok(f.text.split('\r\n')[0].length, f.name + ' needs a header'));
};

tests['a palette-only backup restores on its own'] = () => {
  const { Store } = boot();
  Store.logPreference('family', 'amber', 1.0);
  Store.logPreference('note', 'Cumin', -1.0);

  const fresh = boot().Store;
  const report = fresh.prepareImport(Store.exportFiles());
  assert.strictEqual(report.rejected, 0, JSON.stringify(report.tables));
  fresh.applyImport(report);
  eq(fresh.palette().family, { amber: 1.0 });
  eq(fresh.palette().note, { Cumin: -1.0 });
  eq(fresh.perfumes(), []);
};

/* ── static markup / stylesheet checks ────────────────────────────────────── */

/* These are lints, not render tests — there is no DOM here. They exist because the
 * bugs they catch are invisible in unit tests and obvious in a browser. */

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const STYLES_CSS = fs.readFileSync(path.join(__dirname, '..', 'web', 'styles.css'), 'utf8');
/* Comments mention selectors, so order checks must run on the declarations only. */
const STYLES_CODE = STYLES_CSS.replace(/\/\*[\s\S]*?\*\//g, '');

tests['[hidden] is forced off, so view switching actually hides views'] = () => {
  /* The `hidden` attribute is only honoured by the UA stylesheet, so any author rule
   * setting `display` on the element beats it. `.main-content { display: grid }` did
   * exactly that, and both views rendered stacked. */
  const hiddenElements = INDEX_HTML.match(/<[^>]*\shidden(?=[\s>])[^>]*>/g) || [];
  assert.ok(hiddenElements.length, 'expected at least one element using [hidden]');

  const rule = /\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important/.test(STYLES_CODE);
  assert.ok(rule, '[hidden] { display: none !important } is missing from styles.css');

  /* And it must come before anything that sets a display value, or a later rule of
   * equal specificity would win again. */
  const hiddenAt = STYLES_CODE.search(/\[hidden\]\s*\{/);
  const mainContentAt = STYLES_CODE.search(/\.main-content\s*\{/);
  assert.ok(hiddenAt < mainContentAt,
    'the [hidden] rule must precede .main-content, which sets display: grid');
};

tests['every view has a tab and every tab has a view'] = () => {
  const views = (INDEX_HTML.match(/data-view="([^"]+)"/g) || [])
    .map((m) => m.replace(/.*"([^"]+)"/, '$1'));
  const targets = (INDEX_HTML.match(/data-view-target="([^"]+)"/g) || [])
    .map((m) => m.replace(/.*"([^"]+)"/, '$1'));
  assert.ok(views.length >= 2, 'expected at least two views');
  eq(views.slice().sort(), targets.slice().sort());
};

tests['exactly one view starts visible'] = () => {
  /* Two visible on load is the stacked-views bug; zero is a blank page. */
  const sections = INDEX_HTML.match(/<div[^>]*data-view="[^"]*"[^>]*>/g) || [];
  const visible = sections.filter((tag) => !/\shidden(?=[\s>])/.test(tag));
  assert.strictEqual(visible.length, 1,
    'exactly one [data-view] section should lack the hidden attribute, found ' +
    visible.length);
};

tests['every download trigger in the markup is wired up'] = () => {
  const APP_JS = fs.readFileSync(path.join(__dirname, '..', 'web', 'js', 'app.js'), 'utf8');
  ['downloadBtn', 'downloadAllBtn', 'downloadPaletteBtn'].forEach((id) => {
    assert.ok(INDEX_HTML.indexOf('id="' + id + '"') !== -1, id + ' missing from markup');
    assert.ok(APP_JS.indexOf("$('" + id + "')") !== -1, id + ' not wired in app.js');
  });
};

tests['scripts load in dependency order and are classic, not modules'] = () => {
  const srcs = (INDEX_HTML.match(/<script src="([^"]+)"/g) || [])
    .map((m) => m.replace(/.*"([^"]+)"/, '$1'));
  eq(srcs, ['web/js/csv.js', 'web/js/store.js', 'web/js/predict.js',
    'web/js/preferences.js', 'web/js/app.js']);
  assert.ok(!/<script[^>]*type="module"/.test(INDEX_HTML),
    'ES modules are blocked by file:// CORS; keep these classic scripts');
};

/* ── runner ───────────────────────────────────────────────────────────────── */

/* Emit a populated export for tests/test_bridge.py to feed to the Python importer. */
function emit(dir) {
  const { Store } = boot();
  const a = Store.logPerfume(withPerfume({
    brand: 'Marc-Antoine Barrois "B683"',
    name: 'Ganymede, the moon',
    top_notes: ['Mandarin', 'Violet Leaf'],
    middle_notes: ['Osmanthus Tincture'],
    base_notes: ['Suede', 'Ambroxan']
  }));
  const b = Store.logPerfume(withPerfume({ brand: 'Chanel', name: 'Bleu EDP' }));
  const combo = Store.logCombination(a.perfume_id, b.perfume_id, 'b');
  Store.logRating({ perfumeId: a.perfume_id, rating: 4.5, context: 'hot afternoon' });
  Store.logRating({ perfumeId: b.perfume_id, rating: 3.0 });
  Store.logRating({ combinationId: combo.combination_id, rating: 4.0 });
  Store.logPrediction({ subjectId: a.perfume_id, value: 4.2, modelVersion: 'legacy-heuristic-js' });
  Store.logPreference('family', 'citrus', 1.0);
  Store.logPreference('family', 'animalic', -1.0);
  Store.logPreference('note', 'Ambroxan', 0.5);
  Store.logPreference('medium', 'Spray Perfume (EDP)', 1.0);

  fs.mkdirSync(dir, { recursive: true });
  Store.exportFiles().forEach((file) => {
    fs.writeFileSync(path.join(dir, file.name), file.text, 'utf8');
  });
  process.stdout.write(JSON.stringify(Store.summary()) + '\n');
}

function main() {
  const emitFlag = process.argv.indexOf('--emit');
  if (emitFlag !== -1) {
    emit(process.argv[emitFlag + 1]);
    return 0;
  }

  const names = Object.keys(tests);
  let failed = 0;
  for (const name of names) {
    try {
      tests[name]();
      console.log(`ok   ${name}`);
    } catch (err) {
      failed++;
      console.log(`FAIL ${name}: ${err.message}`);
    }
  }
  console.log(`\n${names.length - failed}/${names.length} passed`);
  return failed ? 1 : 0;
}

process.exit(main());
