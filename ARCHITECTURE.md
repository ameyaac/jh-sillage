# Sillage — Architecture

A **perfume preference prediction system**, not a recommender. The system learns one
user's utility function over *fragrance composition space* and can score any
composition — including one that has never existed.

The distinction matters architecturally. A recommender ranks items from a catalog and
leans on co-occurrence between users. Here there is exactly one user, no catalog, and
every item at prediction time is effectively cold. So the entire problem is **item
cold-start**, which is precisely the regime where content-based models work and
collaborative filtering cannot function at all.

---

## 0. The governing constraint

`n < 100`, and the natural feature space has 500–3000 distinct notes. Every design
decision below follows from `p >> n`:

- We cannot learn per-note effects. We learn per-*family* effects and treat individual
  notes as noisy observations of families.
- We cannot use a model that estimates interactions from data. We inject the
  interactions we know about as features.
- We cannot afford a held-out test set. Evaluation is leave-one-out, and it must be
  benchmarked against "always predict the user's mean" — a surprisingly strong baseline
  when ratings are skewed high.
- Shrinkage is not a regularization detail, it is the core algorithm.

---

## 1. Feature representation

### 1.1 The central abstraction: `Composition`

Everything is a **layered note-mass vector**, not a perfume:

```python
Composition(
    layers = {
        "top":    {"bergamot": 0.5, "pink pepper": 0.5},
        "middle": {"jasmine": 1.0},
        "base":   {"vetiver": 0.34, "amber": 0.33, "vanilla": 0.33},
    },
    perfume_type   = 3,   # categorical id
    perfume_medium = 1,   # categorical id
)
```

Mass within each layer is normalized to sum to 1. This is deliberate: note lists are
hand-entered and vary wildly in length, and a perfume listed with 8 base notes is not
"more woody" than one listed with 3. Normalizing makes features invariant to
transcription verbosity.

Choosing note-*mass* over a note-*set* is the single most important architectural
decision in this document, because it is what makes layering (§8) fall out for free. A
layered pair is not a special case — it is just another mass vector.

### 1.2 Feature blocks

The featurizer emits named blocks. Blocks matter because each gets its own shrinkage
strength (§4.3).

| Block | Dim | Content | Role |
|---|---|---|---|
| `B1` pooled family | 14 | Family intensity summed over layers with perceptual layer weights | Dense, low-variance workhorse |
| `B2` per-layer family | 42 | Family intensity per layer (3 × 14) | Lets the model learn "citrus **openings**" as distinct from citrus anywhere |
| `B3` perceptual axes | 8 | sweet, fresh, warm, woody, green, spicy, animalic, powdery — mass-weighted | Densest block; generalizes best to unseen notes |
| `B4` structure | 7 | note counts, layer balance, family entropy, unknown-note fraction, mean note rarity | Captures "busy" vs "linear" compositions |
| `B5` categoricals | ~12 | `perfume_type` one-hot, `perfume_medium` one-hot + an ordinal intensity projection | Concentration/format effects |
| `B6` note identity | dynamic | One column per note with **≥3 observations in this user's own log** | Where genuine per-note signal is allowed to enter, gated by support |

~83 static features plus a slowly growing `B6`. Yes, `p > n`. That is expected and
handled; it is not a bug to be engineered away by deleting features, because each block
carries information the others don't.

**Soft family membership.** A note does not belong to one family. Bergamot is mostly
citrus with a fresh edge and a whisper of floral; vetiver is woody + earthy + green;
tonka is gourmand + warm + powdery. The lexicon stores a *distribution* over families
per note, summing to 1. Hard one-note-one-family tables (what the old `predictor.py`
implied) throw away real information and produce worse features.

**Layer weights.** We do *not* hardcode "heart notes matter 1.3×" the way the old
scorer did. `B1` uses fixed perceptual weights only as a smoothing prior; `B2` gives the
model the raw per-layer signal so it can learn *this* user's layer sensitivity. Some
people buy for the dry-down, some for the first ten seconds.

---

## 2. Handling previously unseen notes

Unseen notes are the normal case, not the edge case. The resolution cascade — each step
returns `(family_distribution, axes, confidence, method)`:

1. **Normalize** — lowercase, strip parentheticals, drop qualifiers (`absolute`,
   `accord`, `essence`, `extract`, `oil`, `notes`), singularize.
