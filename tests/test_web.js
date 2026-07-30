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

tests['export writes all four files even when empty'] = () => {
  const { Store } = boot();
  Store.logPerfume(withPerfume());
  const files = Store.exportFiles();
  eq(
    files.map((f) => f.name),
    ['perfumes.csv', 'combinations.csv', 'ratings.csv', 'predictions.csv']
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
