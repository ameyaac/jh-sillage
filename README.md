# JH Sillage

Predicts how *you* would rate a perfume (1–5) from its composition — top, middle and
base notes plus type and medium. One user, no catalog, and most perfumes it scores have
never been rated by anyone. See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and
the reasoning behind the model choices.

**Source of truth is Python.** `index.html` is a generated surface: it will get the
lexicon and trained coefficients from an export step, and re-implements the schema and
inference in JS so the page stays standalone and offline. When the two disagree, Python
is right. Nothing about scent semantics is ever authored in JS, and
`tests/test_bridge.py` enforces that the two sides stay compatible.

**Jump to:** [Setup](#setup) · [Your data](#your-data) ·
[Backup and restore](#backup-and-restore) ·
[Working on the web app](#working-on-the-web-app) ·
[Deploying](#deploying-to-github-pages) ·
[How a perfume becomes numbers](#how-a-perfume-becomes-numbers) · [FAQ](#faq) ·
[Layout](#layout) · [More information](#more-information)

---

## Status

| Layer | State |
|---|---|
| `data/` — five-table log, schema, migration, backup/restore | **built**, 52 tests |
| `web/` — browser store, CSV, placeholder scorer, UI | **built**, 47 tests |
| Python ↔ browser bridge | **built**, 4 tests |
| Preferences page — declared base palette | **built** |
| `lexicon/` — note → family/axis data | not started |
| `features/` — normalise, resolve unseen notes, featurise | not started |
| `model/` — prior, ridge, kNN, artifact registry | `predictor.py` is the legacy heuristic |
| `eval/` — grouped LOO + mandatory baselines | not started |
| `export/` — emit lexicon + model bundle for the page | not started |
| `ui/` — customtkinter desktop app | **not in the repo** — source is in `sillage_app.zip`, unported |

**The scorer is still a placeholder.** It fits no parameters, so it cannot be
cross-validated, and the page says so where the number appears. What is real now is the
*data*: your ratings are recorded properly, separated from predictions, and backed up.
With no ratings yet, that is the actual bottleneck — the model has nothing to learn from
until the log fills up.

Two defects in the previous version were serious enough to call out. The log was a
plain in-memory array, so **a page refresh lost everything**, and there was no way to
record an actual rating at all — the table stored only the *predicted* score, which the
model then learned from. Both are fixed.

---

## Setup

Python 3.12, Ubuntu 24.04. The data layer needs no third-party packages at all.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # numpy/scipy/sklearn (+ customtkinter, unused)
```

Nothing above is needed to use the data layer, which is pure standard library. The
desktop app is not in the repo — `ui/` is an empty package and the customtkinter source
sits unported in `sillage_app.zip`, so `python3-tk` and `customtkinter` are not required
today.

The web app needs no build step — open `index.html` in a browser, straight off the
filesystem if you like. Tests:

```bash
python tests/run_all.py                # everything (needs node for the JS suites)
python tests/test_data.py              # data layer only, no pytest needed
node tests/test_web.js                 # browser modules only
```

---

## Your data

Two independent stores, linked by CSV:

- **The browser** keeps its log in `localStorage`. It is per-browser and per-device.
- **Python** keeps its log in a directory that is **not** tracked by git:

```
userdata/                  # or wherever $SILLAGE_DATA_DIR points
  perfumes.csv
  combinations.csv
  ratings.csv
  predictions.csv
  preferences.csv
  _meta.json               # schema version
```

`SILLAGE_DATA_DIR` overrides the location; if you point it inside the repo, add that
path to `.gitignore` yourself, since gitignore cannot expand environment variables.

### Backup and restore

**This is the only irreplaceable thing in the project.** Browser storage is not a
backup — clearing site data, switching device, or opening the page in a private window
loses the log.

In the page, the **Backup & Restore** card does both directions:

- **Download my log** writes all five CSVs, including `preferences.csv`. Keep them
  together; all five are written even when empty, because a backup silently missing
  `ratings.csv` is indistinguishable from one where you had no ratings. The Preferences
  tab has the same button — it downloads the whole log, not a palette-only file, since a
  partial export that looked like a backup is the more dangerous thing to keep.
- **Drop your CSVs here** validates them and shows exactly what it would load —
  accepted rows per table, and why anything was rejected — before you confirm. Only
  then does it replace the log. Files are identified by their header row, so a browser
  download renamed to `perfumes (1).csv` still lands in the right table.

Restore is **replace**, not merge. Merging two logs needs id remapping, and a
half-correct remap silently attaches ratings to the wrong perfume.

The same files move in and out of Python:

```bash
python -m data.portable export ~/Backups/sillage-2026-07-30
python -m data.portable import ~/Downloads/*.csv --replace
python -m data.portable import ~/Downloads/*.csv --dry-run   # validate only
python -m data.portable check ~/Downloads/perfumes.csv       # which table is this?
python -m data.portable --data-dir ./userdata export ~/Backups/today
```

`import` takes `--into DIR` to target a different log directory, `--replace` to overwrite
an existing one, and `--dry-run` to validate without writing. Importing into a non-empty
log without `--replace` is refused rather than merged.

So the round trip works: log on the page → download → `import` into Python to train →
`export` → upload back into the page.

### Why five tables

One table with `predicted_rating` and `actual_rating` side by side is what let the old
engine confuse the two — `actual_rating or predicted_rating` fed the model's own output
back in as ground truth whenever a perfume was unrated. The split makes that impossible
rather than merely discouraged: **`training_set()` reads perfumes and ratings, and has
no code path to `predictions.csv`.**

- **`perfumes.csv`** — immutable compositions. Notes are stored exactly as typed;
  normalisation and family resolution belong to the feature layer, so the log stays
  lossless and the resolution cascade stays testable.
- **`combinations.csv`** — two perfumes worn together, stored *by reference* to their
  members rather than as a flattened note list. The blend is derived, so a lexicon
  improvement propagates to every pair instead of leaving stale copies on disk.
- **`ratings.csv`** — timestamped rating *events*, for a perfume or a combination.
  Re-rating appends rather than overwrites, so both judgments survive. Taste drift is
  something to measure, not erase.
- **`predictions.csv`** — what the model claimed, with its version and feature-spec
  hash. Write-only as far as training is concerned; it exists so predicted-vs-actual can
  be charted honestly and a model version can be held accountable.
- **`preferences.csv`** — taste you *declared*, as opposed to demonstrated. Signed
  affinities in [-1, +1] over families, axes, notes and mediums — deliberately **not** on
  the 1–5 rating scale, so a declaration can never be mistaken for a training target.
  See [the preferences page](#the-preferences-page).

### Usage

```python
from data import Perfume, RatingLog

log = RatingLog()
p = log.log_perfume(Perfume(
    brand="Dior", name="Sauvage",
    perfume_type="Woody", perfume_medium="Spray Perfume (EDT)",
    top_notes=["bergamot", "pink pepper"],
    middle_notes=["lavender"],
    base_notes=["ambroxan", "vetiver"],
))
log.log_rating(p.perfume_id, 4.5)     # after you have actually worn it

log.training_set()   # [(Perfume, 4.5)] — chronological, latest rating per perfume
log.unrated()        # logged but not yet judged
log.groups()         # perfume_id -> grouping key for grouped cross-validation
log.summary()        # counts, mean rating, distinct notes
```

Declared taste — optional, and a prior rather than evidence:

```python
log.log_preference("family", "citrus", +1.0)
log.log_preference("family", "animalic", -1.0)
log.log_preference("note", "Cumin", -1.0)
log.palette()        # {'family': {'citrus': 1.0, 'animalic': -1.0}, ...}
log.has_palette()
```

Layered pairs live in the same log:

```python
combo = log.log_combination(a.perfume_id, b.perfume_id, applied_first="b")
log.log_rating(rating=4.0, combination_id=combo.combination_id)
log.pair_training_set()   # [(Combination, Perfume, Perfume, 4.0)]
```

Everything the store exposes:

| Write | |
|---|---|
| `log_perfume(Perfume)` | append a composition, returns it with its id and timestamp |
| `log_combination(a_id, b_id, applied_first="", comment="")` | append a pair; re-logging the same pair returns the existing record |
| `log_rating(perfume_id, rating, ...)` / `log_rating(rating=…, combination_id=…)` | append a rating event; exactly one subject |
| `log_prediction(Prediction)` | append what the model claimed; never read back by training |
| `log_preference(scope, key, weight, ...)` | append a declaration; `weight` in [-1, +1] |

| Read | |
|---|---|
| `perfumes()` · `perfumes_by_id()` | all compositions |
| `combinations()` · `combinations_by_id()` | all pairs |
| `rating_events(subject_type=None)` | every rating ever given, oldest first |
| `current_ratings(subject_type="perfume")` | latest rating per subject |
| `predictions(subject_type=None)` | everything the model claimed |
| `preference_events()` | every declaration ever made, oldest first |
| `palette(include_retracted=False)` · `has_palette()` | the declared palette as `{scope: {key: weight}}` |
| `training_set()` | `[(Perfume, rating)]`, chronological, single perfumes only |
| `pair_training_set()` | `[(Combination, Perfume, Perfume, rating)]` |
| `unrated()` · `unrated_combinations()` | logged but not yet judged |
| `groups()` · `combination_groups()` | grouping keys for grouped cross-validation |
| `summary()` | counts, mean rating, distinct notes and groups |

Paths are available as `perfumes_path`, `combinations_path`, `ratings_path`,
`predictions_path`, `preferences_path` and `meta_path` if you need to read the CSVs
directly.

Records are validated before they touch disk: ratings must be 1–5 in half-star steps,
a perfume needs at least one note, notes may not contain `;` (the in-cell separator),
a combination needs two *different* known perfumes, and a rating for an unknown id is
refused. Ids are `max + 1`, never `len + 1` — the legacy store reissued ids after a
deletion and silently attached new ratings to the wrong perfume.

`groups()` exists because grouped cross-validation is not optional here: *Bleu EDT* and
*Bleu EDP* share a composition, and letting them land on opposite sides of a split
leaks the target. Concentration terms are stripped when grouping; flanker words like
*Intense* are deliberately kept, since those usually are a different juice.

Pair ratings deliberately stay out of `training_set()`. Layering is a different target
with a different model — a *correction* on top of the single-perfume prediction (see
ARCHITECTURE.md §8) — so mixing the two would contaminate the composition-level fit.

### The preferences page

**Optional, and the page says so.** The *Preferences* tab lets you declare a base smell
palette before you have rated anything: note families, specific notes you seek out or
avoid, preferred mediums, and — behind an "advanced" disclosure — perceptual axes. An
empty palette is a complete answer; nothing gates on filling it in.

Each control is a five-way scale (avoid / dislike / – / like / love) mapping to weights
`-1, -0.5, 0, +0.5, +1`.

Three things make this different from the legacy "taste profile" it replaces:

- **It's a prior, not a rule.** Precedence in the scorer is **what you rated > what you
  declared > the shipped default list**. The old engine hardcoded a liked/disliked note
  set that no amount of contrary evidence could revise. In the real model these become
  prior *means* on family coefficients, which the likelihood overrides family by family
  at a rate proportional to evidence (ARCHITECTURE.md §4.3).
- **It is never training data.** Declarations live in their own table on their own scale.
  `training_set()` has no code path to them, exactly as with predictions.
- **Changing your mind is recorded, not erased.** Declarations are append-only events, and
  setting something back to **–** writes a *retraction* (weight 0) rather than deleting
  the row. A retracted palette and an empty one look identical to the model — correct,
  since both mean "no prior" — but the history stays on disk.

Honest limitation: the placeholder scorer can only use **note** declarations directly,
plus **family** and **medium** declarations when the logged scent family maps onto the
14-family taxonomy and the medium matches exactly. **Axis** declarations are stored but
cannot be applied until `lexicon/` exists, because without note→axis resolution there is
nothing to apply them to. The page states this rather than implying otherwise.

If you used the old desktop app, `taste_profile.json` migrates:

```bash
python -m data.migrate --profile path/to/taste_profile.json --apply
```

`loved_families` / `disliked_families` become ±1, `liked_notes` / `disliked_notes` become
±1 on the note scope, `preferred_mediums` become +1, and 1–5 slider scores are rescaled
onto the signed range (3 → 0). Unknown families are dropped rather than guessed at. The
profile migrates independently of the rating log, so an old profile is worth keeping even
if there were never any ratings.

### Migrating the old log

```bash
python -m data.migrate                 # dry run — prints what it would write
python -m data.migrate --apply
```

`scent_family → perfume_type`, `scent_medium → perfume_medium`, and both
`scent_strength` and the free-text `notes` column are preserved rather than dropped.
`predicted_rating` goes to `predictions.csv` tagged `legacy-heuristic`;
`actual_rating` is the only thing that becomes a training target. Rows that cannot be
migrated are listed in the report, never dropped silently. Migration refuses to run
twice into a non-empty log unless you pass `--force`.

---

## Working on the web app

No build step: open `index.html`, edit a file, reload. Worth knowing before you touch it.

### Module contract

Loaded as **classic scripts in dependency order** — `csv.js` → `store.js` →
`predict.js` → `preferences.js` → `app.js` — each attaching to `window.Sillage`. Not ES
modules, because `file://` CORS blocks module imports and the page is meant to work opened
straight off disk.

| File | Holds | Must not hold |
|---|---|---|
| `csv.js` | RFC 4180 read/write | anything domain-specific |
| `store.js` | the five tables, validation, export/import | scoring rules, DOM |
| `predict.js` | the scorer — the seam where the exported model lands | persistence, DOM |
| `preferences.js` | the declared-palette page | scoring rules |
| `app.js` | DOM wiring, view tabs, log table, backup/restore | schema knowledge, scoring rules |

`app.js` deliberately knows no field names and no scoring rules, so swapping in the
Python-exported model touches `predict.js` only.

### Two view tabs, one document

Log and Preferences are sections with `data-view`, toggled by `showView()` in `app.js` —
not separate HTML files. `localStorage` is not reliably shared between two documents on a
`file://` origin, and one document means one storage context.

### The `[hidden]` trap

**This already caused a bug.** The `hidden` attribute works only through the browser's
user-agent stylesheet, so *any* author rule that sets `display` on the element wins and
the element stays visible. `.main-content { display: grid }` silently overrode
`hidden`, and both views rendered stacked on top of each other while the tabs appeared to
do nothing.

`web/styles.css` therefore carries, above everything that sets a display value:

```css
[hidden] { display: none !important; }
```

Don't remove it, and don't add a `display` rule above it.

### Static lints in `tests/test_web.js`

There is no DOM in the test environment, so these are lints rather than render tests. They
exist because the bugs they catch are invisible to unit tests and obvious in a browser:

- `[hidden] { display: none !important }` exists **and** precedes `.main-content`
- every `data-view` has a matching `data-view-target`, and vice versa
- **exactly one** view starts visible — two is the stacked-views bug, zero is a blank page
- every `download*Btn` in the markup is actually wired up in `app.js`
- scripts load in dependency order and none are `type="module"`

They cannot prove the page works. Nothing here replaces opening it in a browser.

### Browser-side store extras

Beyond the mirror of the Python API: `Store.setPalette(entries)` writes several
declarations at once and skips no-ops, `Store.isEmpty()` reports whether *any* table holds
anything (which is what gates the backup button — a palette with no perfumes is still
worth downloading), `Store.prepareImport(files)` / `Store.applyImport(report)` split
validation from committing, and `Store.exportFiles()` returns
`[{table, name, text, rows}]`.

---

## Deploying to GitHub Pages

The web app is fully static — no build step, no server, and every asset path is relative
— so Pages serves it as-is, including from a subpath like
`https://<user>.github.io/jh-sillage/`.

### Read this first: your data does not come with it

**`localStorage` is per-origin.** `file:///…/index.html` and
`https://<user>.github.io/jh-sillage/` are different origins with completely separate
storage, so the deployed page starts empty even though the local one has your log. This
is not a bug and there is no setting that changes it.

So the order matters:

1. On your **local** page, open *Backup & Restore* → **Download my log** (5 CSVs).
2. Deploy.
3. On the **deployed** page, drop those CSVs into the import box and confirm.

Same applies in reverse, and to every origin independently — `file://`, `localhost` and
the Pages URL are three separate stores. Treat the CSVs as the thing that moves; the
browser is just where a copy happens to live.

### It is public

GitHub Pages sites are public, and on a free account the repo must be public too. Two
consequences:

- **`userdata/` must stay out of git.** It already is, and `.gitignore` also covers
  `sillage_log.csv`, `taste_profile.json` and `smell-profile.json`. Verify with
  `git status --ignored` before the first push — a rating log pushed once is public
  history even after deletion.
- **The whole branch is served**, so `data/schema.py`, `tests/` and everything else
  becomes fetchable over HTTP. That's fine — it's source, not secrets — but know it.

Anyone with the URL can *use* the app, and their data stays in their own browser. No
server means no accounts and nothing shared.

### Steps

```bash
# 1. Commit. (The repo currently has no commits and is on `master`.)
git branch -M main
git add .
git commit -m "Sillage: data layer, web app, docs"

# 2. Create an EMPTY public repo on github.com — no README, no .gitignore — then:
git remote add origin https://github.com/<user>/jh-sillage.git
git push -u origin main
```

Then in the repo: **Settings → Pages → Build and deployment → Source: Deploy from a
branch → Branch: `main` / `(root)` → Save.** First build takes a minute or two; the URL
appears on that same page.

### Optional touches

- **`.nojekyll`** — an empty file at the repo root skips Jekyll processing. Not strictly
  needed here (nothing in the deployed set starts with `_` or `.`), but it removes a
  class of surprise and speeds the build: `touch .nojekyll && git add -f .nojekyll`.
- **Fonts** come from the Google Fonts CDN. Fine online; the page falls back to Georgia
  and a system sans if that's blocked. Self-host them under `web/` if you want it fully
  offline.
- **Publishing only the web assets** — if serving the Python source bothers you, add a
  GitHub Actions workflow that uploads just `index.html` and `web/` as the Pages
  artifact, instead of deploying from a branch. More moving parts; only worth it if you
  actually care.

### Caching

Pages sets a short cache lifetime, but a hard-reload (Ctrl/Cmd+Shift+R) is the reliable
way to see a fresh `web/js/*.js` after a push. If you deploy often, append a version
query to the script tags (`web/js/app.js?v=2`).

---

## How a perfume becomes numbers

The design guide for the feature layer. Nothing here touches a perfume name, brand or
ID — the model is a function over *composition space*, so it can score a fragrance that
has never existed. [ARCHITECTURE.md](ARCHITECTURE.md) §1–3 has the full reasoning; this
is the working summary.

### Note lists become mass vectors, not sets

Each layer is a distribution over notes summing to 1:

```
top:    {bergamot: 0.5, pink pepper: 0.5}
middle: {jasmine: 1.0}
base:   {vetiver: 0.34, amber: 0.33, vanilla: 0.33}
```

This is the load-bearing decision, for two reasons:

- **Invariance to transcription verbosity.** Note lists are hand-entered and vary wildly
  in length. A perfume listed with 8 base notes is not "more woody" than one listed with
  3 — the difference is how thorough whoever typed it was. Raw counts encode that noise
  straight into the features.
- **Layering falls out free.** A layered pair is a weighted mixture of two mass vectors —
  the same type, not a special case. With sets it would need its own code path.

### Notes resolve through families, never into their own columns

With `n < 100` ratings and 500–3000 distinct notes, one-hot per note is memorisation, and
every unseen note is a zero column carrying nothing. So notes pass through an
indirection: `note string → (family distribution, perceptual axes, confidence)`.

A note is a **distribution** over families, never a single label:

```
bergamot    → {citrus: 0.7, fresh: 0.2, floral: 0.1}
vetiver     → {woody: 0.5, green: 0.3, earthy: 0.2}
tonka bean  → {gourmand: 0.5, warm: 0.3, powdery: 0.2}
```

Hard one-note-one-family tables — what the legacy `predictor.py` implied — throw away
real information. Bergamot genuinely *is* mostly citrus with a floral edge.

Features are then aggregations over that, in named blocks:

| Block | Dim | Content |
|---|---|---|
| `B1` pooled family | 14 | family intensity across layers, with perceptual layer weights |
| `B2` per-layer family | 42 | family intensity per layer (3 × 14) |
| `B3` perceptual axes | 8 | sweet, fresh, warm, woody, green, spicy, animalic, powdery |
| `B4` structure | ~7 | note counts, layer balance, family entropy, unknown fraction, note rarity |
| `B5` categoricals | ~12 | `perfume_type` + `perfume_medium` |
| `B6` note identity | dynamic | one column per note with **≥3 ratings in your own log** |

Worked example, using the masses above:

```
B2[top, citrus] = 0.5 × 0.7 = 0.35        (bergamot)
B2[top, spicy]  = 0.5 × 0.6 = 0.30        (pink pepper)
B1[citrus]      = w_top × 0.35 + w_mid × … + w_base × …
```

`B3` is only 8 columns but matters more than its size suggests: it is the densest block
and the one that generalises to notes you have never rated. A note resolved only to
"woody, warm" still lands in a region of the space where your preference is estimated.

### Top / middle / base — three mechanisms, not a choice between them

1. **`B2` keeps the layers separate**, so the model learns *your* layer sensitivity from
   data. Some people buy for the dry-down, some for the first ten seconds. Nothing
   hardcodes "heart notes matter 1.3×" — that was an unfalsifiable constant in the legacy
   scorer.
2. **`B1` pools across layers** with fixed perceptual weights, as a low-variance
   smoothing prior.
3. **The blocks get different shrinkage.** `B1`/`B3` lightly penalised, `B2` heavily. At
   low `n` the model leans on the pooled block; as evidence accumulates, `B2` earns its
   way in.

That third point is what makes layer distinction affordable — you get the resolution of
42 per-layer columns without paying for 42 free parameters up front. It is one global
ridge solve, not a custom optimiser: scale block `g`'s columns by `1/√λ_g` first, which
is the standard exact equivalence.

### Categorical features

`perfume_type` (~10 values) and `perfume_medium` (~7) are low-cardinality, so **plain
one-hot**. Do not use target or mean encoding — it leaks the target badly at this sample
size.

Two refinements worth the columns:

- **An ordinal projection for medium.** Concentration is genuinely ordered: body mist <
  EDC < EDT < EDP < extrait < attar. One monotone column captures "how loud, how long"
  with a single parameter where the one-hot needs seven. Keep both — the ordinal carries
  the trend cheaply, the one-hot catches format effects (an oil behaves differently from
  a spray at the same strength).
- **An explicit "unspecified" level.** All-zeros must not mean both "not recorded" and
  "none of the above". Both fields are optional in the log, so this is common.

Caveat: `perfume_type` is substantially redundant with the family blocks — a perfume
labelled *Woody* has woody notes. Ridge handles that fine (correlated features share a
coefficient), but don't read the type coefficient as an independent effect size.

### Notes never seen before

Unseen notes are the **normal case, not the edge case** — that's the premise of scoring
compositions without a database. Each step of the cascade returns a confidence:

| Step | Method | Confidence |
|---|---|---|
| 1 | normalise — lowercase, strip parentheticals, drop `absolute`/`accord`/`extract`/`oil`, singularise | — |
| 2 | exact lexicon lookup | 1.0 |
| 3 | alias table (`agarwood → oud`, `ambroxan → ambergris`) | 1.0 |
| 4 | fuzzy match, edit ratio ≥ 0.88, length-guarded | 0.9 |
| 5 | head-noun morphology (`* wood → woody`, `* blossom → floral`, `* berry → fruity`) | ~0.7 |
| 6 | modifier hints (`sea\|marine → aquatic`, `burnt\|smoked → smoky`) | ~0.6 |
| 7 | subword 3-gram cosine — borrow the match's *family distribution*, never its identity | ~0.5 |
| 8 | unknown | 0 |

Three numeric details that are easy to get wrong:

- **An unknown note abstains; it does not contribute a constant.** The legacy scorer gave
  unseen notes `0.1`, so every novel composition drifted toward a fixed value regardless
  of content — which defeats the whole purpose. Unknown contributes zero to `B1`–`B3`,
  increments `B4.unknown_fraction`, and widens the predictive interval.
- **Weight each note's mass by its resolution confidence**, so a note guessed at 0.5
  counts half as much as an exact hit.
- **Renormalise over resolved mass only, and record the deficit.** Normalising a layer to
  1 *before* resolution lets unknowns silently dilute everything else: one recognised note
  among three unknowns would read as 25% as citrusy as it should.

The real fix for an unknown note is asking. Unresolved notes should queue in the UI with a
"what does this smell like?" prompt, writing into a user-extension lexicon that overrides
the base one — a lexicon that grows with use beats any imputation heuristic. **Extend it
from note descriptions only, never from ratings**: the latter leaks the target into the
feature space.

### Why families are load-bearing

- **Statistical.** 80 ratings against 14 family coefficients is ~6 observations per
  parameter: tight but workable. 80 ratings against 500 note coefficients is memorisation.
  Families are a hierarchical prior — `note → family → rating` — so every note borrows
  strength from every other note in its family. This is what makes prediction from 30
  ratings possible at all.
- **Transfer.** Without families, a note you have never rated carries literally zero
  information and the model can only fall back to your mean.
- **Explainability.** "You tend to enjoy citrus openings" *is* the coefficient on
  `B2[top, citrus]`. The explanation vocabulary and the feature vocabulary are the same
  vocabulary — no post-hoc approximation over an opaque model.

Families alone are too coarse, which is why `B6` exists: once a note has ≥3 observations
it earns its own column and may deviate from its family. That gate matters — an ungated
note column with one observation just memorises one perfume.

Taxonomy (14): citrus, floral, white-floral, fruity, green, aromatic, spicy, woody,
resinous, amber, gourmand, musk, animalic, aquatic.

---

## FAQ

### Using it

**Where does my data actually live?**
Two independent stores linked by CSV: the browser keeps its log in `localStorage`
(per-browser, per-device), and Python keeps its log in `userdata/`. Neither syncs
automatically — the CSVs are the transport.

**I opened the page on my phone and it's empty.**
Expected. `localStorage` is per-browser *and* per-origin. Download the CSVs on the first
device and upload them on the second. The same applies after deploying — see
[Deploying](#deploying-to-github-pages).

**Why can't I merge two logs?**
Restore is replace-only. Merging needs id remapping, and a half-correct remap silently
attaches ratings to the wrong perfume — the exact class of bug the schema exists to
prevent. If you need it, do the merge deliberately in Python where you can check it.

**Why can't I clear a rating once I've set it?**
Ratings are append-only *events*. Changing your mind appends a new event and the latest
wins, so both judgments stay on disk — taste drift is something to measure, not erase.
Deleting means editing `ratings.csv` by hand.

**Can I edit the CSVs by hand?**
Yes, that's part of why they're CSV. Re-import through `--dry-run` first: every row is
validated and anything malformed is reported rather than absorbed. Don't put a `;` in a
note name — it separates notes inside a cell.

**I logged the same perfume twice. Is that a problem?**
Not for storage, and it's handled for evaluation: `groups()` maps both rows to one
grouping key so they can't land on opposite sides of a train/test split, which would leak
the target. Concentration terms (EDT/EDP/parfum) are stripped when grouping; *Intense*,
*Elixir* and *Absolu* are deliberately kept apart, since those usually are a different
juice.

**Why five CSVs instead of one?**
See [Why five tables](#why-five-tables). Short version: keeping `predicted` and `actual`
in adjacent columns is what let the old engine train on its own output.

**Do I have to fill in the preferences page?**
No. An empty palette is a complete answer — the model then starts from neutral and learns
only from what you rate. A half-remembered guess entered under pressure is worse than no
prior at all.

**I declared that I love citrus, but a citrus perfume still scored low. Why?**
Either you have rated something contradicting it — ratings outrank declarations by design
— or the notes in that perfume aren't ones the placeholder recognises as citrus. Family
declarations only apply through the scent-family button until the lexicon lands.

**Why are preference weights -1 to +1 instead of 1 to 5?**
So they can never be confused for a rating. A declared affinity is a prior on a
coefficient; a rating is an observation. Keeping them on different scales makes mixing
them up a validation error rather than a silent bug — `log_preference("family",
"citrus", 4.5)` is refused.

**Is my palette included when I download a backup?**
Yes — it's `preferences.csv`, one of the five files. You can download it before logging
any perfumes at all: declaring taste first and logging later is the normal order, and a
palette-only backup restores on its own.

**How do I undo a declaration?**
Set it back to **–**, or use *Clear everything*. Both write retractions rather than
deleting rows, so the palette empties while the history stays in `preferences.csv`.

**How do I record two perfumes worn together?**
`log_combination(a_id, b_id, applied_first="b")` then rate the combination. The page has
no UI for this yet — storage and backup support it, the front end doesn't.

**Should I trust the score the page shows?**
Not yet, and it says so where the number appears. The current scorer fits no parameters,
so it cannot be cross-validated. Log and rate anyway — the ratings are the point.

**How many ratings before it's actually useful?**
Below ~8 the honest answer is "the prior, not a model". 25–60 is where a fitted model
starts beating the naive baseline. The learning curve in `eval/` will show this for your
own data rather than guessing.

### Design questions

**Why not just one-hot encode the notes?**
`p >> n`. 500–3000 possible notes against fewer than 100 ratings is memorisation, and
every unseen note becomes a zero column carrying no information — while unseen notes are
the normal case here.

**Isn't ~83 features on 80 rows overfitting?**
`p > n` is fine by construction with a penalised model: the ridge penalty makes the
solution unique and stable. The guard is not fewer features, it's block shrinkage plus an
honest evaluation — grouped leave-one-out, benchmarked against "always predict your mean".
If the model can't beat that baseline on MAE, the representation has learned nothing. That
check is the difference between a working system and a self-flattering one.

**Why not CatBoost / XGBoost / Random Forest?**
Sample starvation (a depth-6 tree on 80 rows leaves under 2 samples per leaf), the wrong
inductive bias (preference is roughly monotone and smooth in "how much citrus is in this";
a tree spends scarce data rediscovering that with a staircase of splits), unstable
attributions (importances reorder across seeds, so you can't build a taste-profile page on
them), and no extrapolation outside the observed hull — which is bad when the whole point
is scoring novel compositions. CatBoost is the right first tree model to add *as an
ensemble member* above ~150 samples, never as a replacement for the linear core.
ARCHITECTURE.md §4.2 has the full argument.

**Why fit on the residual from my mean rating instead of raw stars?**
It removes the intercept problem and your idiosyncratic scale use, and it makes the
baseline check meaningful. With ratings skewed to 3.5–4.5, "always predict your mean"
achieves MAE ≈ 0.5 and is genuinely hard to beat.

**Why is this not collaborative filtering / a recommender?**
One user, no co-occurrence matrix, no catalog. CF cannot score an item nobody has rated,
which is precisely the requirement. The system is a *function* over compositions; a
catalog, if one ever exists, is just a set of points to evaluate it at. That inversion is
what makes novel-composition scoring possible.

**Why does the browser duplicate the Python schema instead of calling an API?**
So the page works offline as a single standalone surface. The cost is two hand-written
implementations; the guard is `tests/test_bridge.py`, which runs the browser store under
node, has it export CSVs, and imports them with Python. Nothing at runtime enforces the
agreement, so the test does.

**Why version the feature spec?**
Hash the block definitions plus the lexicon and store it with any trained model, then
refuse to load a model whose spec no longer matches. Otherwise a lexicon edit silently
invalidates stored coefficients and you get quietly wrong predictions with no error.

---

## Layout

```
index.html            entry point — markup only
web/
  styles.css          all styling
  js/csv.js           RFC 4180 read/write
  js/store.js         the five tables in localStorage — mirrors data/schema.py
  js/predict.js       PLACEHOLDER scorer; the seam where the real model lands
  js/preferences.js   the declared-palette page
  js/app.js           DOM wiring, view tabs, log table, backup & restore
data/
  schema.py           record types and validation
  store.py            RatingLog — append-only reads and writes
  portable.py         CSV export/import, the browser bridge
  migrate.py          legacy CSV + taste profile -> five tables
model/predictor.py    legacy heuristic, being replaced
ui/                   empty package — the customtkinter app in sillage_app.zip
                      has not been ported to the new store
tests/                run_all.py · test_data.py · test_portable.py
                      test_web.js · test_bridge.py
ARCHITECTURE.md
```

The front end is split into markup, styling and behaviour, loaded as classic scripts in
dependency order — not ES modules, so the page still works when opened directly from
the filesystem, where `file://` CORS would block module imports.

`web/js/store.js` is a hand-written mirror of `data/schema.py`, down to the snake_case
field names. That duplication is the cost of a standalone offline page; the guard
against drift is `tests/test_bridge.py`, which runs the browser store under node, has it
export real CSVs, and imports them with Python.

---

# More information

Long-form write-ups behind specific design decisions.
[ARCHITECTURE.md](ARCHITECTURE.md) is the canonical design document; these are the
practical arguments for the choices in it, kept here so they don't have to be
re-derived.

- [Choosing the model](#choosing-the-model) — why penalised linear regression over
  the tree family, model by model
- [Designing the explainability layer](#designing-the-explainability-layer) — why not
  SHAP, and how to test that an explanation is true
- [Designing for features you haven't built yet](#designing-for-features-you-havent-built-yet)
  — layering, collaborative filtering, and what extensibility actually costs

---

## Choosing the model

### A correction to the usual framing

These are **not mostly categorical features** once featurised. The raw input is
set-valued and categorical, but the design matrix is ~14 pooled family intensities + 42
per-layer intensities + 8 perceptual axes + ~7 structural columns — all continuous —
against only ~12 genuine one-hot columns. "Small data plus categorical features →
CatBoost" is the standard reflex and it rests on a premise that mostly evaporates once
notes resolve through families.

The facts that actually decide the choice: `n < 100` with `p ≈ 83`, so `p > n`; the
target is bounded on [1,5] and skewed to 3.5–4.5; **every perfume at prediction time is
effectively novel**, so extrapolation and transfer to unseen notes are requirements, not
nice-to-haves; there is no budget for a held-out test set, so evaluation is
leave-one-out; and the product needs *stable* interpretability.

### Recommendation

| Model | Verdict at n<100 | Main reason |
|---|---|---|
| **Bayesian ridge** | **core model** | `p>n` fine by construction; free exact LOO; coefficients *are* the explanation; prior mean solves cold start |
| **Gaussian Process** | **ensemble member, strong** | calibrated uncertainty; Tanimoto kernel injects note-set similarity directly |
| **Pairwise ranker** | **add it** | turns 60 ratings into ~1770 training pairs |
| k-NN | low weight, kept for *explanations* | distances concentrate in 83 dims, but it supplies real evidence |
| CatBoost | wait until ~150 samples | best GBM here (ordered boosting); still can't extrapolate |
| Random Forest | marginal above ~60, low weight | free OOB estimate; hard prediction ceiling |
| XGBoost | skip | strictly dominated by CatBoost on this problem |
| LightGBM | **skip — worst fit of the family** | every design choice targets large `n` |
| Plain OLS | **cannot run** | `X'X` is singular when `p > n` |

### Linear regression

**Plain OLS does not work here** — with `p > n`, `X'X` is singular and there is no unique
solution. Any usable answer is a *penalised* linear model, and the penalty is not a
regularisation detail bolted on afterwards, it is the core algorithm.

**Advantages, specific to this problem:**

- **`p > n` is handled by construction.** The penalty makes the solution unique and
  stable. No feature deletion required.
- **Leave-one-out is free.** The exact LOO residual is `rᵢ / (1 − hᵢᵢ)` from the
  hat-matrix diagonal, so all `n` folds cost one solve. Refit on every new rating in
  milliseconds. No tree model gives you this.
- **The coefficients *are* the explanation, exactly.** Contributions sum to the
  prediction with zero residual.
- **A non-zero prior mean solves cold start.** Onboarding answers set prior *means* on
  family coefficients instead of shrinking toward zero, and the likelihood overrides
  them smoothly as evidence arrives. No tree model has anywhere to put that information.
- **Block-specific shrinkage gives partial pooling** — the mechanism that lets per-layer
  and per-note effects exist without paying for them up front.
- **It extrapolates**, which matters because scoring never-before-existing compositions
  is the whole point.
- **Calibrated predictive variance** for the `±` and for uncertainty-weighted ensembling.

**Disadvantages:**

- **It cannot discover interactions.** You must inject the ones you know about (clash
  pairs, saturation, redundancy). With 80 rows that's the right trade — nothing can
  *estimate* interactions here — but domain knowledge is doing real work.
- **It assumes a roughly additive, monotone response.** Mostly true for preference;
  wrong for real non-monotonicities (a trace of animalic can attract where a lot
  repels). Mitigable with a squared term on a few axes, at the cost of columns.
- **Model quality becomes lexicon quality.** That's inspectable and fixable, but not free.

**Ridge, not Lasso.** Variable selection sounds attractive at `p > n`, but the family
blocks are correlated by construction (`B1` is literally a weighted sum of `B2`). Lasso
picks one of a correlated group arbitrarily and drops the rest — and *which* one changes
between refits. Your taste-profile page would then change its claims when you rate one
more perfume, which destroys trust faster than a slightly worse MAE buys. Elastic net is
a compromise; ridge with block-specific λ is more principled, because grouped shrinkage
is what we want and selection is not.

### Random Forest

**Advantages:** low variance, minimal tuning, consumes sparse one-hots without
preprocessing, and **out-of-bag error is a free validation estimate** — genuinely
valuable when you can't afford a test set. Provides a real nonlinear signal above roughly
60 samples as a low-weight ensemble member.

**Disadvantages:**

- **The arithmetic is unkind.** At `n=80` each bootstrap sample holds ~63% unique rows,
  so ~50 effective observations per tree. A defensible `min_samples_leaf ≈ 5` allows
  three or four meaningful splits — a coarse piecewise-constant function on 4 of 83
  features.
- **A hard prediction ceiling.** Predictions are averages of leaf means, so it can never
  predict above the highest leaf average. It structurally cannot say "this novel
  composition is better than anything you've rated" — one of the more useful things this
  product could say.
- **Split bias toward high-cardinality features**, so continuous `B6` note-identity and
  rarity columns would dominate spuriously over the one-hots.
- **Unstable importances**, so no taste-profile page can be built on them.
- Axis-aligned splits approximate a smooth monotone response with a staircase.

### XGBoost

**Advantages:** mature, fast, well-regularised for its class, real control via
`reg_lambda` / `reg_alpha` / `min_child_weight`.

**Disadvantages:**

- **Boosting overfits enthusiastically at 80 rows** unless regularised so hard it becomes
  approximately linear — at which point use the linear model, which is faster,
  interpretable, and has calibrated uncertainty.
- **The largest tuning surface, and no budget to tune it.** Tuning against the same LOO
  you then report is hyperparameter leakage and produces an optimistic number.
- **No ordered boosting**, so it suffers the prediction-shift/target-leakage problem that
  hurts most precisely at small `n`. That is the specific thing CatBoost fixes.

It offers nothing CatBoost doesn't do better here.

### LightGBM

**The worst fit of the tree family**, for structural reasons rather than quality — every
design decision that makes LightGBM excellent is an optimisation for large `n`:

- **Leaf-wise (best-first) growth** with the default `num_leaves=31` on 80 rows is near
  perfect memorisation. You'd need `num_leaves=3–4`, which disables its distinguishing
  feature.
- **`min_data_in_leaf` defaults to 20.** On 80 rows that's a stump; lower it and you
  overfit immediately. The safe window is narrow to nonexistent.
- **Histogram binning** (`max_bin=255`) is meaningless when a feature has at most 80
  distinct values — it only adds discretisation error.
- **GOSS** subsamples by gradient magnitude: no computation to save at 80 rows, real
  variance to add.
- **EFB** bundles mutually-exclusive sparse features, which would bundle *our*
  `perfume_type` / `perfume_medium` one-hots together and destroy the individual
  interpretability they were cheap enough to have.

It trains in five milliseconds and is wrong. Its genuine advantages — speed and native
categorical support — are worthless when the dataset fits in a cache line and the
categoricals are already encoded.

### CatBoost

**The best of the GBMs for this problem**, for real reasons:

- **Ordered boosting** is the one boosting variant explicitly designed against the
  prediction-shift problem that dominates at small `n`.
- **Ordered target statistics** handle `perfume_type` / `perfume_medium` without the leaky
  mean-encoding you'd otherwise hand-roll — and mean encoding at `n<100` leaks badly.
- **Symmetric (oblivious) trees** use the same split at every node of a level: heavy
  regularisation by construction, a defensible bias when you have no data to spend on
  discovering structure.
- **Sensible defaults**, lowering the risk of tuning against your own reported metric.

**Disadvantages:** needs ~150+ samples to earn its place; depth must be crushed to 2–3
with high `l2_leaf_reg` and a low learning rate; still cannot extrapolate; still no
calibrated uncertainty; importances still unstable at this `n`.

**Verdict:** the right first tree model to add — above ~150 samples, as an *ensemble
member*, never as a replacement for the linear core.

### k-Nearest Neighbors

**Disadvantages first, because they disqualify it as a primary model:**

- **Distances concentrate.** With 80 points in ~83 dimensions the ratio of nearest to
  farthest neighbour distance approaches 1, so "nearest" carries little information.
- **Predictions are neighbour averages**, so it shrinks toward the mean, cannot
  extrapolate, and inherits the upward rating skew.
- **Never Euclidean on the full sparse space.** It must be cosine on the dense `B1+B3`
  vector, plus Tanimoto on the raw note sets as a second view. Wrong metric and it loses
  to the mean predictor.

**Advantages that justify keeping it at low weight:**

- **It produces the evidence for explanations** — "closest to X, which you rated 4.5;
  shares vetiver, bergamot, ambergris" is grounded in actual rows. No other model yields
  that artifact directly.
- **It works with three ratings**, so it has a role at the very bottom of the data range.

### Approaches not usually in the shortlist

**Gaussian Process with a composite kernel.** For `20 ≤ n ≤ 200` arguably the
best-fitting tool available:

```
k(A, B) = a · Tanimoto(notes_A, notes_B)
        + b · RBF(family_axis_vector_A, family_axis_vector_B)
        + c · 1[type_A = type_B]
```

*Advantages:* nonparametric, so no `p > n` problem at all — it works in function space
and O(n³) is nothing at `n=80`. Natively calibrated uncertainty. Most importantly **the
kernel is where domain knowledge about note-set similarity enters directly** rather than
having to be learned from 80 examples. It also yields one interpretable artifact: the
posterior weight on each training perfume tells you which of your ratings drove a
prediction.

*Disadvantages:* less interpretable than the linear model (covered by having both);
kernel hyperparameters optimised by marginal likelihood can overfit at small `n`, so
bound them; and it reverts to the prior mean far from data — arguably correct, but it
won't make bold calls on genuinely novel compositions.

**A pairwise ranker — the highest value-per-line addition at this scale.** Absolute star
ratings are noisy and drift over months; *relative* judgments are far more stable. From
60 ratings you get ~1770 pairs. Fit a linear ranker on feature **differences**, which
cancels the intercept and any additive rating bias, and effective sample size goes from
60 to ~1770. Ranking is also closer to the real decision — "which of these three should I
buy". Restrict to pairs with `|Δrating| ≥ 0.5` so ties don't dominate.

**A bounded / ordinal head.** The target is bounded and effectively ordinal at half-star
steps; squared loss will happily predict 5.3. Ordinal logistic (proportional odds) or beta
regression on the rescaled target respects the bounds for a handful of parameters.
Related and cheap: `p(rating ≥ 4)` from a small regularised logistic model — "82% chance
you'd rate this 4+" is often more decision-useful and more robust than a point estimate.

**Hierarchical multi-task**, if there is ever more than one user. Per-user coefficient
vectors drawn from a population prior; new users warm-start from the population mean
instead of a hand-written prior. Today's design matrix is already the multi-task design
matrix, so this is a growth path, not a rewrite.

### The ensemble schedule

Members: `prior` · `ridge` · `knn` · `gp` · `ranker` (· `catboost`/`rf` above their
thresholds). Weights from simplex-constrained stacking on LOO predictions, pulled toward
`n`-appropriate defaults so the weights themselves don't overfit:

| `n` | Behaviour |
|---|---|
| `< 8` | prior only — no fitting attempted |
| `8–24` | prior-dominant, heavily-penalised ridge, light kNN |
| `25–59` | ridge-dominant, kNN + GP contributing |
| `60–149` | LOO-stacked weights, with a floor on the prior weight |
| `≥ 150` | CatBoost joins at depth 2–3, low weight |

The prior weight never reaches zero — it is what keeps predictions sane on compositions
unlike anything in the log.

### What matters more than the model choice

At `n < 100` the **feature representation dominates**. Swapping ridge for CatBoost moves
MAE by hundredths; fixing unseen-note resolution so a novel composition gets real
features instead of zeros moves it far more.

And whichever model wins, the non-negotiable check is whether it beats **"always predict
this user's mean rating"** under *grouped* leave-one-out. That baseline achieves MAE ≈ 0.5
on skewed ratings and is genuinely hard to beat. Grouped, because *Bleu EDT* and *Bleu
EDP* on opposite sides of a split leaks the target.

---

## Designing the explainability layer

### The short answer

**Directly from the model — but only because the model was chosen to make that
possible.** The core is a penalised linear model, so `prediction = μ + Σⱼ wⱼxⱼ` and the
per-feature contributions sum to the prediction with *zero residual*. That is not an
approximation of an explanation; it is the computation itself, read back. A deterministic
templating layer turns those numbers into sentences.

So explainability is not a fifth component bolted on at the end — it is a **model
selection constraint**. With a tree core you would be choosing between SHAP
approximations and unstable importances over an opaque model. The correct move is to not
be stuck.

### Why not feature importance

Global importance answers "what does this model use in general", which is the wrong
question for "why did *this* perfume score 4.1". It is also **unsigned** — it says citrus
matters, not whether you like it. And at `n < 100` importances from any tree model
reorder across random seeds, so no user-facing taste profile can rest on them.

### Why not SHAP

This is the reflex answer, so concretely:

- **For a linear model SHAP reduces to exactly `wⱼ(xⱼ − E[xⱼ])`** — a dependency and a
  background-distribution choice added to compute what the coefficients already give you
  exactly.
- **The background distribution is badly estimated here.** Every SHAP value is relative
  to `E[x]`, estimated from 80 rows with `p ≈ 83`. The reference point is noisy and all
  attributions inherit it.
- **Feature independence is violated by construction.** `B1` is a weighted sum of `B2`.
  SHAP splits credit among collinear features in ways that are mathematically defensible
  and explanatorily nonsense: "citrus overall +0.2, citrus in the opening +0.2"
  double-counts to a reader who thinks those are the same thing.
- **KernelSHAP/TreeSHAP approximate.** Approximating an unstable model at `n=80` is noise
  on noise.

SHAP is right when you inherited a black box. Reach for it if CatBoost later joins the
ensemble and you want to interrogate *that member* — not as the explanation surface.

### Why not a rule engine

**No if it decides what's true, yes if it only decides wording.** A rule engine that
asserts preferences is precisely the legacy failure: `compute_learned_prefs` marked a note
"liked" if it appeared in *any* perfume rated ≥ 4, then stated that as fact. The rule to
hold is:

> Rules may control **phrasing**. Every **claim** originates in a fitted coefficient.

### Five mechanisms, each answering a different question

| # | Mechanism | Question | Example it produces |
|---|---|---|---|
| 1 | exact additive attribution | *why this score?* | "Citrus in the opening: +0.42. Animalic base: −0.31." |
| 2 | layer decomposition | *which phase of the wear?* | the top/heart/base bars, with real numbers |
| 3 | leave-one-note-out counterfactual | *what's the culprit?* | "Without the cumin this would be 4.3, not 3.1." |
| 4 | neighbour evidence | *what is this like?* | "Closest to X (you rated 4.5); shares vetiver, bergamot." |
| 5 | global taste profile | *what do I like generally?* | "You consistently rate amber bases highly (7 fragrances)." |

Notes on three of them:

**1** — raw per-feature output is not an explanation; 83 numbers explain nothing.
Aggregate into user-meaningful buckets (family × layer) and cap at the top 3 positive and
top 2 negative.

**3** — one solve per note removed, and the most *actionable* explanation type: the only
one that tells the user something they can do something about.

**4** — "shares several characteristics with fragrances you rated highly" is unfalsifiable
as phrased. Ground it: named row, named notes. This is the one job k-NN genuinely earns
its place for.

### The gate is the most important decision in this layer

Show a profile claim only when **all** of:

- support ≥ 3 rated perfumes containing that family
- the posterior credible interval excludes zero
- the effect size clears a minimum threshold

Otherwise the honest output is *"not enough data yet."*

A preference-learning product loses trust the moment it tells a user something about
themselves that isn't true, and it does not get it back. The legacy engine asserted
confident preferences from a single observation.

Adverbs come from the numbers, so hedging is automatic rather than a writing decision:

| Support | Effect size | Phrasing |
|---|---|---|
| ≥ 3, CI excludes 0 | small | "you *lean* toward…" |
| ≥ 5 | moderate | "you *tend to* enjoy…" |
| ≥ 8 | large | "you *consistently* rate … highly" |
| below gate | any | omitted entirely |

Carry uncertainty into the explanation itself: *"Predicted 4.1 ± 0.4 — but I don't
recognise osmanthus tincture, so treat this as less certain."* The featuriser already
tracks the unresolved fraction.

### Claims as data, not strings

The explain layer emits structured objects; the presentation layer renders them:

```python
Claim(
    kind="profile",            # profile | attribution | counterfactual | neighbour
    subject=("top", "citrus"),
    effect=+0.42,
    interval=(0.18, 0.66),
    support=7,
    strength="tend_to",
)
```

Three reasons this matters here: the desktop app and the browser page render the *same*
claims rather than each inventing prose; templating is testable without a model; and it
extends the bridge-test pattern, since the JS side must produce identical claims from
identical inputs.

An LLM may polish the prose. It must never originate a claim, and the validation is
mechanical: reject any output containing a number that wasn't in the input claim set.

### How to test that explanations are true

Usually skipped, and it is what keeps the layer honest as the model changes:

- **Attribution completeness.** Assert `μ + Σ contributions == prediction` to
  floating-point tolerance. This one test catches featuriser drift, block-scaling bugs and
  any missing block.
- **Counterfactual agreement.** If an explanation blames the cumin, removing the cumin must
  move the prediction in the claimed direction by roughly the claimed magnitude.
- **Claim stability.** Refit after adding one rating; a surviving claim must not flip sign.
  Track claim churn per new rating — high churn means the gate is too loose, and you will
  be caught telling the user contradictory things a week apart.
- **Gate honesty.** With a synthetic log of 2 ratings, the profile must produce *zero*
  claims.

### Where it sits

```
explain/
  contributions.py    exact additive attribution, layer decomposition
  neighbors.py        evidence retrieval
  counterfactual.py   leave-one-note-out
  profile.py          global profile: gating + credible intervals
  narrate.py          Claim -> templated English
```

It reads a trained artifact plus a feature vector and emits claims. It holds no scoring
rules and no scent semantics.

Don't persist the rendered sentences. `predictions.csv` already carries `model_version`
and `spec_hash`, which is exactly enough to regenerate claims for any past prediction.
Stored prose would be a second source of truth that can silently disagree with the model.

---

## Designing for features you haven't built yet

### The principle

Extensibility here comes from **representational choices**, not from indirection. Each
planned feature becomes an increment rather than a rewrite because of one specific
decision about how data is shaped — and each of those decisions costs roughly nothing
today. Abstraction layers, plugin frameworks and generic pipelines cost something every
day and buy very little of it.

| Future feature | The decision that makes it cheap | Cost if you get it wrong |
|---|---|---|
| **Layering** | notes as per-layer **mass vectors**; a pair stored **by reference** to its members | a blend can't be expressed as a Composition, so layering needs a parallel code path forever |
| **Collaborative filtering** | features are a **pure function of composition** — no user-specific constants in the featuriser | the design matrix isn't stackable across users; multi-task means re-deriving features |
| **Unseen notes** | the lexicon is **data, not code**; resolution is a stage returning `(semantics, confidence, method)` | improving note handling means editing the scorer, and every improvement risks the model |
| **Explanations** | a **linear core** with **named, human-meaningful** feature blocks | you're stuck approximating an opaque model with unstable coefficients |

The pattern: in each case the extension is *already expressible* in the representation, so
adding it means writing new code, not changing old code.

### Per feature

**Layering** — done, in storage. `combinations.csv` records a pair by reference plus which
perfume went on first, and ratings and predictions are polymorphic over their subject. A
pair *is* a `Composition` once mass vectors can be blended, so Stage 1 (predict the blend
with the existing model) ships the day the featuriser exists, with zero new data. The
residual correction comes later. No schema change needed.

**Collaborative filtering** — the load-bearing decision is fitting on the **residual from
the user's mean rating**, and never letting the featuriser see a user. Today's design
matrix *is* the multi-task design matrix: with N users you stack per-user rows and draw
per-user coefficient vectors from a population prior.

The specific thing that breaks this is any user-dependent quantity leaking into features
— normalising a column by that user's observed range, or extending the lexicon from
ratings rather than descriptions. Both make the feature space non-comparable across users,
and both are easy to do by accident.

**Unseen notes** — the resolution cascade is a stage with a typed output, so adding a step
touches one file and changes no model code. The legacy engine had this backwards:
`_DEFAULT_LIKED` was a Python `set` literal *inside the scorer*, so note semantics and
scoring rules were the same code and neither could change independently.

**Explanations** — four of the five mechanisms are different aggregations of one
contribution vector, so they arrive nearly free once the linear core exists. The
constraint is that feature names stay user-meaningful (`B2[top, citrus]`, not
`embedding_37`).

### The invariants that make all of them cheap

1. **One pure-function seam: `Composition → (vector, spec_hash)`.** Everything upstream is
   data (lexicon, log), everything downstream is parameters (coefficients). Every planned
   feature enters on one side or the other, never in the middle.
2. **A fit/predict boundary with a versioned artifact.** The one not yet built, and the
   one that matters most for extension: without it there is no *place* to put a new model.
   It is also what makes the spec hash meaningful — a stored model refuses to load against
   a changed feature spec, so a lexicon edit cannot silently invalidate coefficients.
3. **An append-only log with a schema version.** Migrations become mechanical rather than
   destructive. `_meta.json` refuses to open a log written by an unknown version, which
   turns "add a user dimension" into a migration instead of a corruption.
4. **Predictions in their own table.** In the CF context specifically: with multiple users
   a self-training loop doesn't just degrade one model, it propagates one user's output
   into the population prior. `training_set()` having no path to `predictions.csv` makes
   that structurally impossible rather than something to remember.
5. **An evaluation harness with the mean baseline — the extensibility feature nobody
   recognises as one.** Every roadmap item is a *claim* that something improves
   prediction. Without grouped LOO and a "beat the user's mean" gate, each addition is a
   coin flip, and you will accumulate changes that make the system worse while feeling
   like progress. Rank this above any individual feature.

### What not to build now

The failure mode for this kind of planning is over-abstraction, so explicitly:

- **No model plugin/strategy framework.** One `fit → Artifact`,
  `Artifact.predict → (estimate, interval, evidence)` interface covers ridge, GP, kNN and
  CatBoost alike.
- **No `user_id` columns yet.** Dead weight until a second user exists, and the version
  guard makes adding them later safe. Speculative schema is still schema to maintain.
- **No abstract base classes with one implementation.**
- **No generic feature-pipeline DSL.** Named blocks in one function are more readable and
  equally extensible.

### What isn't free

- **Multi-user is a v1→v2 schema migration.** Mechanical, with the pattern already
  established — but real work, not a config change.
- **`B6` note-identity columns change dimension as the log grows**, which invalidates
  stored models. The spec hash makes that safe rather than silent, but it means
  retraining, not loading.
- **The browser mirror doubles every schema change.** The standing cost of a standalone
  offline page; the bridge test is what stops it being a silent cost.

### Build order

1. `lexicon/` + `features/` — blocks everything else
2. `model/` + `eval/` — the artifact boundary and the baseline gate
3. `explain/` — nearly free once 2 exists
4. Layering Stage 1 — needs no new data; "best pairs in your collection, ranked" is the
   highest delight-per-effort item on the list
5. Collaborative filtering — only when there is genuinely a second user

Steps 1–2 are where the actual risk lives. Everything after is additive by construction,
which is the point of the decisions above.