2. **Exact lookup** in the canonical lexicon. `confidence = 1.0`
3. **Alias table** — `agarwood → oud`, `ambroxan → ambergris`. `confidence = 1.0`
4. **Fuzzy match** (edit-distance ratio ≥ 0.88, length-guarded) — catches typos and
   spelling variants. `confidence = 0.9`
5. **Head-noun morphology** — the last token usually carries the family:
   `* wood → woody`, `* blossom|flower → floral`, `* musk → musk`,
   `* pepper → spicy`, `* leaf → green`, `* resin|balsam → resinous`,
   `* berry|fruit → fruity`, `* moss → mossy`. `confidence ≈ 0.7`
6. **Modifier hints** — `sea|marine|ocean → aquatic`, `burnt|smoked → smoky`,
   `candied|sugared → gourmand`. `confidence ≈ 0.6`
7. **Subword similarity** — character 3-gram cosine against the lexicon; borrow the
   best match's *family distribution*, never its identity. `confidence ≈ 0.5`
8. **Unknown** — contributes nothing to `B1`–`B3`, increments
   `B4.unknown_fraction`, and widens the predictive interval.

Three properties this buys:

- **Graceful degradation, never silent guessing.** Every prediction reports which notes
  it couldn't resolve. "Predicted 4.1 ± 0.4, but I don't recognize *osmanthus tincture*
  — treat this as less certain" is a far better product than a confident 4.1.
- **Generalization lives in `B3`.** An unseen note resolved only to "woody, warm" still
  lands in the right region of a space where the user's woody/warm preference is
  already estimated. This is why perceptual axes are worth maintaining even though
  they're only 8 columns.
- **Human-in-the-loop growth.** The real fix for an unknown note is asking. Unresolved
  notes queue up in the UI with a "what does this smell like?" prompt (pick families /
  drag axes), writing into a user-extension lexicon that overrides the base one. A
  lexicon that grows with use beats any imputation heuristic.

Important: extending the lexicon from *note descriptions* is fine. Extending it from
*ratings* would leak the target into the feature space — never do that.

---

## 3. Should note families be incorporated?

**Yes — they are load-bearing, not a nice-to-have.** Three independent reasons:

**Statistical.** With 80 ratings, learning 14 family coefficients gives ~6 observations
per parameter: tight but workable. Learning 500 note coefficients from 80 ratings is
not estimation, it's memorization. Families are a hierarchical prior over notes:
`note → family → rating`. Each note "borrows strength" from every other note in its
family. This is the mechanism that makes prediction from 30 ratings possible at all.

**Transfer to unseen notes.** A note the user has never rated is still scoreable if it
maps into families they have. Without families, an unseen note carries literally zero
information and the model can only fall back to the mean.

**Explainability.** "You tend to enjoy citrus openings" *is* the coefficient on
`B2[top, citrus]`. The explanation vocabulary and the feature vocabulary are the same
vocabulary — no post-hoc rationalization, no SHAP approximation over an opaque model.

Families alone are too coarse, which is why `B6` exists: once a note has ≥3
observations, it earns its own column and can deviate from its family. That is partial
pooling — bias-heavy family representation at low support, variance-heavy note
representation at high support, smoothly interpolated by the ratio of the two blocks'
penalties.

Family taxonomy used (14): citrus, floral, white-floral, fruity, green, aromatic,
spicy, woody, resinous, amber, gourmand, musk, animalic, aquatic.

---

## 4. Model choice

### 4.1 The short answer

**None of CatBoost, XGBoost, Random Forest, or kNN as the primary model.** The core is a
**Bayesian ridge regression with an informative prior mean**, sitting inside a small
ensemble whose mixing weights adapt to `n`.

### 4.2 Why not the tree models, specifically

- **Sample starvation.** A depth-6 tree on 80 rows leaves under 2 effective samples per
  leaf. Boosted ensembles of such trees fit noise with great enthusiasm.
- **Wrong inductive bias.** Preference is roughly *monotone and smooth* in "how much
  citrus is in this" — a linear model gets that for one parameter. A tree must
  approximate the same smooth response with a staircase of splits, spending scarce
  data to rediscover a structure we already know exists.
- **Unstable attributions.** Feature importances from an 80-row GBM reorder across
  random seeds. You cannot build a user-facing "your taste profile" page on a quantity
  that changes when you retrain.
