# JH-Sillage

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
[How a perfume becomes numbers](#how-a-perfume-becomes-numbers) · [FAQ](#faq) ·
[Layout](#layout)

---

## Status

| Layer | State |
|---|---|
| `data/` — four-table log, schema, migration, backup/restore | **built**, 46 tests |
| `web/` — browser store, CSV, placeholder scorer, UI | **built**, 27 tests |
| Python ↔ browser bridge | **built**, 4 tests |
| `lexicon/` — note → family/axis data | not started |
| `features/` — normalise, resolve unseen notes, featurise | not started |
| `model/` — prior, ridge, kNN, artifact registry | `predictor.py` is the legacy heuristic |
| `eval/` — grouped LOO + mandatory baselines | not started |
| `export/` — emit lexicon + model bundle for the page | not started |

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
pip install -r requirements.txt        # numpy/scipy/sklearn + customtkinter
sudo apt install python3-tk            # only for the desktop UI
```

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
  _meta.json               # schema version
```

`SILLAGE_DATA_DIR` overrides the location; if you point it inside the repo, add that
path to `.gitignore` yourself, since gitignore cannot expand environment variables.

### Backup and restore

**This is the only irreplaceable thing in the project.** Browser storage is not a
backup — clearing site data, switching device, or opening the page in a private window
loses the log.

In the page, the **Backup & Restore** card does both directions:

- **Download my log** writes all four CSVs. Keep them together; all four are written
  even when empty, because a backup silently missing `ratings.csv` is indistinguishable
  from one where you had no ratings.
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
```

So the round trip works: log on the page → download → `import` into Python to train →
`export` → upload back into the page.

### Why four tables

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

Layered pairs live in the same log:

```python
combo = log.log_combination(a.perfume_id, b.perfume_id, applied_first="b")
log.log_rating(rating=4.0, combination_id=combo.combination_id)
log.pair_training_set()   # [(Combination, Perfume, Perfume, 4.0)]
```

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
Expected. `localStorage` is per-browser. Download the CSVs on the first device and upload
them on the second.

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

**Why four CSVs instead of one?**
See [Why four tables](#why-four-tables). Short version: keeping `predicted` and `actual`
in adjacent columns is what let the old engine train on its own output.

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
  js/store.js         the four tables in localStorage — mirrors data/schema.py
  js/predict.js       PLACEHOLDER scorer; the seam where the real model lands
  js/app.js           DOM wiring, log table, backup & restore
data/
  schema.py           record types and validation
  store.py            RatingLog — append-only reads and writes
  portable.py         CSV export/import, the browser bridge
  migrate.py          legacy single-table CSV -> four tables
model/predictor.py    legacy heuristic, being replaced
ui/                   desktop UI (customtkinter)
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
