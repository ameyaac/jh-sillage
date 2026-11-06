/* The layering page: two logged perfumes worn together.
 *
 * The data layer has always stored combinations (combinations.csv) and pair
 * ratings, but nothing in the page could create or rate one. This is that page.
 *
 * A pair is stored *by reference* to its two perfumes — never as a copied note
 * list — and its rating is a separate event, exactly like a single perfume's.
 * Pair ratings stay out of the single-perfume training set (Store.trainingSet), so
 * nothing here changes the live prediction on the Log page.
 *
 * The score shown for a pair is the placeholder scorer run over both pyramids
 * pooled layer by layer. That is a stand-in for the mixture model described in
 * ARCHITECTURE.md, and the page says so.
 *
 * Exposed as window.Sillage.Layering.
 */
(function (root, document) {
  'use strict';

  var Sillage = root.Sillage = root.Sillage || {};
  var Store = Sillage.Store;
  var Predict = Sillage.Predict;

  var ui = null;            /* helpers handed over by app.js: esc, badge, … */
  var onChangeCallback = null;

  function $(id) { return document.getElementById(id); }

  function label(p) {
    if (!p) return '';
    var name = [p.brand, p.name].filter(Boolean).join(' — ');
    return name || 'Entry #' + p.perfume_id;
  }

  /* Pool two pyramids layer by layer; case-insensitive de-duplication so a note
   * both perfumes share is not counted twice. */
  function pooled(a, b, key) {
    var out = [], seen = Object.create(null);
    a[key].concat(b[key]).forEach(function (note) {
      var k = note.toLowerCase();
      if (seen[k]) return;
      seen[k] = true;
      out.push(note);
    });
    return out;
  }

  function pairGuess(a, b) {
    var families = Store.perfumeTypes(a).concat(Store.perfumeTypes(b));
    return Predict.run(
      pooled(a, b, 'top_notes'), pooled(a, b, 'middle_notes'), pooled(a, b, 'base_notes'),
      families, Store.trainingSet(), Store.palette(), ''
    );
  }

  /* ── form ────────────────────────────────────────────────────────────────── */

  function selected() {
    var byId = Store.perfumesById();
    return { a: byId[Number($('layerA').value)] || null, b: byId[Number($('layerB').value)] || null };
  }

  function appliedFirst() {
    var checked = document.querySelector('input[name="layerFirst"]:checked');
    return checked ? checked.value : '';
  }

  function fillSelect(select, perfumes, keep) {
    select.innerHTML = '<option value="">— choose a perfume —</option>' +
      perfumes.slice().reverse().map(function (p) {
        return '<option value="' + p.perfume_id + '">' + ui.esc(label(p)) + '</option>';
      }).join('');
    if (keep && Store.perfumesById()[keep]) select.value = keep;
  }

  function memberCard(p, slot) {
    if (!p) {
      return '<div class="layer-member is-empty"><span class="layer-slot">' + slot +
        '</span><p class="cell-faint">Pick a perfume</p></div>';
    }
    function line(title, notes, layer) {
      return notes.length
        ? '<div class="layer-notes"><span>' + title + '</span>' +
          '<span class="tag-line">' + notes.map(function (n) {
            return '<span class="tag tag-' + layer + '">' + ui.esc(n) + '</span>';
          }).join('') + '</span></div>'
        : '';
    }
    var fams = Store.perfumeTypes(p);
    return '<div class="layer-member"><span class="layer-slot">' + slot + '</span>' +
      '<p class="layer-name">' + ui.esc(label(p)) + '</p>' +
      (fams.length ? '<span class="family-chips family-chips-row">' + fams.map(function (f) {
        return '<span class="family-chip">' + ui.esc(f) + '</span>';
      }).join('') + '</span>' : '') +
      line('Top', p.top_notes, 'top') + line('Heart', p.middle_notes, 'mid') +
      line('Base', p.base_notes, 'base') + '</div>';
  }

  function renderPreview() {
    var pick = selected();
    $('layerMembers').innerHTML = memberCard(pick.a, 'A') +
      '<div class="layer-plus" aria-hidden="true">+</div>' + memberCard(pick.b, 'B');

    /* Name the "applied first" options after the actual perfumes. */
    $('layerFirstA').textContent = pick.a ? label(pick.a) + ' first' : 'A first';
    $('layerFirstB').textContent = pick.b ? label(pick.b) + ' first' : 'B first';

    var guess = $('layerGuess');
    var same = pick.a && pick.b && pick.a.perfume_id === pick.b.perfume_id;
    $('saveLayerBtn').disabled = !(pick.a && pick.b) || same;
    if (same) {
      guess.innerHTML = '<p class="bad">Pick two different perfumes.</p>';
      return;
    }
    if (!(pick.a && pick.b)) {
      guess.innerHTML = '<p class="cell-faint">Pick two perfumes to see a rough guess.</p>';
      return;
    }
    var result = pairGuess(pick.a, pick.b);
    guess.innerHTML = result
      ? '<div class="layer-guess-score"><span class="layer-guess-value">' + result.total.toFixed(1) +
        '</span><span class="layer-guess-stars">' + ui.stars(result.total) + '</span></div>' +
        '<p class="cell-faint">Rough guess: both pyramids pooled and run through the ' +
        'placeholder scorer. There is no real pair model yet — your ratings below are ' +
        'what it will learn from.</p>'
      : '';
  }

  function save() {
    var pick = selected();
    if (!pick.a || !pick.b) return;
    /* The store canonicalises A/B order; tell it which physical perfume went first. */
    var first = appliedFirst();
    var firstId = first === 'a' ? pick.a.perfume_id : first === 'b' ? pick.b.perfume_id : null;
    var before = Store.combinations().length;
    var combo;
    try {
      combo = Store.logCombination(pick.a.perfume_id, pick.b.perfume_id,
        first ? (firstId === pick.a.perfume_id ? 'a' : 'b') : '', $('layerComment').value);
    } catch (err) {
      ui.toast(err.message);
      return;
    }
    if (Store.combinations().length === before) {
      ui.toast('That pair is already in your layering log — rate it there.');
      highlight(combo.combination_id);
      return;
    }
    var result = pairGuess(pick.a, pick.b);
    if (result) {
      Store.logPrediction({
        subjectType: Store.SUBJECT_COMBINATION, subjectId: combo.combination_id,
        value: result.total, modelVersion: result.modelVersion + '+pooled-pair'
      });
    }
    $('layerComment').value = '';
    renderLog();
    highlight(combo.combination_id);
    ui.toast('Pair saved. Rate it once you have worn them together.');
    if (onChangeCallback) onChangeCallback();
  }

  function highlight(id) {
    var row = document.querySelector('[data-combination-row="' + id + '"]');
    if (!row) return;
    row.classList.remove('is-flash');
    void row.offsetWidth; /* restart the animation */
    row.classList.add('is-flash');
    row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  /* ── pair log ────────────────────────────────────────────────────────────── */

  function renderLog() {
    var combos = Store.combinations();
    var byId = Store.perfumesById();
    var ratings = Store.currentRatings(Store.SUBJECT_COMBINATION);
    var predictions = Store.latestPredictions(Store.SUBJECT_COMBINATION);
    var rated = combos.filter(function (c) { return ratings[c.combination_id]; }).length;

    $('layerCount').textContent = String(combos.length);
    $('layerStats').textContent = combos.length
      ? rated + ' rated · ' + (combos.length - rated) + ' awaiting a rating' : '';

    var list = $('layerLog');
    if (!combos.length) {
      list.innerHTML = '<tr><td colspan="9" class="empty-log">' +
        'No pairs yet — layer two perfumes above.</td></tr>';
      return;
    }

    function td(name, html, cls) {
      return '<td data-label="' + ui.esc(name) + '"' + (cls ? ' class="' + cls + '"' : '') +
        '>' + html + '</td>';
    }
    var faint = '<span class="cell-faint">—</span>';

    list.innerHTML = combos.slice().reverse().map(function (c) {
      var a = byId[c.perfume_id_a], b = byId[c.perfume_id_b];
      /* The one applied first goes in the 1st column. With no recorded order the
       * pair keeps its stored A/B order and both cells say "together". */
      var first = c.applied_first === 'b' ? b : a;
      var second = c.applied_first === 'b' ? a : b;
      var together = c.applied_first === ''
        ? '<span class="pair-together">together</span>' : '';
      var rating = ratings[c.combination_id] ? ratings[c.combination_id].rating : null;
      var predicted = predictions[c.combination_id] ? predictions[c.combination_id].value : null;
      return '<tr data-combination-row="' + c.combination_id + '">' +
        td('#', String(c.combination_id), 'cell-id') +
        td('1st', '<span class="pair-perfume">' + ui.esc(label(first)) + '</span>' + together, 'cell-first') +
        td('2nd', '<span class="pair-perfume">' + ui.esc(label(second)) + '</span>' + together, 'cell-second') +
        td('Note', c.comment ? '<span class="pair-comment">' + ui.esc(c.comment) + '</span>' : faint, 'cell-note') +
        td('Guess', predicted === null ? faint : '<strong>' + predicted.toFixed(1) + '</strong>', 'cell-predicted') +
        td('Your rating', '<select class="rating-select' + (rating === null ? ' is-unrated' : '') +
          '" data-combination-id="' + c.combination_id + '" aria-label="Your rating for ' +
          ui.esc(label(a) + ' with ' + label(b)) + '">' + ui.ratingOptions(rating) + '</select>',
          'cell-rating') +
        td('Verdict', ui.badge(rating), 'cell-verdict') +
        td('Logged', ui.esc((c.created_at || '').slice(0, 10)), 'cell-date') +
        td('', ui.deleteButton('data-delete-combination', c.combination_id,
          'Delete pair ' + label(a) + ' + ' + label(b)), 'cell-actions') +
        '</tr>';
    }).join('');

    list.querySelectorAll('[data-delete-combination]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        ui.deletePair(Number(btn.getAttribute('data-delete-combination')));
      });
    });

    list.querySelectorAll('select[data-combination-id]').forEach(function (select) {
      select.addEventListener('change', function () {
        if (!select.value) return;
        try {
          Store.logRating({
            combinationId: Number(select.dataset.combinationId),
            rating: Number(select.value)
          });
        } catch (err) {
          ui.toast(err.message);
          return;
        }
        renderLog();
        ui.toast('Pair rating saved.');
        if (onChangeCallback) onChangeCallback();
      });
    });
  }

  /* ── page ────────────────────────────────────────────────────────────────── */

  function render() {
    if (!ui) return;
    var perfumes = Store.perfumes();
    var enough = perfumes.length >= 2;
    $('layerNeedMore').hidden = enough;
    $('layerForm').hidden = !enough;
    if (enough) {
      var keepA = $('layerA').value, keepB = $('layerB').value;
      fillSelect($('layerA'), perfumes, keepA);
      fillSelect($('layerB'), perfumes, keepB);
      renderPreview();
    }
    renderLog();
  }

  function init(helpers, onChange) {
    ui = helpers;
    onChangeCallback = onChange || null;
    ['layerA', 'layerB'].forEach(function (id) { $(id).addEventListener('change', renderPreview); });
    $('layerSwap').addEventListener('click', function () {
      var a = $('layerA').value;
      $('layerA').value = $('layerB').value;
      $('layerB').value = a;
      /* The applied-first choice follows the perfume, not the slot. */
      var first = appliedFirst();
      if (first) document.querySelector('input[name="layerFirst"][value="' +
        (first === 'a' ? 'b' : 'a') + '"]').checked = true;
      renderPreview();
    });
    $('layerForm').addEventListener('submit', function (event) {
      event.preventDefault();
      save();
    });
    render();
  }

  Sillage.Layering = { init: init, render: render, pairGuess: pairGuess };
})(window, document);