- **No extrapolation.** Trees cannot predict outside the convex hull of observed
  feature values — bad when the whole point is scoring novel compositions.

Ranked, for when the dataset does grow: **CatBoost > Random Forest > XGBoost** for this
problem. CatBoost's ordered boosting genuinely reduces target leakage on small data and
its native categorical handling suits `perfume_type`/`perfume_medium`. Introduce it
above roughly **150 samples**, at `depth=2..3`, high `l2_leaf_reg`, low learning rate —
and keep it as an *ensemble member*, never a replacement for the linear core. Random
Forest is a reasonable low-variance nonlinear member above ~60 samples, weighted low.
XGBoost offers nothing CatBoost doesn't here.

**kNN** stays in the ensemble at low weight, and it earns its place mostly for
explanations: retrieved neighbours are the evidence behind "this resembles fragrances
you rated highly." Similarity is cosine on the `B1+B3` vector, plus Tanimoto on the raw
note sets as a second view — never Euclidean on the full sparse space.

### 4.3 Why Bayesian ridge, concretely

- **`p > n` is fine by construction** — the penalty makes the solution unique and stable.
- **Block-specific shrinkage.** Each feature block gets its own `λ_g`, implemented by
  scaling block `g`'s columns by `1/sqrt(λ_g)` before a single global ridge solve — the
  standard equivalence, exact and cheap. `B1`/`B3` are lightly penalized, `B2`/`B6`
  heavily. This is how the family/note partial pooling of §3 is actually realized.
- **The coefficients are the explanation, exactly.** `prediction = μ + Σ_j w_j x_j`, and
  the contributions sum to the prediction with no residual. Nothing approximate.
