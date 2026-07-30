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
