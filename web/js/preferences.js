/* The preferences page — the user's optional declared smell palette.
 *
 * This is the onboarding prior from ARCHITECTURE.md §4.3, and the honest version of
 * the one good idea in the legacy engine. The old app hardcoded a liked/disliked
 * note list and treated it as permanent fact; here the same information is a *prior*
 * that evidence overrides. Precedence in the scorer is
 *
 *     what you rated  >  what you declared  >  the shipped default list
 *
 * Everything here is optional. An empty palette is a valid, complete answer — the
 * page says so, and nothing gates on filling it in. That matters because a
 * half-remembered guess entered under pressure is worse than no prior at all.
 *
 * Declarations are append-only events, so changing your mind is recorded rather than
 * erased, and "neutral" is a real value that retracts an earlier declaration.
 *
 * Exposed as window.Sillage.Preferences.
 */
(function (root, document) {
  'use strict';

  var Sillage = root.Sillage = root.Sillage || {};
  var Store = Sillage.Store;

  /* Ordered worst-to-best so the control reads like a scale. */
  var LEVELS = [
    { key: 'avoid', weight: -1.0, label: 'Avoid', title: 'Actively puts me off' },
    { key: 'dislike', weight: -0.5, label: 'Dislike', title: 'I would rather not' },
    { key: 'neutral', weight: 0.0, label: '–', title: 'No opinion (clears a declaration)' },
    { key: 'like', weight: 0.5, label: 'Like', title: 'I enjoy this' },
    { key: 'love', weight: 1.0, label: 'Love', title: 'I seek this out' }
  ];

  var FAMILY_HINTS = {
    citrus: 'bergamot, lemon, grapefruit',
    floral: 'rose, peony, iris',
    'white-floral': 'jasmine, tuberose, gardenia',
    fruity: 'fig, pear, blackcurrant',
    green: 'violet leaf, galbanum, grass',
    aromatic: 'lavender, sage, rosemary',
    spicy: 'pink pepper, cardamom, cinnamon',
    woody: 'sandalwood, cedar, vetiver',
    resinous: 'incense, myrrh, elemi',
    amber: 'labdanum, benzoin, vanilla-amber',
    gourmand: 'vanilla, tonka, praline',
    musk: 'white musk, ambrette',
    animalic: 'civet, castoreum, cumin',
    aquatic: 'sea salt, calone, ozone'
  };

  var AXIS_HINTS = {
    sweet: 'sugar, syrup, candied',
    fresh: 'clean, airy, just-showered',
    warm: 'skin-close, cosy',
    woody: 'dry, sawn timber',
    green: 'crushed stems, sap',
    spicy: 'peppery, hot',
    animalic: 'skin, fur, sweat',
    powdery: 'iris, talc, makeup'
  };

  /* Same list the log form offers, so declared mediums match logged ones exactly. */
  var MEDIUMS = [
    'Spray Perfume (EDP)',
    'Spray Perfume (EDT)',
    'Spray Perfume (EDC)',
    'Extrait de Parfum',
    'Attar / Oil',
    'Solid Perfume',
    'Body Mist'
  ];

  var noteTags = { love: [], avoid: [] };
  var onChangeCallback = null;

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function levelForWeight(weight) {
    if (weight === undefined || weight === null) return 'neutral';
    var best = LEVELS[2];
    LEVELS.forEach(function (level) {
      if (Math.abs(level.weight - weight) < Math.abs(best.weight - weight)) best = level;
    });
    return best.key;
  }

  /* ── the five-way control ─────────────────────────────────────────────────── */

  function segmentedControl(scope, key, current, hint) {
    var name = 'pref-' + scope + '-' + key.replace(/[^a-z0-9-]/gi, '_');
    var selected = levelForWeight(current);
    var options = LEVELS.map(function (level) {
      var id = name + '-' + level.key;
      return '<input type="radio" id="' + id + '" name="' + name + '"' +
        ' value="' + level.weight + '" data-scope="' + esc(scope) + '"' +
        ' data-key="' + esc(key) + '"' + (selected === level.key ? ' checked' : '') + '>' +
        '<label for="' + id + '" class="seg-' + level.key + '" title="' +
        esc(level.title) + '">' + esc(level.label) + '</label>';
    }).join('');

    return '<div class="palette-row">' +
      '<div class="palette-label"><span>' + esc(key) + '</span>' +
      (hint ? '<span class="palette-hint">' + esc(hint) + '</span>' : '') + '</div>' +
      '<fieldset class="segmented" aria-label="' + esc(key) + '">' + options + '</fieldset>' +
      '</div>';
  }

  /* ── rendering ────────────────────────────────────────────────────────────── */

  function renderFamilies(palette) {
    var declared = palette.family || {};
    $('paletteFamilies').innerHTML = Store.FAMILIES.map(function (family) {
      return segmentedControl('family', family, declared[family], FAMILY_HINTS[family]);
    }).join('');
  }

  function renderAxes(palette) {
    var declared = palette.axis || {};
    $('paletteAxes').innerHTML = Store.AXES.map(function (axis) {
      return segmentedControl('axis', axis, declared[axis], AXIS_HINTS[axis]);
    }).join('');
  }

  function renderMediums(palette) {
    var declared = palette.medium || {};
    $('paletteMediums').innerHTML = MEDIUMS.map(function (medium) {
      return segmentedControl('medium', medium, declared[medium], '');
    }).join('');
  }

  function renderNotes(palette) {
    var declared = palette.note || {};
    noteTags.love = [];
    noteTags.avoid = [];
    Object.keys(declared).forEach(function (note) {
      if (declared[note] > 0) noteTags.love.push(note);
      else if (declared[note] < 0) noteTags.avoid.push(note);
    });
    redrawNoteTags('love');
    redrawNoteTags('avoid');
  }

  function redrawNoteTags(bucket) {
    var wrap = $(bucket === 'love' ? 'lovedNotesWrap' : 'avoidedNotesWrap');
    var input = $(bucket === 'love' ? 'lovedNotesInput' : 'avoidedNotesInput');
    wrap.querySelectorAll('.tag').forEach(function (node) { node.remove(); });
    noteTags[bucket].forEach(function (value) {
      var tag = document.createElement('span');
      tag.className = 'tag ' + (bucket === 'love' ? 'tag-base' : 'tag-avoid');
      tag.textContent = value + ' ';
      var remove = document.createElement('span');
      remove.className = 'tag-remove';
      remove.setAttribute('role', 'button');
      remove.setAttribute('aria-label', 'Remove ' + value);
      remove.textContent = '×';
      remove.addEventListener('click', function () {
        noteTags[bucket] = noteTags[bucket].filter(function (t) { return t !== value; });
        redrawNoteTags(bucket);
        markDirty();
      });
      tag.appendChild(remove);
      wrap.insertBefore(tag, input);
    });
  }

  function makeNoteInput(bucket) {
    var input = $(bucket === 'love' ? 'lovedNotesInput' : 'avoidedNotesInput');
    var other = bucket === 'love' ? 'avoid' : 'love';

    function add(raw) {
      var value = String(raw).trim().replace(/,+$/, '').trim();
      if (!value) return;
      if (value.indexOf(';') !== -1) {
        report('Notes cannot contain a semicolon — it separates notes in the CSV.', true);
        return;
      }
      var lower = value.toLowerCase();
      /* A note cannot be both loved and avoided; the newer declaration wins. */
      noteTags[other] = noteTags[other].filter(function (t) {
        return t.toLowerCase() !== lower;
      });
      if (!noteTags[bucket].some(function (t) { return t.toLowerCase() === lower; })) {
        noteTags[bucket].push(value);
      }
      input.value = '';
      redrawNoteTags('love');
      redrawNoteTags('avoid');
      markDirty();
    }

    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        add(input.value);
      } else if (event.key === 'Backspace' && !input.value && noteTags[bucket].length) {
        noteTags[bucket].pop();
        redrawNoteTags(bucket);
        markDirty();
      }
    });
    input.addEventListener('blur', function () {
      if (input.value.trim()) add(input.value);
    });
    $(bucket === 'love' ? 'lovedNotesWrap' : 'avoidedNotesWrap')
      .addEventListener('click', function (event) {
        if (event.target === event.currentTarget) input.focus();
      });
  }

  /* ── state ────────────────────────────────────────────────────────────────── */

  var dirty = false;

  function markDirty() {
    dirty = true;
    $('savePaletteBtn').disabled = false;
    report('Unsaved changes.', false);
  }

  function report(message, isProblem) {
    var target = $('paletteStatus');
    target.textContent = message || '';
    target.className = 'palette-status' + (isProblem ? ' bad' : '');
  }

  function renderSummary() {
    var summary = Store.summary();
    var declared = summary.declared_preferences;
    $('paletteSummary').innerHTML = declared
      ? '<strong>' + declared + '</strong> declaration' + (declared === 1 ? '' : 's') +
        ' saved, across ' + summary.preference_events + ' event' +
        (summary.preference_events === 1 ? '' : 's') + '.'
      : 'Nothing declared. That is a complete answer — the model will start from ' +
        'neutral and learn only from what you rate.';
  }

  /* Read every control on the page into a flat list of declarations. */
  function collect() {
    var entries = [];
    document.querySelectorAll('#viewPreferences input[type="radio"]:checked')
      .forEach(function (input) {
        entries.push({
          scope: input.dataset.scope,
          key: input.dataset.key,
          weight: Number(input.value)
        });
      });

    /* Notes come from the tag inputs. Anything previously declared but no longer
     * listed is retracted, so removing a tag actually takes effect. */
    var previous = Store.palette().note || {};
    var listed = Object.create(null);
    noteTags.love.forEach(function (note) {
      listed[note.toLowerCase()] = true;
      entries.push({ scope: 'note', key: note, weight: 1.0 });
    });
    noteTags.avoid.forEach(function (note) {
      listed[note.toLowerCase()] = true;
      entries.push({ scope: 'note', key: note, weight: -1.0 });
    });
    Object.keys(previous).forEach(function (note) {
      if (!listed[note.toLowerCase()]) {
        entries.push({ scope: 'note', key: note, weight: 0.0 });
      }
    });
    return entries;
  }

  function save() {
    var written;
    try {
      written = Store.setPalette(collect());
    } catch (err) {
      report(err.message, true);
      return;
    }
    dirty = false;
    $('savePaletteBtn').disabled = true;
    renderSummary();
    report(
      written.length
        ? 'Saved ' + written.length + ' change' + (written.length === 1 ? '' : 's') + '.'
        : 'Nothing changed.',
      false
    );
    if (onChangeCallback) onChangeCallback();
  }

  function clearAll() {
    if (!Store.hasPalette()) {
      report('Nothing to clear.', false);
      return;
    }
    var ok = root.confirm(
      'This retracts every declaration in your palette. Your ratings are untouched, ' +
      'and the history stays in the log.\n\nClear it?');
    if (!ok) return;

    var palette = Store.palette();
    var entries = [];
    Object.keys(palette).forEach(function (scope) {
      Object.keys(palette[scope]).forEach(function (key) {
        entries.push({ scope: scope, key: key, weight: 0.0 });
      });
    });
    Store.setPalette(entries);
    render();
    report('Palette cleared.', false);
    if (onChangeCallback) onChangeCallback();
  }

  function render() {
    var palette = Store.palette();
    renderFamilies(palette);
    renderAxes(palette);
    renderMediums(palette);
    renderNotes(palette);
    renderSummary();
    dirty = false;
    $('savePaletteBtn').disabled = true;
    report('', false);
  }

  function init(onChange) {
    onChangeCallback = onChange || null;
    render();
    makeNoteInput('love');
    makeNoteInput('avoid');

    /* One listener for every segmented control, added once. */
    $('viewPreferences').addEventListener('change', function (event) {
      if (event.target && event.target.type === 'radio') markDirty();
    });
    $('savePaletteBtn').addEventListener('click', save);
    $('clearPaletteBtn').addEventListener('click', clearAll);
  }

  Sillage.Preferences = {
    init: init,
    render: render,
    MEDIUMS: MEDIUMS,
    isDirty: function () { return dirty; }
  };
})(window, document);