- **Non-zero prior mean solves cold-start.** Onboarding answers ("I love citrus, I hate
  animalic") set prior *means* on the corresponding family coefficients rather than
  shrinking toward zero. At `n=0` the model reproduces the user's stated taste; as
  ratings arrive the likelihood overrides the prior smoothly, family by family, at a
  rate proportional to evidence. This is what the old hand-tuned weight tables were
  groping toward, expressed properly.
- **Calibrated predictive variance** for the `±` shown to the user and for
  uncertainty-aware ensemble weighting.

We fit on the **residual from the user's mean rating**, not raw stars. This removes the
intercept problem and each user's idiosyncratic scale use.

### 4.4 Gaussian Process — the strong alternative

For `20 ≤ n ≤ 200` a GP is arguably the best-fitting tool available:

```
k(A, B) = a · Tanimoto(notes_A, notes_B)
        + b · RBF(family_axis_vector_A, family_axis_vector_B)
        + c · 1[type_A = type_B]
```

Nonparametric, natively calibrated, and the kernel is where domain knowledge about note-
set similarity gets injected directly rather than being learned. It is included as an
ensemble member. Its weakness is interpretability, which is covered by the linear
member; a GP does offer one interpretable artifact — the posterior weight on each
training perfume, i.e. "which of your ratings drove this prediction."

### 4.5 Auxiliary heads

- **Pairwise ranker.** Absolute star ratings are noisy and drift over months; *relative*
  judgments are far more stable. From 60 ratings you get ~1770 pairs. Fitting a linear
  ranker on feature *differences* (which cancels the intercept and any additive rating
  bias) is a large effective-sample-size win, and its score blends into the regression.
  One of the highest-value-per-line additions at this scale.
- **`p(rating ≥ 4)`** from a small regularized logistic model. "82% chance you'd rate
  this 4+" is often more decision-useful, and more robust, than a point estimate.

### 4.6 The ensemble

Members: `prior` (unfitted, onboarding-driven) · `ridge` · `knn` · `gp` · `ranker`
(· `catboost`/`rf` above their sample thresholds).

Weights come from **simplex-constrained stacking on leave-one-out predictions**, with a
Dirichlet-style pull toward `n`-appropriate defaults so the weights themselves don't
overfit:

| `n` | Behaviour |
|---|---|
| `< 8` | prior only — no fitting attempted |
| `8–24` | prior-dominant, heavily-penalized ridge, light kNN |
| `25–59` | ridge-dominant, kNN + GP contributing |
| `≥ 60` | LOO-stacked weights, with a floor on the prior weight |

The prior weight never reaches zero. It is the thing that keeps predictions sane on
compositions unlike anything in the log.

---

## 5. Training and evaluation

### 5.1 Protocol

**Leave-one-out cross-validation.** At `n < 100`, 5-fold wastes 20% of already-scarce
data per fit. LOO is affordable — and for ridge it's *free*: the exact LOO residual is
`r_i / (1 - h_ii)` from the hat matrix diagonal, so all `n` folds cost one solve. Refit
on every new rating; it takes milliseconds.

### 5.2 Metrics

- **MAE** — in stars, the number to quote to a user.
- **RMSE** — penalizes the confident misses.
- **Spearman ρ** — do we *rank* correctly? Usually what actually matters ("which of
  these three should I buy").
- **Pairwise accuracy** on pairs with `|Δrating| ≥ 0.5`, so ties don't inflate it.
- **PICP** — does the 80% interval actually cover 80%? An uncalibrated `±` is worse
  than none.

### 5.3 Mandatory baselines

Every report includes: constant 3.0 · **user mean** · user mean + note-overlap
heuristic. If the model doesn't beat the user-mean predictor on MAE, it has learned
nothing. With ratings skewed toward 3.5–4.5 the mean predictor achieves MAE ≈ 0.5 and
is easy to lose to. **This check is the difference between a working system and a
self-flattering one**, and it's the one most often skipped.

### 5.4 Leakage traps

- **Duplicate perfumes and flankers.** The same fragrance logged twice, or *Bleu EDT*
  and *Bleu EDP*, put near-identical rows in train and test. Group by normalized
  brand+name and use **grouped** LOO.
- **Hyperparameter leakage.** Tuning `λ` on the LOO you then report is optimistic.
  Nested LOO for the honest number; report both.
- **Lexicon leakage.** Extending the lexicon from note descriptions: fine. From
  ratings: leaks the target.

### 5.5 Beyond a single number

- **Learning curve** over `n` — tells the user when to start trusting it, and is the
  most persuasive thing you can show them.
- **Prefix evaluation** (train on first `k`, test on `k+1`) — detects taste drift, which
  LOO's shuffling hides. If prefix error is much worse than LOO error, tastes are
  moving and recent ratings should be up-weighted.
- **Artifact versioning.** Persist the model with a hash of the feature spec and
  lexicon; refuse to load a model whose spec no longer matches.

---

## 6. Explainability

Five mechanisms, each answering a different question.

**1. Exact additive attribution** — *why this score?* From the linear core,
`prediction = μ + Σ_j w_j x_j`, grouped by family and layer. Contributions sum exactly
to the prediction. → *"Citrus in the opening: +0.42. Animalic base: −0.31."*

**2. Layer decomposition** — *which phase of the wear?* Contributions summed per layer,
feeding the existing top/heart/base breakdown bars with numbers that finally mean
something.

**3. Leave-one-note-out counterfactuals** — *what's the culprit?* Re-predict with each
note removed; the delta is that note's marginal effect *in this composition*.
→ *"Without the cumin this would be 4.3 instead of 3.1."* Cheap, and the most
actionable explanation type.

**4. Neighbour evidence** — *what is this like?* Retrieved nearest rated perfumes with
their shared notes. → *"Closest to Fragrance X (you rated 4.5); shares vetiver,
bergamot, ambergris."* This is the requested "shares several characteristics with
fragrances you rated highly", grounded in actual rows rather than asserted.

**5. Global taste profile** — *what do I like, in general?* Family affinities with
posterior credible intervals **and support counts**, gated: shown only when support ≥ 3
rated perfumes and the interval excludes zero. Otherwise it says *"not enough data
yet."* → *"You consistently rate vanilla and amber bases highly (based on 7
fragrances)."*

That gate is the most important line in this section. The old heuristic asserted
confident preferences it had no evidence for. A preference-learning product loses trust
the moment it tells a user something about themselves that isn't true.

Phrasing is **template-based, driven by the numbers**, with adverbs keyed to effect size
and support ("tend to" vs "consistently"). Deterministic and auditable. An LLM may
polish the prose, but every *claim* must originate in a coefficient.

---

## 7. Problem formulation

**Single-user content-based preference regression** — learning a utility function over
composition space. Concretely:

- **Not collaborative filtering.** One user, no co-occurrence matrix. CF cannot score an
  item nobody has rated; that's the requirement.
- **Not classical recommendation.** There is no catalog to rank. The system is a
  *function* over compositions, and the catalog (if any) is just a set of points to
  evaluate it at. This inversion is what makes novel-composition scoring possible.
- **Regression as the primary objective**, on the residual from the user's mean.
- **Pairwise ranking as an auxiliary objective** (§4.5) — a large effective-sample win,
  since ranking is both more robust and closer to the real decision.
- **Optional classification head**, `p(rating ≥ 4)` — the buy/skip framing.

**The growth path is hierarchical, not a rewrite.** With many users this becomes
multi-task: per-user coefficient vectors drawn from a population prior (hierarchical
ridge), or a factorization machine over (user × family) with shared note embeddings.
New users then warm-start from the population mean instead of a hand-written prior, and
content features stay in place so unseen perfumes keep working. Critically, **today's
design matrix is already the multi-task design matrix** — the v1 feature space is what
you'd stack per user. Nothing here has to be thrown away to get there.

---

## 8. Layering (combinations of two perfumes)

This is why §1.1 chose note-mass vectors. A layered pair **is not a special case** — it
is a `Composition`.

Storage for this exists already (§9.1): `combinations.csv` records a pair by reference to
its two members plus which one went on the skin first, and its ratings live in the same
`ratings.csv` as everything else. Nothing here needs a schema change to start collecting
layering data — only the blend and interaction code below.

### 8.1 Blending

`blend(A, B, w_A, w_B) → Composition`, mixing per-layer masses with weights derived
from medium and concentration (an attar under an EDT is not 50/50). Layering is
physically order-dependent: the perfume sprayed second dominates the opening, while the
dry-down is close to the union of both bases. So the top layer mixes with
order-dependent weights and the base layer near-symmetrically. When order is
unspecified, predict both orders and average — keeping the model symmetric in `A, B`
except through an explicit order feature.

### 8.2 Three stages

**Stage 1 — additive baseline (ships immediately, zero new data).** Predict on the
blended composition with the existing model. Honest, often decent, and available today.

**Stage 2 — interaction features.** Layering is genuinely non-additive; clashes and
synergies are real. Add hand-crafted features:
- *Clash / harmony pairs* from the outer product of A's and B's family profiles, reduced
  to a curated shortlist (gourmand × aquatic, animalic × gourmand, smoky × fresh-citrus
  fight; woody × amber, citrus × aromatic harmonize).
- *Overload* — total note count, summed sweetness, summed projection → saturation penalty.
  Most bad layering is simply too much.
- *Redundancy* — cosine similarity of the two family profiles. Near-identical pairs add
  nothing but risk nothing; very dissimilar pairs are where clash risk lives.
- *Shared anchor* — a common base note acts as a bridge and predicts harmony.

**Stage 3 — residual model.** Once real layering ratings exist, fit
`Δ = rating(A⊕B) − f(blend(A,B))` on the interaction features alone.

The key insight: **never model layering from scratch — model the correction.** Learning
`rating(A⊕B)` directly would need hundreds of samples. Learning a 4-coefficient
residual around an already-good baseline is tractable with 10–20 layering ratings,
because the baseline has absorbed all the composition-level structure.

### 8.3 Free consequences

- **Explanations transfer.** Contributions attribute to notes, and each note in a blend
  remembers its parent perfume → *"the vanilla from A with the oud from B is the part
  you'd like; the two citrus openings just cancel."*
- **Combinatorial search is cheap.** 40 owned perfumes → 780 pairs, all scoreable in
  milliseconds. "Best layering combinations in your collection, ranked" needs **no new
  data at all** — it's Stage 1 applied 780 times. This is the highest
  delight-per-effort feature in the roadmap.

---

## 9. Module layout

```
sillage/
  lexicon/
    notes.json          canonical note → soft family distribution + perceptual axes
    aliases.json        synonym / spelling-variant table
    families.py         taxonomy, layer weights, clash & harmony pairs
    store.py            base lexicon + user extensions, merge & lookup
  features/
    normalize.py        note string → canonical form
    resolve.py          the §2 unseen-note cascade
    composition.py      Composition dataclass, blending, note-mass algebra
    featurize.py        Composition → named feature vector; versioned FeatureSpec
  model/
    baseline.py         mean / prior-only predictors
    ridge.py            block-shrunk Bayesian ridge, informative prior mean, exact LOO
    knn.py              cosine + Tanimoto similarity model
    gp.py               GP with the composite kernel
    ranker.py           pairwise linear ranker
    ensemble.py         n-adaptive simplex stacking
    registry.py         train / persist / load, spec-hash validation
  explain/
    contributions.py    exact additive attribution, layer decomposition
    neighbors.py        evidence retrieval
    counterfactual.py   leave-one-note-out
    profile.py          global taste profile with support gating + CIs
    narrate.py          numbers → templated English
  eval/
    cv.py               grouped LOO, metrics, baselines, learning curve
    report.py           formatted evaluation output
  layering/
    blend.py            §8.1
    interactions.py     §8.2 clash / overload / redundancy features
    residual.py         §8.3
  data/                 ── BUILT ──
    schema.py           record types + validation (Perfume, Combination, RatingEvent, Prediction)
    store.py            RatingLog: five append-only tables
    portable.py         CSV export/import — the browser bridge
    migrate.py          legacy CSV + taste profile -> five tables
  export/
    to_js.py            emit lexicon + trained model as JS for the page
```

And the browser surface, which is split rather than inlined:

```
index.html              markup only
web/
  styles.css
  js/csv.js             RFC 4180 read/write
  js/store.js           mirror of data/schema.py, in localStorage
  js/predict.js         the seam where the exported model lands
  js/app.js             DOM wiring, log table, backup & restore
```

Python is the source of truth. The page reimplements featurization, the ridge solve,
kNN and the explanation layer in JS against a lexicon bundle generated by
`export/to_js.py`, so the browser app stays standalone and self-training while both
surfaces agree on semantics.

That duplication is a real cost, and the only thing keeping it honest is a test:
`tests/test_bridge.py` runs `web/js/store.js` under node, has it export CSVs, and
imports them with the Python reader. Field names, column order, the note separator,
timestamp format and number formatting all have to match; nothing at runtime enforces
it, so the test does.

### 9.1 What the storage layer actually looks like

Five append-only tables, not one mutable one:

| Table | Holds | Why separate |
|---|---|---|
| `perfumes.csv` | immutable compositions, notes exactly as typed | normalisation is the feature layer's job, so the log stays lossless |
| `combinations.csv` | layered pairs, **by reference** to their members | the blend is derived, so a lexicon fix propagates instead of leaving stale copies |
| `ratings.csv` | timestamped rating *events* | re-rating appends; drift is measurable rather than overwritten |
| `predictions.csv` | what the model claimed, with version + spec hash | accountability, and it keeps predictions out of the training path |
| `preferences.csv` | taste the user *declared*, on a signed [-1,1] scale | §4.3's prior mean, expressed as data; off the rating scale so it can never be read as a target |

Ratings and predictions are polymorphic over their subject (`perfume` or
`combination`), because a layered pair is judged on the same 1–5 scale and the
evaluation code should not need two of everything.

The load-bearing property: **`training_set()` reads perfumes and ratings and has no code
path to `predictions.csv`.** §10's self-training bug is not fixed by discipline here, it
is unreachable. `pair_training_set()` is separate for the same reason — pair ratings
would contaminate the composition-level fit if they landed in the single-perfume target.

---

## 10. What was replaced, and why it had to go

The previous engine (`model/predictor.py`, and the same logic inlined in `index.html`)
scored perfumes with hardcoded liked/disliked note sets and hand-tuned multipliers.

Its three fatal problems:

1. **It didn't learn.** `compute_learned_prefs` marked a note "liked" if it appeared in
   *any* perfume rated ≥ 4 — so one good perfume permanently canonized all eight of its
   notes, with no weighting, no support requirement, and no way to ever revise. Notes in
   both a loved and a hated perfume resolved by set-difference, arbitrarily.
2. **Unseen notes scored 0.1** — a small positive constant. Novel compositions drifted
   toward a fixed value regardless of content, which defeats the system's entire purpose.
3. **It couldn't be evaluated.** With no fitted parameters there was nothing to
   cross-validate, so there was no way to know whether any of it worked. The magic
   constants (`1.3` heart weight, `(raw + 1.2) / 3.0` normalization) were unfalsifiable.

It also trained on its own predictions — `compute_learned_prefs` falls back to
`predicted_rating` when `actual_rating` is missing, feeding model output back in as
ground truth. That's a self-reinforcing loop with no corrective signal.

The replacement keeps the one genuinely good idea in it — that onboarding-declared
preferences should seed the model before any ratings exist — and expresses it properly,
as the prior mean of a Bayesian regression that data can override.
