/* Browser-side rating log — the same five tables as data/store.py, in localStorage.
 *
 * Field names are deliberately snake_case to match the Python schema character for
 * character. The CSVs this produces are the ones `python -m data.portable import`
 * reads, so any renaming here would be a silent incompatibility.
 *
 * Two properties carried over from the Python side because they are the reason it
 * is shaped this way:
 *   - Predictions live in their own table and are never a training input. The old
 *     page learned from `entry.predicted`, which is a closed loop with no
 *     corrective signal.
 *   - Ratings are append-only events, so re-rating a perfume keeps both judgments.
 *   - Declared preferences are a prior, not evidence. They live in their own table
 *     on a signed [-1, 1] affinity scale, deliberately off the 1-5 rating scale so
 *     they can never be mistaken for a training target.
 *
 * The previous version of this page kept the log in a plain in-memory array, so a
 * refresh lost everything. Every write here persists immediately.
 *
 * Exposed as window.Sillage.Store.
 */
(function (root) {
  'use strict';

  var Sillage = root.Sillage = root.Sillage || {};
  var Csv = Sillage.Csv;

  /* The version goes up with every release, so it also names the app version.
   * Older keys, newest first, are read once on first load and upgraded in place.
   * Every upgrade so far is purely additive:
   *   v1 -> v2 added preferences.
   *   v2 -> v3 lets perfume_type hold several ";"-joined scent families; a v2
   *            single-family cell is already a valid one-element v3 list.
   *   v3 -> v4 no format change: layering page, deleting entries, themes.
   *   v4 -> v5 no format change: Appearance became a centred dialog.
   *   v5 -> v6 no format change: Appearance dialog flows as one column. */
  var SCHEMA_VERSION = 6;
  var STORAGE_KEY = 'sillage.log.v6';
  var LEGACY_STORAGE_KEYS = ['sillage.log.v5', 'sillage.log.v4', 'sillage.log.v3', 'sillage.log.v2', 'sillage.log.v1'];
  /* Schema versions this build can read and upgrade in place. */
  var UPGRADABLE_FROM = [1, 2, 3, 4, 5];
  var NOTE_SEPARATOR = ';';
  var MIN_RATING = 1.0, MAX_RATING = 5.0, RATING_STEP = 0.5;

  var MIN_WEIGHT = -1.0, MAX_WEIGHT = 1.0;

  var SUBJECT_PERFUME = 'perfume';
  var SUBJECT_COMBINATION = 'combination';
  var SUBJECT_TYPES = [SUBJECT_PERFUME, SUBJECT_COMBINATION];
  var APPLIED_FIRST_VALUES = ['', 'a', 'b'];

  var SCOPE_FAMILY = 'family';
  var SCOPE_AXIS = 'axis';
  var SCOPE_NOTE = 'note';
  var SCOPE_MEDIUM = 'medium';
  var PREFERENCE_SCOPES = [SCOPE_FAMILY, SCOPE_AXIS, SCOPE_NOTE, SCOPE_MEDIUM];

  /* What the five-way control maps onto. Zero is a real value: it retracts an
   * earlier declaration rather than being missing. */
  var PREFERENCE_LEVELS = {
    love: 1.0, like: 0.5, neutral: 0.0, dislike: -0.5, avoid: -1.0
  };

  var FAMILIES = ['citrus', 'floral', 'white-floral', 'fruity', 'green', 'aromatic',
    'spicy', 'woody', 'resinous', 'amber', 'gourmand', 'musk', 'animalic', 'aquatic'];

  var AXES = ['sweet', 'fresh', 'warm', 'woody', 'green', 'spicy', 'animalic', 'powdery'];

  var PERFUME_FIELDS = ['perfume_id', 'brand', 'name', 'perfume_type', 'perfume_medium',
    'perfume_strength', 'top_notes', 'middle_notes', 'base_notes', 'comment', 'logged_at'];
  var COMBINATION_FIELDS = ['combination_id', 'perfume_id_a', 'perfume_id_b',
    'applied_first', 'comment', 'created_at'];
  var RATING_FIELDS = ['rating_id', 'subject_type', 'subject_id', 'rating', 'context', 'rated_at'];
  var PREDICTION_FIELDS = ['prediction_id', 'subject_type', 'subject_id', 'value',
    'interval_low', 'interval_high', 'model_version', 'spec_hash', 'predicted_at'];
  var PREFERENCE_FIELDS = ['preference_id', 'scope', 'key', 'weight', 'declared_at'];

  var TABLES = {
    perfumes:     { file: 'perfumes.csv',     fields: PERFUME_FIELDS,     id: 'perfume_id' },
    combinations: { file: 'combinations.csv', fields: COMBINATION_FIELDS, id: 'combination_id' },
    ratings:      { file: 'ratings.csv',      fields: RATING_FIELDS,      id: 'rating_id' },
    predictions:  { file: 'predictions.csv',  fields: PREDICTION_FIELDS,  id: 'prediction_id' },
    preferences:  { file: 'preferences.csv',  fields: PREFERENCE_FIELDS,  id: 'preference_id' }
  };
  /* Dependency order: a row may only reference something already imported. */
  var IMPORT_ORDER = ['perfumes', 'combinations', 'ratings', 'predictions', 'preferences'];

  function ValidationError(message) {
    this.name = 'ValidationError';
    this.message = message;
  }
  ValidationError.prototype = Object.create(Error.prototype);
  ValidationError.prototype.constructor = ValidationError;

  function fail(message) { throw new ValidationError(message); }

  /* ── time ────────────────────────────────────────────────────────────────── */

  /* Local ISO to the second, matching datetime.now().isoformat(timespec='seconds').
   * Deliberately naive/local: the Python side writes the same shape, and these
   * strings are compared as plain text when ordering events. */
  function nowStamp() {
    var d = new Date(), p = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
      'T' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  /* ── notes ───────────────────────────────────────────────────────────────── */

  function parseNotes(cell) {
    if (!cell) return [];
    return String(cell).split(NOTE_SEPARATOR)
      .map(function (n) { return n.trim(); })
      .filter(Boolean);
  }

  function formatNotes(notes) { return (notes || []).join(NOTE_SEPARATOR); }

  /* Strip, drop blanks, reject separators, de-duplicate case-insensitively while
   * keeping the first spelling. Normalisation and family resolution belong to the
   * feature layer — storage keeps what was typed. */
  function cleanNotes(notes, layer) {
    var out = [], seen = Object.create(null);
    (notes || []).forEach(function (raw) {
      var note = String(raw == null ? '' : raw).trim();
      if (!note) return;
      if (note.indexOf(NOTE_SEPARATOR) !== -1) {
        fail(layer + ' note "' + note + '" contains "' + NOTE_SEPARATOR +
          '", which separates notes in storage');
      }
      var key = note.toLowerCase();
      if (seen[key]) return;
      seen[key] = true;
      out.push(note);
    });
    return out;
  }

  /* A perfume can belong to several scent families. They share one cell, joined
   * like a note layer, so a single family reads back unchanged. Mirrors
   * schema.clean_families. */
  function cleanFamilies(value) {
    var items = Array.isArray(value) ? value : parseNotes(value);
    return cleanNotes(items, 'family');
  }

  function perfumeTypes(perfume) {
    return parseNotes(perfume && perfume.perfume_type);
  }

  /* ── identity ────────────────────────────────────────────────────────────── */

  /* Concentration terms only — flanker words like "Intense" usually mean a
   * different juice and must stay in their own group. Mirrors schema.group_key. */
  var CONCENTRATION_TERMS = ['eau de parfum', 'eau de toilette', 'eau de cologne',
    'eau fraiche', 'extrait de parfum', 'parfum', 'cologne', 'edp', 'edt', 'edc'];

  function groupKey(brand, name) {
    var text = ((brand || '') + ' ' + (name || '')).toLowerCase();
    text = text.replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
    CONCENTRATION_TERMS.forEach(function (term) {
      text = text.replace(new RegExp('\\b' + term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'g'), ' ');
    });
    return text.replace(/\s+/g, ' ').trim();
  }

  /* ── validation ──────────────────────────────────────────────────────────── */

  function asInt(value, label) {
    var n = Number(value);
    if (!isFinite(n) || Math.floor(n) !== n) fail(label + ' "' + value + '" is not an id');
    return n;
  }

  function validateSubject(subjectType, subjectId) {
    var kind = String(subjectType || '').trim().toLowerCase();
    if (SUBJECT_TYPES.indexOf(kind) === -1) {
      fail('subject_type must be one of ' + SUBJECT_TYPES.join(', ') + ', got "' + subjectType + '"');
    }
    return { subject_type: kind, subject_id: asInt(subjectId, 'subject_id') };
  }

  function validateRating(value) {
    var n = Number(value);
    if (value === '' || value === null || value === undefined || !isFinite(n)) {
      fail('rating "' + value + '" is not a number');
    }
    if (n < MIN_RATING || n > MAX_RATING) {
      fail('rating ' + n + ' outside ' + MIN_RATING + '–' + MAX_RATING);
    }
    if (Math.abs(n / RATING_STEP - Math.round(n / RATING_STEP)) > 1e-9) {
      fail('rating ' + n + ' is not a multiple of ' + RATING_STEP);
    }
    return Math.round(n * 10) / 10;
  }

  function str(value) { return value === null || value === undefined ? '' : String(value).trim(); }

  function validatePerfume(record) {
    var top = cleanNotes(record.top_notes, 'top');
    var middle = cleanNotes(record.middle_notes, 'middle');
    var base = cleanNotes(record.base_notes, 'base');
    if (!top.length && !middle.length && !base.length) fail('a perfume needs at least one note');
    return {
      perfume_id: record.perfume_id,
      brand: str(record.brand),
      name: str(record.name),
      perfume_type: formatNotes(cleanFamilies(record.perfume_type)),
      perfume_medium: str(record.perfume_medium),
      perfume_strength: str(record.perfume_strength),
      top_notes: top,
      middle_notes: middle,
      base_notes: base,
      comment: str(record.comment),
      logged_at: str(record.logged_at)
    };
  }

  function validateCombination(record) {
    var a = asInt(record.perfume_id_a, 'perfume_id_a');
    var b = asInt(record.perfume_id_b, 'perfume_id_b');
    if (a === b) fail('a combination needs two different perfumes (got ' + a + ' twice)');
    var applied = str(record.applied_first).toLowerCase();
    if (APPLIED_FIRST_VALUES.indexOf(applied) === -1) {
      fail('applied_first must be one of "", "a", "b", got "' + record.applied_first + '"');
    }
    /* Canonical ascending order, with applied_first following the swap so it keeps
     * pointing at the same physical perfume. */
    if (a > b) {
      var t = a; a = b; b = t;
      applied = applied === 'a' ? 'b' : applied === 'b' ? 'a' : applied;
    }
    return {
      combination_id: record.combination_id,
      perfume_id_a: a,
      perfume_id_b: b,
      applied_first: applied,
      comment: str(record.comment),
      created_at: str(record.created_at)
    };
  }

  function validateRatingEvent(record) {
    var subject = validateSubject(record.subject_type || SUBJECT_PERFUME, record.subject_id);
    return {
      rating_id: record.rating_id,
      subject_type: subject.subject_type,
      subject_id: subject.subject_id,
      rating: validateRating(record.rating),
      context: str(record.context),
      rated_at: str(record.rated_at)
    };
  }

  function optionalNumber(value) {
    if (value === '' || value === null || value === undefined) return null;
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function validatePrediction(record) {
    var subject = validateSubject(record.subject_type || SUBJECT_PERFUME, record.subject_id);
    var value = Number(record.value);
    if (!isFinite(value)) fail('prediction "' + record.value + '" is not a number');
    return {
      prediction_id: record.prediction_id,
      subject_type: subject.subject_type,
      subject_id: subject.subject_id,
      value: Math.round(value * 1000) / 1000,
      interval_low: optionalNumber(record.interval_low),
      interval_high: optionalNumber(record.interval_high),
      model_version: str(record.model_version),
      spec_hash: str(record.spec_hash),
      predicted_at: str(record.predicted_at)
    };
  }

  function validatePreference(record) {
    var scope = str(record.scope).toLowerCase();
    if (PREFERENCE_SCOPES.indexOf(scope) === -1) {
      fail('scope must be one of ' + PREFERENCE_SCOPES.join(', ') + ', got "' + record.scope + '"');
    }
    var key = str(record.key);
    if (!key) fail('a preference needs a key');
    var weight = Number(record.weight);
    if (record.weight === '' || record.weight === null || record.weight === undefined ||
        !isFinite(weight)) {
      fail('weight "' + record.weight + '" is not a number');
    }
    if (weight < MIN_WEIGHT || weight > MAX_WEIGHT) {
      fail('weight ' + weight + ' outside ' + MIN_WEIGHT + '–' + MAX_WEIGHT +
        '. This is a signed affinity, not a 1–5 rating.');
    }
    /* Families and axes come from a closed vocabulary; notes and mediums are open,
     * because the point of declaring a note is often that it is unusual. */
    if (scope === SCOPE_FAMILY && FAMILIES.indexOf(key.toLowerCase()) === -1) {
      fail('unknown family "' + key + '"');
    }
    if (scope === SCOPE_AXIS && AXES.indexOf(key.toLowerCase()) === -1) {
      fail('unknown axis "' + key + '"');
    }
    return {
      preference_id: record.preference_id,
      scope: scope,
      key: (scope === SCOPE_FAMILY || scope === SCOPE_AXIS) ? key.toLowerCase() : key,
      weight: Math.round(weight * 1000) / 1000,
      declared_at: str(record.declared_at)
    };
  }

  var VALIDATORS = {
    perfumes: validatePerfume,
    combinations: validateCombination,
    ratings: validateRatingEvent,
    predictions: validatePrediction,
    preferences: validatePreference
  };

  /* ── row conversion (must match the Python to_row / from_row exactly) ─────── */

  function num(value) {
    /* Match Python's "%g": integral floats lose the trailing ".0". */
    return value === null || value === undefined || value === '' ? '' : String(value);
  }

  var TO_ROW = {
    perfumes: function (p) {
      return {
        perfume_id: String(p.perfume_id), brand: p.brand, name: p.name,
        perfume_type: p.perfume_type, perfume_medium: p.perfume_medium,
        perfume_strength: p.perfume_strength,
        top_notes: formatNotes(p.top_notes),
        middle_notes: formatNotes(p.middle_notes),
        base_notes: formatNotes(p.base_notes),
        comment: p.comment, logged_at: p.logged_at
      };
    },
    combinations: function (c) {
      return {
        combination_id: String(c.combination_id),
        perfume_id_a: String(c.perfume_id_a), perfume_id_b: String(c.perfume_id_b),
        applied_first: c.applied_first, comment: c.comment, created_at: c.created_at
      };
    },
    ratings: function (r) {
      return {
        rating_id: String(r.rating_id), subject_type: r.subject_type,
        subject_id: String(r.subject_id), rating: num(r.rating),
        context: r.context, rated_at: r.rated_at
      };
    },
    predictions: function (p) {
      return {
        prediction_id: String(p.prediction_id), subject_type: p.subject_type,
        subject_id: String(p.subject_id), value: num(p.value),
        interval_low: num(p.interval_low), interval_high: num(p.interval_high),
        model_version: p.model_version, spec_hash: p.spec_hash,
        predicted_at: p.predicted_at
      };
    },
    preferences: function (p) {
      return {
        preference_id: String(p.preference_id), scope: p.scope, key: p.key,
        weight: num(p.weight), declared_at: p.declared_at
      };
    }
  };

  var FROM_ROW = {
    perfumes: function (row) {
      return {
        perfume_id: asInt(row.perfume_id, 'perfume_id'),
        brand: row.brand, name: row.name, perfume_type: row.perfume_type,
        perfume_medium: row.perfume_medium, perfume_strength: row.perfume_strength,
        top_notes: parseNotes(row.top_notes),
        middle_notes: parseNotes(row.middle_notes),
        base_notes: parseNotes(row.base_notes),
        comment: row.comment || '', logged_at: row.logged_at || ''
      };
    },
    combinations: function (row) {
      return {
        combination_id: asInt(row.combination_id, 'combination_id'),
        perfume_id_a: row.perfume_id_a, perfume_id_b: row.perfume_id_b,
        applied_first: row.applied_first || '', comment: row.comment || '',
        created_at: row.created_at || ''
      };
    },
    ratings: function (row) {
      return {
        rating_id: asInt(row.rating_id, 'rating_id'),
        subject_type: row.subject_type || SUBJECT_PERFUME,
        subject_id: row.subject_id, rating: row.rating,
        context: row.context || '', rated_at: row.rated_at || ''
      };
    },
    predictions: function (row) {
      return {
        prediction_id: asInt(row.prediction_id, 'prediction_id'),
        subject_type: row.subject_type || SUBJECT_PERFUME,
        subject_id: row.subject_id, value: row.value,
        interval_low: row.interval_low, interval_high: row.interval_high,
        model_version: row.model_version || '', spec_hash: row.spec_hash || '',
        predicted_at: row.predicted_at || ''
      };
    },
    preferences: function (row) {
      return {
        preference_id: asInt(row.preference_id, 'preference_id'),
        scope: row.scope || '', key: row.key || '', weight: row.weight,
        declared_at: row.declared_at || ''
      };
    }
  };

  /* ── persistence ─────────────────────────────────────────────────────────── */

  function emptyState() {
    return {
      schema_version: SCHEMA_VERSION,
      perfumes: [], combinations: [], ratings: [], predictions: [], preferences: []
    };
  }

  var state = null;

  function load() {
    if (state) return state;
    state = emptyState();

    var raw = null, upgradedFrom = null;
    try {
      raw = root.localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        /* Nothing at the current key — look for an older one and upgrade it. */
        for (var i = 0; i < LEGACY_STORAGE_KEYS.length; i++) {
          var legacy = root.localStorage.getItem(LEGACY_STORAGE_KEYS[i]);
          if (legacy) { raw = legacy; upgradedFrom = LEGACY_STORAGE_KEYS[i]; break; }
        }
      }
    } catch (err) {
      /* Private browsing can deny storage entirely. The app still works for the
       * session; the backup buttons are the way out. */
      console.warn('Sillage: localStorage unavailable, running in memory only', err);
      return state;
    }
    if (!raw) return state;

    try {
      var parsed = JSON.parse(raw);
      var version = parsed && parsed.schema_version;
      if (version === SCHEMA_VERSION || UPGRADABLE_FROM.indexOf(version) !== -1) {
        IMPORT_ORDER.forEach(function (table) {
          if (Array.isArray(parsed[table])) state[table] = parsed[table];
        });
        if (version !== SCHEMA_VERSION) {
          /* Additive upgrade: tables absent in the old version stay empty. Persist
           * under the new key so this happens once, and leave the old key alone as
           * an accidental backup. */
          console.warn('Sillage: upgrading log from schema v' + version +
            ' to v' + SCHEMA_VERSION + (upgradedFrom ? ' (' + upgradedFrom + ')' : ''));
          save();
        }
      } else {
        console.warn('Sillage: ignoring log written by schema v' + version +
          ', this build speaks v' + SCHEMA_VERSION);
      }
    } catch (err) {
      console.error('Sillage: stored log is not readable JSON, starting empty', err);
    }
    return state;
  }

  function save() {
    try {
      root.localStorage.setItem(STORAGE_KEY, JSON.stringify(load()));
      return true;
    } catch (err) {
      console.error('Sillage: could not persist the log', err);
      return false;
    }
  }

  function nextId(table) {
    var idField = TABLES[table].id, highest = 0;
    load()[table].forEach(function (record) {
      var value = Number(record[idField]);
      if (isFinite(value) && value > highest) highest = value;
    });
    return highest + 1; /* max + 1, never length + 1 */
  }

  /* ── writes ──────────────────────────────────────────────────────────────── */

  function logPerfume(input) {
    var record = validatePerfume(input);
    record.perfume_id = nextId('perfumes');
    record.logged_at = record.logged_at || nowStamp();
    load().perfumes.push(record);
    save();
    return record;
  }

  function logCombination(perfumeIdA, perfumeIdB, appliedFirst, comment) {
    var record = validateCombination({
      perfume_id_a: perfumeIdA, perfume_id_b: perfumeIdB,
      applied_first: appliedFirst || '', comment: comment || ''
    });
    var known = perfumesById();
    [record.perfume_id_a, record.perfume_id_b].forEach(function (id) {
      if (!known[id]) fail('no perfume with id ' + id);
    });
    var existing = load().combinations.filter(function (c) {
      return c.perfume_id_a === record.perfume_id_a &&
        c.perfume_id_b === record.perfume_id_b &&
        c.applied_first === record.applied_first;
    })[0];
    if (existing) return existing;

    record.combination_id = nextId('combinations');
    record.created_at = nowStamp();
    load().combinations.push(record);
    save();
    return record;
  }

  /* logRating({perfumeId, rating}) or logRating({combinationId, rating}) */
  function logRating(options) {
    var opts = options || {};
    var hasPerfume = opts.perfumeId !== undefined && opts.perfumeId !== null;
    var hasCombination = opts.combinationId !== undefined && opts.combinationId !== null;
    if (hasPerfume === hasCombination) fail('pass exactly one of perfumeId or combinationId');

    var subjectType, subjectId;
    if (hasPerfume) {
      subjectType = SUBJECT_PERFUME;
      subjectId = asInt(opts.perfumeId, 'perfumeId');
      if (!perfumesById()[subjectId]) fail('no perfume with id ' + subjectId);
    } else {
      subjectType = SUBJECT_COMBINATION;
      subjectId = asInt(opts.combinationId, 'combinationId');
      if (!combinationsById()[subjectId]) fail('no combination with id ' + subjectId);
    }

    var record = validateRatingEvent({
      subject_type: subjectType, subject_id: subjectId,
      rating: opts.rating, context: opts.context || '', rated_at: opts.ratedAt || ''
    });
    record.rating_id = nextId('ratings');
    record.rated_at = record.rated_at || nowStamp();
    load().ratings.push(record);
    save();
    return record;
  }

  function logPrediction(options) {
    var opts = options || {};
    var record = validatePrediction({
      subject_type: opts.subjectType || SUBJECT_PERFUME,
      subject_id: opts.subjectId,
      value: opts.value,
      interval_low: opts.intervalLow === undefined ? null : opts.intervalLow,
      interval_high: opts.intervalHigh === undefined ? null : opts.intervalHigh,
      model_version: opts.modelVersion || '',
      spec_hash: opts.specHash || ''
    });
    record.prediction_id = nextId('predictions');
    record.predicted_at = nowStamp();
    load().predictions.push(record);
    save();
    return record;
  }

  /* Declare a taste preference. Optional by design — an empty palette is a valid,
   * complete answer, and the model then starts from the neutral prior instead.
   * Declaring the same key again appends rather than edits, so a change of mind is
   * recorded; a weight of exactly 0 retracts. */
  function logPreference(scope, key, weight, declaredAt) {
    var record = validatePreference({
      scope: scope, key: key, weight: weight, declared_at: declaredAt || ''
    });
    record.preference_id = nextId('preferences');
    record.declared_at = record.declared_at || nowStamp();
    load().preferences.push(record);
    save();
    return record;
  }

  /* Write several declarations at once, skipping ones that would not change the
   * current palette. Returns the records actually written. */
  function setPalette(entries) {
    var current = palette(true), written = [];
    (entries || []).forEach(function (entry) {
      var scope = str(entry.scope).toLowerCase();
      var key = str(entry.key);
      var lookupKey = (scope === SCOPE_FAMILY || scope === SCOPE_AXIS)
        ? key.toLowerCase() : key;
      var existing = (current[scope] || {})[lookupKey];
      var weight = Number(entry.weight);
      /* An unset key and an explicit 0 are the same thing to the model, so don't
       * write a retraction for something never declared. */
      if (existing === undefined && weight === 0) return;
      if (existing !== undefined && Math.abs(existing - weight) < 1e-9) return;
      written.push(logPreference(scope, key, weight));
    });
    return written;
  }

  /* ── reads ───────────────────────────────────────────────────────────────── */

  function perfumes() { return load().perfumes.slice(); }
  function combinations() { return load().combinations.slice(); }

  function perfumesById() {
    var out = Object.create(null);
    load().perfumes.forEach(function (p) { out[p.perfume_id] = p; });
    return out;
  }

  function combinationsById() {
    var out = Object.create(null);
    load().combinations.forEach(function (c) { out[c.combination_id] = c; });
    return out;
  }

  function ratingEvents(subjectType) {
    var events = load().ratings.slice();
    if (subjectType) {
      events = events.filter(function (e) { return e.subject_type === subjectType; });
    }
    events.sort(function (a, b) {
      if (a.rated_at !== b.rated_at) return a.rated_at < b.rated_at ? -1 : 1;
      return (a.rating_id || 0) - (b.rating_id || 0);
    });
    return events;
  }

  function predictions(subjectType) {
    var records = load().predictions.slice();
    return subjectType
      ? records.filter(function (p) { return p.subject_type === subjectType; })
      : records;
  }

  /* Latest rating per subject — later events supersede earlier ones. */
  function currentRatings(subjectType) {
    var latest = Object.create(null);
    ratingEvents(subjectType || SUBJECT_PERFUME).forEach(function (event) {
      latest[event.subject_id] = event;
    });
    return latest;
  }

  /* Rated perfumes, chronological, latest rating each. Single perfumes only:
   * layered pairs are a different target and would contaminate the fit. */
  function trainingSet() {
    var byId = perfumesById(), rows = [];
    ratingEvents(SUBJECT_PERFUME).forEach(function (event) {
      var perfume = byId[event.subject_id];
      if (perfume) rows.push({ perfume: perfume, rating: event.rating, id: event.subject_id });
    });
    var seen = Object.create(null), out = [];
    for (var i = rows.length - 1; i >= 0; i--) {
      if (seen[rows[i].id]) continue;
      seen[rows[i].id] = true;
      out.push({ perfume: rows[i].perfume, rating: rows[i].rating });
    }
    return out.reverse();
  }

  function pairTrainingSet() {
    var byId = perfumesById(), combos = combinationsById(), rows = [];
    ratingEvents(SUBJECT_COMBINATION).forEach(function (event) {
      var combo = combos[event.subject_id];
      if (!combo) return;
      var a = byId[combo.perfume_id_a], b = byId[combo.perfume_id_b];
      if (!a || !b) return;
      rows.push({ combination: combo, a: a, b: b, rating: event.rating, id: event.subject_id });
    });
    var seen = Object.create(null), out = [];
    for (var i = rows.length - 1; i >= 0; i--) {
      if (seen[rows[i].id]) continue;
      seen[rows[i].id] = true;
      out.push(rows[i]);
    }
    return out.reverse();
  }

  function preferenceEvents() {
    var events = load().preferences.slice();
    events.sort(function (a, b) {
      if (a.declared_at !== b.declared_at) return a.declared_at < b.declared_at ? -1 : 1;
      return (a.preference_id || 0) - (b.preference_id || 0);
    });
    return events;
  }

  /* The declared smell palette: {scope: {key: weight}}. Latest declaration per
   * (scope, key) wins. Retractions drop out unless asked for, so an empty palette
   * and a fully-retracted one look identical to the model — correct, since both
   * mean "no prior". */
  function palette(includeRetracted) {
    var latest = Object.create(null);
    preferenceEvents().forEach(function (event) {
      latest[event.scope + '\u0000' + event.key] = event;
    });
    var out = {};
    Object.keys(latest).forEach(function (composite) {
      var event = latest[composite];
      if (event.weight === 0 && !includeRetracted) return;
      if (!out[event.scope]) out[event.scope] = {};
      out[event.scope][event.key] = event.weight;
    });
    return out;
  }

  function hasPalette() {
    var current = palette();
    return Object.keys(current).some(function (scope) {
      return Object.keys(current[scope]).length > 0;
    });
  }

  function unrated() {
    var rated = currentRatings(SUBJECT_PERFUME);
    return perfumes().filter(function (p) { return !rated[p.perfume_id]; });
  }

  /* Latest prediction per subject, for showing predicted-vs-actual. */
  function latestPredictions(subjectType) {
    var latest = Object.create(null);
    predictions(subjectType || SUBJECT_PERFUME).forEach(function (p) {
      var seen = latest[p.subject_id];
      if (!seen || (p.predicted_at || '') >= (seen.predicted_at || '')) latest[p.subject_id] = p;
    });
    return latest;
  }

  /* True when no table holds anything. Used to decide whether a backup is worth
   * offering — deliberately not "has the user logged a perfume", because a declared
   * palette with no perfumes yet is the normal onboarding state and is absolutely
   * worth backing up. */
  function isEmpty() {
    var current = load();
    return IMPORT_ORDER.every(function (table) { return current[table].length === 0; });
  }

  function summary() {
    var training = trainingSet();
    var ratings = training.map(function (t) { return t.rating; });
    var noteSet = Object.create(null);
    perfumes().forEach(function (p) {
      p.top_notes.concat(p.middle_notes, p.base_notes).forEach(function (n) {
        noteSet[n.toLowerCase()] = true;
      });
    });
    var groups = Object.create(null);
    training.forEach(function (t) {
      groups[groupKey(t.perfume.brand, t.perfume.name) || 'id:' + t.perfume.perfume_id] = true;
    });
    return {
      schema_version: SCHEMA_VERSION,
      perfumes: load().perfumes.length,
      rated: training.length,
      unrated: load().perfumes.length - training.length,
      rating_events: load().ratings.length,
      distinct_groups: Object.keys(groups).length,
      combinations: load().combinations.length,
      rated_combinations: pairTrainingSet().length,
      predictions_logged: load().predictions.length,
      declared_preferences: Object.keys(palette()).reduce(function (total, scope) {
        return total + Object.keys(palette()[scope]).length;
      }, 0),
      preference_events: load().preferences.length,
      mean_rating: ratings.length
        ? Math.round(ratings.reduce(function (a, b) { return a + b; }, 0) / ratings.length * 1000) / 1000
        : null,
      distinct_notes: Object.keys(noteSet).length
    };
  }

  /* ── export ──────────────────────────────────────────────────────────────── */

  /* All five files every time, headers included even when empty: a backup that
   * silently omits ratings.csv is indistinguishable from one where you had no
   * ratings. */
  function exportFiles() {
    return IMPORT_ORDER.map(function (table) {
      var spec = TABLES[table];
      var rows = load()[table].map(TO_ROW[table]);
      return { table: table, name: spec.file, text: Csv.toCsv(spec.fields, rows), rows: rows.length };
    });
  }

  /* ── import ──────────────────────────────────────────────────────────────── */

  /* Identify by header row, not filename — a browser download renamed to
   * "perfumes (1).csv" still lands in the right table. */
  function identify(header) {
    var found = (header || []).map(function (h) { return String(h).trim(); }).sort().join('|');
    for (var table in TABLES) {
      if (TABLES[table].fields.slice().sort().join('|') === found) return table;
    }
    return null;
  }

  function referentialProblem(table, record, known) {
    if (table === 'combinations') {
      var missing = [record.perfume_id_a, record.perfume_id_b].filter(function (id) {
        return !known.perfumes[id];
      });
      return missing.length ? 'member perfume ' + missing[0] + ' not in the import' : null;
    }
    if (table === 'ratings' || table === 'predictions') {
      var bucket = record.subject_type === SUBJECT_PERFUME ? 'perfumes' : 'combinations';
      if (!known[bucket][record.subject_id]) {
        return record.subject_type + ' ' + record.subject_id + ' not in the import';
      }
    }
    return null;
  }

  /* files: [{name, text}]. Returns a report; nothing is written unless apply. */
  function prepareImport(files) {
    var report = { tables: {}, unrecognised: [], accepted: 0, rejected: 0, staged: {} };
    var collected = {};

    (files || []).forEach(function (file) {
      var parsed = Csv.parseObjects(file.text);
      var table = identify(parsed.header);
      if (!table) { report.unrecognised.push(file.name); return; }
      collected[table] = (collected[table] || []).concat(parsed.rows);
    });

    var known = { perfumes: Object.create(null), combinations: Object.create(null) };

    IMPORT_ORDER.forEach(function (table) {
      if (!collected[table]) return;
      var spec = TABLES[table], entry = { accepted: 0, rejected: [] }, seen = Object.create(null);
      var out = [];

      collected[table].forEach(function (row, index) {
        var record;
        try {
          record = VALIDATORS[table](FROM_ROW[table](row));
        } catch (err) {
          entry.rejected.push(table + ' line ' + (index + 2) + ': ' + err.message);
          return;
        }
        var recordId = record[spec.id];
        if (seen[recordId]) {
          entry.rejected.push(table + ' line ' + (index + 2) + ': duplicate ' + spec.id + ' ' + recordId);
          return;
        }
        var problem = referentialProblem(table, record, known);
        if (problem) {
          entry.rejected.push(table + ' line ' + (index + 2) + ': ' + problem);
          return;
        }
        seen[recordId] = true;
        if (known[table]) known[table][recordId] = true;
        out.push(record);
      });

      entry.accepted = out.length;
      report.tables[table] = entry;
      report.staged[table] = out;
      report.accepted += entry.accepted;
      report.rejected += entry.rejected.length;
    });

    return report;
  }

  /* Replace-only, on purpose. Merging two logs needs id remapping, and a
   * half-correct remap silently attaches ratings to the wrong perfume — the exact
   * class of bug this schema exists to prevent. */
  function applyImport(report) {
    var next = emptyState();
    IMPORT_ORDER.forEach(function (table) {
      if (report.staged[table]) next[table] = report.staged[table];
    });
    state = next;
    save();
    return summary();
  }

  function reset() {
    state = emptyState();
    save();
  }

  /* ── deletion ────────────────────────────────────────────────────────────────
   * The log is append-only for *changes* (a re-rating is a new event), but a
   * whole entry can be removed. Deletes cascade so nothing is left pointing at a
   * record that no longer exists: a rating or prediction for a deleted subject
   * would otherwise be an orphan that silently skews summaries and exports.
   *
   * Ids are max + 1, so deleting the newest perfume frees its id for the next one.
   * That is safe because the cascade has removed every reference to it. */

  function isAbout(record, subjectType, ids) {
    return record.subject_type === subjectType && ids[record.subject_id];
  }

  /* What deleting this perfume would also remove — for the confirm dialog. */
  function deletionImpact(perfumeId) {
    var id = asInt(perfumeId, 'perfumeId');
    var pairs = load().combinations.filter(function (c) {
      return c.perfume_id_a === id || c.perfume_id_b === id;
    });
    var ratings = ratingEvents(SUBJECT_PERFUME).filter(function (r) { return r.subject_id === id; });
    return { pairs: pairs.length, ratings: ratings.length };
  }

  function removeSubjects(current, subjectType, ids) {
    var before = { ratings: current.ratings.length, predictions: current.predictions.length };
    current.ratings = current.ratings.filter(function (r) { return !isAbout(r, subjectType, ids); });
    current.predictions = current.predictions.filter(function (p) { return !isAbout(p, subjectType, ids); });
    return {
      ratings: before.ratings - current.ratings.length,
      predictions: before.predictions - current.predictions.length
    };
  }

  function deleteCombination(combinationId) {
    var id = asInt(combinationId, 'combinationId');
    var current = load();
    if (!combinationsById()[id]) fail('no combination with id ' + id);
    var ids = Object.create(null); ids[id] = true;
    current.combinations = current.combinations.filter(function (c) { return c.combination_id !== id; });
    var removed = removeSubjects(current, SUBJECT_COMBINATION, ids);
    save();
    return { combinations: 1, ratings: removed.ratings, predictions: removed.predictions };
  }

  function deletePerfume(perfumeId) {
    var id = asInt(perfumeId, 'perfumeId');
    var current = load();
    if (!perfumesById()[id]) fail('no perfume with id ' + id);

    /* Pairs containing it go too, with their own ratings and predictions. */
    var pairIds = Object.create(null), pairCount = 0;
    current.combinations.forEach(function (c) {
      if (c.perfume_id_a === id || c.perfume_id_b === id) { pairIds[c.combination_id] = true; pairCount++; }
    });
    current.combinations = current.combinations.filter(function (c) { return !pairIds[c.combination_id]; });
    var pairRemoved = removeSubjects(current, SUBJECT_COMBINATION, pairIds);

    var self = Object.create(null); self[id] = true;
    current.perfumes = current.perfumes.filter(function (p) { return p.perfume_id !== id; });
    var removed = removeSubjects(current, SUBJECT_PERFUME, self);
    save();
    return {
      perfumes: 1,
      combinations: pairCount,
      ratings: removed.ratings + pairRemoved.ratings,
      predictions: removed.predictions + pairRemoved.predictions
    };
  }

  /* For undo: a deep copy of the whole log, and a way to put it back. */
  function snapshot() { return JSON.parse(JSON.stringify(load())); }

  function restoreSnapshot(snap) {
    if (!snap || snap.schema_version !== SCHEMA_VERSION) fail('not a snapshot from this log');
    state = JSON.parse(JSON.stringify(snap));
    save();
    return summary();
  }

  Sillage.Store = {
    SCHEMA_VERSION: SCHEMA_VERSION,
    SUBJECT_PERFUME: SUBJECT_PERFUME,
    SUBJECT_COMBINATION: SUBJECT_COMBINATION,
    PREFERENCE_LEVELS: PREFERENCE_LEVELS,
    PREFERENCE_SCOPES: PREFERENCE_SCOPES,
    FAMILIES: FAMILIES,
    AXES: AXES,
    MIN_RATING: MIN_RATING,
    MAX_RATING: MAX_RATING,
    RATING_STEP: RATING_STEP,
    ValidationError: ValidationError,
    nowStamp: nowStamp,
    groupKey: groupKey,
    parseNotes: parseNotes,
    formatNotes: formatNotes,
    cleanFamilies: cleanFamilies,
    perfumeTypes: perfumeTypes,
    cleanNotes: cleanNotes,
    logPerfume: logPerfume,
    logCombination: logCombination,
    logRating: logRating,
    logPrediction: logPrediction,
    logPreference: logPreference,
    setPalette: setPalette,
    preferenceEvents: preferenceEvents,
    palette: palette,
    hasPalette: hasPalette,
    isEmpty: isEmpty,
    perfumes: perfumes,
    perfumesById: perfumesById,
    combinations: combinations,
    combinationsById: combinationsById,
    ratingEvents: ratingEvents,
    currentRatings: currentRatings,
    predictions: predictions,
    latestPredictions: latestPredictions,
    trainingSet: trainingSet,
    pairTrainingSet: pairTrainingSet,
    unrated: unrated,
    summary: summary,
    exportFiles: exportFiles,
    identify: identify,
    prepareImport: prepareImport,
    applyImport: applyImport,
    reset: reset,
    deletePerfume: deletePerfume,
    deleteCombination: deleteCombination,
    deletionImpact: deletionImpact,
    snapshot: snapshot,
    restoreSnapshot: restoreSnapshot
  };
})(window);
