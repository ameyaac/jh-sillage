/* DOM wiring: form, live prediction, log table, backup and restore.
 *
 * All persistence goes through Sillage.Store; all scoring through Sillage.Predict.
 * This file holds no schema knowledge and no scoring rules on purpose — when the
 * Python model is exported, only predict.js changes.
 *
 * Exposed as window.Sillage.App.
 */
(function (root, document) {
  'use strict';

  var Sillage = root.Sillage = root.Sillage || {};
  var Store = Sillage.Store;
  var Predict = Sillage.Predict;

  var tags = { top: [], mid: [], base: [] };
  var pendingImport = null;

  function $(id) { return document.getElementById(id); }

  /* User-entered text goes through this before it ever reaches innerHTML. The old
   * page interpolated brand and note names straight into the table markup. */
  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function showToast(message) {
    var toast = $('toast');
    toast.textContent = message;
    toast.classList.add('show');
    root.clearTimeout(showToast._timer);
    showToast._timer = root.setTimeout(function () { toast.classList.remove('show'); }, 3200);
  }

  /* ── theme ───────────────────────────────────────────────────────────────── */

  function initTheme() {
    var toggle = document.querySelector('[data-theme-toggle]');
    var rootEl = document.documentElement;
    var mode = root.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

    function icon(current) {
      if (!toggle) return;
      toggle.innerHTML = current === 'dark'
        ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>'
        : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
    }

    rootEl.setAttribute('data-theme', mode);
    icon(mode);
    if (toggle) {
      toggle.addEventListener('click', function () {
        mode = mode === 'dark' ? 'light' : 'dark';
        rootEl.setAttribute('data-theme', mode);
        icon(mode);
      });
    }
  }

  /* ── live prediction ─────────────────────────────────────────────────────── */

  function stars(score) {
    var full = Math.floor(score);
    var half = score - full >= 0.5 ? 1 : 0;
    var empty = 5 - full - half;
    return '★'.repeat(full) + (half ? '⯪' : '') + '☆'.repeat(Math.max(0, empty));
  }

  function currentPrediction() {
    return Predict.run(
      tags.top, tags.mid, tags.base,
      $('scentType').value,
      Store.trainingSet(),
      Store.palette(),          /* declared taste — a prior, never a training target */
      $('perfumeMedium').value
    );
  }

  function renderPrediction() {
    var result = currentPrediction();
    var display = $('scoreDisplay');
    var breakdownCard = $('breakdownCard');

    if (!result) {
      display.className = 'score-empty';
      display.textContent = 'Fill in notes to see your predicted score';
      breakdownCard.style.display = 'none';
      return;
    }

    var score = result.total;
    var caveats = [];
    if (result.ratingsUsed === 0) {
      caveats.push(result.declarationsUsed
        ? 'No ratings of your own yet — this is your declared palette plus the seed ' +
          'profile guessing, not a model of your taste.'
        : 'No ratings of your own yet, and no palette declared — this is the seed ' +
          'profile guessing. Set a base palette in Preferences, or just start rating.');
    } else if (result.ratingsUsed < 8) {
      caveats.push('Based on only ' + result.ratingsUsed + ' rating' +
        (result.ratingsUsed === 1 ? '' : 's') + ' of yours. Treat it as a rough guess.');
    }
    if (result.unknownNotes.length) {
      caveats.push('Unrecognised: ' + result.unknownNotes.slice(0, 4).join(', ') +
        (result.unknownNotes.length > 4 ? ' and ' + (result.unknownNotes.length - 4) + ' more' : '') +
        ' — they contributed nothing to this score.');
    }

    display.className = 'score-display';
    display.innerHTML =
      '<div class="score-value">' + score.toFixed(1) + '</div>' +
      '<div class="score-label">out of 5.0</div>' +
      '<div class="score-stars">' + stars(score) + '</div>' +
      '<div class="score-verdict">"' + esc(Predict.verdict(score)) + '"</div>' +
      caveats.map(function (c) { return '<p class="score-caveat">' + esc(c) + '</p>'; }).join('');

    var entries = Object.keys(result.breakdown);
    if (!entries.length) { breakdownCard.style.display = 'none'; return; }
    $('breakdown').innerHTML = entries.map(function (label) {
      var value = result.breakdown[label];
      return '<div class="breakdown-row">' +
        '<span class="breakdown-label">' + esc(label) + '</span>' +
        '<span class="breakdown-bar"><span class="breakdown-fill" style="width:' +
        (value / 5 * 100).toFixed(0) + '%"></span></span>' +
        '<span class="breakdown-score">' + value.toFixed(1) + '</span></div>';
    }).join('');
    breakdownCard.style.display = 'block';
  }

  /* ── tag inputs ──────────────────────────────────────────────────────────── */

  function makeTagInput(inputId, wrapId, layer) {
    var input = $(inputId), wrap = $(wrapId);

    wrap.addEventListener('click', function (event) {
      if (event.target === wrap) input.focus();
    });

    function redraw() {
      wrap.querySelectorAll('.tag').forEach(function (node) { node.remove(); });
      tags[layer].forEach(function (value) {
        var tag = document.createElement('span');
        tag.className = 'tag tag-' + layer;
        tag.textContent = value + ' ';
        var remove = document.createElement('span');
        remove.className = 'tag-remove';
        remove.setAttribute('role', 'button');
        remove.setAttribute('aria-label', 'Remove ' + value);
        remove.textContent = '×';
        remove.addEventListener('click', function () {
          tags[layer] = tags[layer].filter(function (t) { return t !== value; });
          redraw();
          renderPrediction();
        });
        tag.appendChild(remove);
        wrap.insertBefore(tag, input);
      });
    }

    function addTag(raw) {
      var value = String(raw).trim().replace(/,+$/, '').trim();
      if (!value) return;
      if (value.indexOf(';') !== -1) {
        showToast('Notes cannot contain a semicolon — it separates notes in the CSV.');
        return;
      }
      value = value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
      var exists = tags[layer].some(function (t) { return t.toLowerCase() === value.toLowerCase(); });
      if (exists) { input.value = ''; return; }
      tags[layer].push(value);
      input.value = '';
      redraw();
      renderPrediction();
    }

    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        addTag(input.value);
      } else if (event.key === 'Backspace' && !input.value && tags[layer].length) {
        tags[layer].pop();
        redraw();
        renderPrediction();
      }
    });
    input.addEventListener('blur', function () {
      if (input.value.trim()) addTag(input.value);
    });

    makeTagInput['redraw_' + layer] = redraw;
  }

  function clearForm() {
    $('brandName').value = '';
    $('scentName').value = '';
    $('scentType').value = '';
    $('perfumeMedium').value = '';
    document.querySelectorAll('.scent-type-btn').forEach(function (b) {
      b.classList.remove('active');
    });
    ['top', 'mid', 'base'].forEach(function (layer) {
      tags[layer] = [];
      makeTagInput['redraw_' + layer]();
    });
    renderPrediction();
  }

  /* ── log table ───────────────────────────────────────────────────────────── */

  function badge(score) {
    if (score === null || score === undefined) {
      return '<span class="badge badge-unrated">Not rated</span>';
    }
    if (score >= 4.5) return '<span class="badge badge-love">Love it</span>';
    if (score >= 3.5) return '<span class="badge badge-like">Like it</span>';
    if (score >= 2.5) return '<span class="badge badge-neutral">Maybe</span>';
    return '<span class="badge badge-dislike">Skip</span>';
  }

  function ratingOptions(selected) {
    var options = ['<option value="">— rate it —</option>'];
    for (var value = Store.MAX_RATING; value >= Store.MIN_RATING - 1e-9; value -= Store.RATING_STEP) {
      var rounded = Math.round(value * 10) / 10;
      var isSelected = selected !== null && Math.abs(rounded - selected) < 1e-9;
      options.push('<option value="' + rounded + '"' + (isSelected ? ' selected' : '') + '>' +
        rounded.toFixed(1) + '</option>');
    }
    return options.join('');
  }

  function cell(value) {
    return value ? esc(value) : '<span class="cell-faint">—</span>';
  }

  function renderLog() {
    var perfumes = Store.perfumes();
    var ratings = Store.currentRatings(Store.SUBJECT_PERFUME);
    var predictions = Store.latestPredictions(Store.SUBJECT_PERFUME);
    var summary = Store.summary();
    var tbody = $('logBody');

    $('logCount').textContent = String(summary.perfumes);
    $('logStats').textContent = summary.perfumes
      ? summary.rated + ' rated · ' + summary.unrated + ' awaiting a rating · ' +
        summary.distinct_notes + ' distinct notes' +
        (summary.mean_rating === null ? '' : ' · your mean ' + summary.mean_rating.toFixed(2))
      : '';

    if (!perfumes.length) {
      tbody.innerHTML = '<tr><td colspan="11" class="empty-log">' +
        'No entries yet — log your first perfume above ✨</td></tr>';
      return;
    }

    tbody.innerHTML = perfumes.slice().reverse().map(function (p) {
      var rating = ratings[p.perfume_id] ? ratings[p.perfume_id].rating : null;
      var predicted = predictions[p.perfume_id] ? predictions[p.perfume_id].value : null;
      return '<tr>' +
        '<td>' + p.perfume_id + '</td>' +
        '<td>' + cell(p.brand) + '</td>' +
        '<td>' + cell(p.name) + '</td>' +
        '<td>' + cell(p.perfume_type) + '</td>' +
        '<td>' + cell(p.top_notes.join(', ')) + '</td>' +
        '<td>' + cell(p.middle_notes.join(', ')) + '</td>' +
        '<td>' + cell(p.base_notes.join(', ')) + '</td>' +
        '<td>' + (predicted === null ? '<span class="cell-faint">—</span>' :
          '<strong>' + predicted.toFixed(1) + '</strong>') + '</td>' +
        '<td><select class="rating-select' + (rating === null ? ' is-unrated' : '') +
          '" data-perfume-id="' + p.perfume_id + '" aria-label="Your rating for ' +
          esc(p.name || 'entry ' + p.perfume_id) + '">' + ratingOptions(rating) + '</select></td>' +
        '<td>' + badge(rating) + '</td>' +
        '<td>' + cell((p.logged_at || '').slice(0, 10)) + '</td>' +
        '</tr>';
    }).join('');

    tbody.querySelectorAll('.rating-select').forEach(function (select) {
      select.addEventListener('change', function () {
        if (!select.value) return; /* clearing a rating would mean deleting an event */
        try {
          Store.logRating({
            perfumeId: Number(select.dataset.perfumeId),
            rating: Number(select.value)
          });
        } catch (err) {
          showToast(err.message);
          return;
        }
        renderLog();
        renderPrediction();  /* a real rating changes what the model has learned */
        showToast('Rating saved. The model now has ' + Store.summary().rated + ' of your ratings.');
      });
    });
  }

  /* ── backup and restore ──────────────────────────────────────────────────── */

  function download(name, text) {
    var blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    root.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function downloadAll() {
    var files = Store.exportFiles();
    if (Store.isEmpty()) {
      showToast('Nothing to back up yet — log a perfume or declare a palette first.');
      return;
    }
    /* Browsers drop rapid successive downloads, so they are spaced out. All five
     * files are written even when empty: a partial backup is worse than none, and a
     * missing preferences.csv is indistinguishable from an empty palette. */
    files.forEach(function (file, index) {
      root.setTimeout(function () { download(file.name, file.text); }, index * 350);
    });
    showToast('Downloading ' + files.length + ' CSVs — keep them together.');
  }

  function renderImportReport(report) {
    var target = $('transferReport');
    if (!report) { target.innerHTML = ''; return; }

    var rows = Object.keys(report.tables).map(function (table) {
      var entry = report.tables[table];
      return '<li><span>' + esc(table) + '</span><span class="' +
        (entry.rejected.length ? 'bad' : 'ok') + '">' + entry.accepted + ' ok' +
        (entry.rejected.length ? ', ' + entry.rejected.length + ' rejected' : '') +
        '</span></li>';
    });

    var problems = Object.keys(report.tables).reduce(function (all, table) {
      return all.concat(report.tables[table].rejected);
    }, []).concat(report.unrecognised.map(function (name) {
      return 'unrecognised file: ' + name;
    }));

    if (!rows.length && !problems.length) {
      target.innerHTML = '<p class="bad">No recognisable Sillage CSVs in that selection.</p>';
      return;
    }

    target.innerHTML =
      '<p><strong>' + report.accepted + ' rows</strong> ready to load' +
      (report.rejected ? ', <span class="bad">' + report.rejected + ' rejected</span>' : '') + '.</p>' +
      '<ul>' + rows.join('') + '</ul>' +
      (problems.length
        ? '<details><summary>Why rows were rejected</summary><pre>' +
          esc(problems.join('\n')) + '</pre></details>'
        : '') +
      (report.accepted
        ? '<button type="button" class="btn-secondary" id="confirmImportBtn">' +
          'Replace my log with these ' + report.accepted + ' rows</button>'
        : '');

    var confirmBtn = $('confirmImportBtn');
    if (confirmBtn) confirmBtn.addEventListener('click', commitImport);
  }

  function commitImport() {
    if (!pendingImport) return;
    var existing = Store.summary();
    if (existing.perfumes) {
      var ok = root.confirm(
        'This replaces the ' + existing.perfumes + ' perfume(s) and ' +
        existing.rating_events + ' rating(s) currently in this browser with the ' +
        'uploaded files. Download a backup first if you are not sure.\n\nReplace now?');
      if (!ok) return;
    }
    var summary = Store.applyImport(pendingImport);
    pendingImport = null;
    renderImportReport(null);
    $('importInput').value = '';
    renderLog();
    renderPrediction();
    showToast('Restored ' + summary.perfumes + ' perfumes and ' +
      summary.rating_events + ' ratings.');
  }

  function readFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;

    Promise.all(files.map(function (file) {
      return new Promise(function (resolve) {
        var reader = new FileReader();
        reader.onload = function () { resolve({ name: file.name, text: String(reader.result) }); };
        reader.onerror = function () { resolve({ name: file.name, text: '' }); };
        reader.readAsText(file);
      });
    })).then(function (contents) {
      /* Validated first, applied only on confirmation — an import that silently
       * overwrote a log would be unrecoverable. */
      pendingImport = Store.prepareImport(contents);
      renderImportReport(pendingImport);
    });
  }

  function initTransfer() {
    $('downloadBtn').addEventListener('click', downloadAll);
    $('downloadAllBtn').addEventListener('click', downloadAll);
    /* Same full backup, not a palette-only file: a partial export that looked like a
     * backup would be the more dangerous thing to offer. */
    $('downloadPaletteBtn').addEventListener('click', downloadAll);

    var input = $('importInput');
    input.addEventListener('change', function () { readFiles(input.files); });

    var drop = $('fileDrop');
    ['dragenter', 'dragover'].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.add('is-over');
      });
    });
    ['dragleave', 'drop'].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.remove('is-over');
      });
    });
    drop.addEventListener('drop', function (event) {
      readFiles(event.dataTransfer && event.dataTransfer.files);
    });
  }

  /* ── form ────────────────────────────────────────────────────────────────── */

  function initForm() {
    makeTagInput('topInput', 'topWrap', 'top');
    makeTagInput('midInput', 'midWrap', 'mid');
    makeTagInput('baseInput', 'baseWrap', 'base');

    document.querySelectorAll('.scent-type-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var alreadyActive = btn.classList.contains('active');
        document.querySelectorAll('.scent-type-btn').forEach(function (b) {
          b.classList.remove('active');
        });
        if (alreadyActive) {
          $('scentType').value = '';
        } else {
          btn.classList.add('active');
          $('scentType').value = btn.dataset.value;
        }
        renderPrediction();
      });
    });

    $('perfumeMedium').addEventListener('change', renderPrediction);
    $('clearBtn').addEventListener('click', clearForm);

    $('perfumeForm').addEventListener('submit', function (event) {
      event.preventDefault();

      var result = currentPrediction();
      if (!result) {
        showToast('Add at least one note before saving.');
        return;
      }

      var perfume;
      try {
        perfume = Store.logPerfume({
          brand: $('brandName').value,
          name: $('scentName').value,
          perfume_type: $('scentType').value,
          perfume_medium: $('perfumeMedium').value,
          top_notes: tags.top,
          middle_notes: tags.mid,
          base_notes: tags.base
        });
      } catch (err) {
        showToast(err.message);
        return;
      }

      /* The prediction is recorded as a prediction — a separate table that no
       * training path reads. It exists so predicted-vs-actual stays honest. */
      Store.logPrediction({
        subjectId: perfume.perfume_id,
        value: result.total,
        modelVersion: result.modelVersion
      });

      renderLog();
      clearForm();
      showToast('Saved. Rate it in the log once you have actually worn it.');
    });
  }

  /* ── views ───────────────────────────────────────────────────────────────── */

  /* Tabs rather than separate HTML files, on purpose: localStorage is not reliably
   * shared between two documents on a file:// origin, and this page is meant to work
   * opened straight off disk. One document means one storage context. */
  function showView(name) {
    document.querySelectorAll('[data-view]').forEach(function (section) {
      section.hidden = section.dataset.view !== name;
    });
    document.querySelectorAll('[data-view-target]').forEach(function (button) {
      var active = button.dataset.viewTarget === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    if (name === 'preferences' && Sillage.Preferences) Sillage.Preferences.render();

    /* Without this you land mid-page when switching from a long log to a short
     * palette, which reads as "nothing happened". */
    if (showView._ready) root.scrollTo({ top: 0, behavior: 'smooth' });
    showView._ready = true;
  }

  function initNav() {
    document.querySelectorAll('[data-view-target]').forEach(function (button) {
      button.addEventListener('click', function () {
        showView(button.dataset.viewTarget);
      });
    });
    showView('log');
  }

  function init() {
    initTheme();
    initForm();
    initTransfer();
    initNav();
    if (Sillage.Preferences) {
      /* A changed palette changes the live prediction and the caveats under it. */
      Sillage.Preferences.init(function () {
        renderPrediction();
        renderLog();
      });
    }
    renderLog();
    renderPrediction();
  }

  Sillage.App = {
    init: init,
    showView: showView,
    renderLog: renderLog,
    renderPrediction: renderPrediction
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window, document);
