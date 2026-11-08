# Trust algorithms for Seraphim — research and recommended design

Date: 2026-11-08. Time box: 30 min. Tags: [Certain] = checked now or standard textbook result; [Likely] = from memory of the paper, not re-read today; [Guessing] = my judgement.

## 1. The problem in truth-discovery terms
- **Object** = one event: `(settlement, predicate, date window)`, for example "RU captured Pokrovsk by 2025-11-20". **Assertion** = one post that says yes (z=+1) or no (z=-1) to the event.
- **Source** = Telegram channel / X / Bluesky account. **Label** = DeepState/ISW outcome, which arrives days to weeks after the post.
- This is binary truth discovery with **partial gold labels, delayed labels, and copying**. The classic algorithms cover each part separately; no single paper covers all three [Likely].

## 2. Literature (with citations)
| Method | Citation | Core idea | Labels? | Copies? | Time? | Calibrated P? | Fit for us |
|---|---|---|---|---|---|---|---|
| Hubs/Authorities, **Sums** | Kleinberg 1999, JACM; Sums as named in Pasternack & Roth 2010 | trust = Σ belief of claims; belief = Σ trust of sources; normalise | no | no | no | no (scores) | baseline only |
| **Average-Log** | Pasternack & Roth 2010, COLING, aclanthology.org/C10-1099 | trust = log(n_claims)·mean belief | no | no | no | no | baseline only |
| **Investment / PooledInvestment** | Pasternack & Roth 2010, COLING | sources "invest" trust in claims, returns grow non-linearly (G(x)=x^g) | no (prior knowledge in their 2010 title paper as constraints) | no | no | no | rewards herding — bad with propaganda clusters [Guessing] |
| **TruthFinder** | Yin, Han, Yu 2007 KDD; 2008 IEEE TKDE 20(6), doi:10.1109/TKDE.2007.190745 | t(s)=mean conf of facts; conf(f)=1−Π(1−t(s)), dampening γ; similar facts support each other | no | no (independence assumed) | no | partly | good ideas: dampening, similarity support |
| **3-Estimates** | Galland, Abiteboul, Marian, Senellart 2010, WSDM, doi:10.1145/1718487.1718504 | adds per-fact difficulty to source error; uses negative statements | no | no | no | no | useful: handles "denials" |
| **LTM** | Zhao, Rubinstein, Gemmell, Han 2012, PVLDB 5(6), arxiv.org/abs/1203.0058 | Bayesian generative; per-source sensitivity **and** specificity (two-coin), Gibbs sampling | priors only | no | no | yes | right model family, Gibbs too slow to build today [Guessing] |
| **CRH** | Li, Li, Gao, Su, Zhao, Fan, Han 2014, SIGMOD, doi:10.1145/2588555.2610509 | minimise Σ w_s·loss(claim, truth); w_s = −log(loss_s/Σloss) | no | no | no | no | heterogeneous types; not needed |
| **CATD** | Li et al. 2015, PVLDB 8(4), vldb.org/pvldb/vol8/p425-li.pdf | confidence interval on source error (χ² bound); small sources get shrunk | no | no | no | no | **key idea for us: shrink sources with few claims** — Beta prior does the same |
| **Survey** | Li, Gao, Meng, Li, Su, Zhao, Fan, Han 2016, SIGKDD Explorations 17(2), arxiv.org/abs/1505.02463 | taxonomy; source dependence, long tail, streaming, semi-supervision | – | – | – | – | read §3–5 |
| **AccuCopy** | Dong, Berti-Equille, Srivastava 2009, PVLDB 2(1), vldb.org/pvldb/2/vldb09-218.pdf | Bayesian test P(s copies t) from shared values; **sharing the same false value is strong evidence of copying**; vote of s discounted by Π(1 − c·P(copy)) over earlier sources | no | **yes** | no | partly | **use the discount formula** |
| Truth + copying over time | Dong, Berti-Equille, Srivastava 2009, PVLDB 2(1) "Truth discovery and copying detection in a dynamic world" | copying and accuracy from update histories (lag, freshness) | no | yes | yes | – | lead/lag idea for "who posted first" [Likely] |
| **Semi-supervised TD** | Yin & Tan 2011, WWW, doi:10.1145/1963405.1963454 | graph regularisation: labelled facts fixed, beliefs smoothed over fact-similarity graph | **yes** | no | no | no | confirms: clamp labelled claims, propagate to similar ones |
| **Dynamic TD** | Li, Li, Gao, Su, Zhao, Fan, Han 2015 KDD "On the discovery of evolving truth", doi:10.1145/2783258.2783277 | source weight updated incrementally with decay as new data arrives | no | no | **yes** | no | **use exponential decay of old evidence** |
| Bayesian TD (online) | Zhao et al. 2012 (above); also streaming variants in the survey | posterior counts as sufficient statistics → online update | priors | no | yes | yes | Beta counts are online by construction [Certain] |
| **Dawid–Skene** | Dawid & Skene 1979, JRSS C 28(1):20–28, doi:10.2307/2346806 | EM over latent truth and per-annotator confusion matrix; one-coin variant = one accuracy per annotator | gold clamping (crowd-kit) | no | no | yes | **our base model (one-/two-coin)** |
| GLAD / MACE | Whitehill et al. 2009 NIPS; Hovy et al. 2013 NAACL | item difficulty (GLAD), spammer detection (MACE) | some | no | no | yes | MACE "spammer" = our "propagandist" idea [Guessing] |
| SLiMFast | Rekatsinas et al. 2017, SIGMOD, doi:10.1145/3035918.3035951 | logistic regression on source **features**; ERM when labels exist | **yes** | no | no | yes | idea: source features (side, language) → prior |
| **TrustRank** | Gyöngyi, Garcia-Molina, Pedersen 2004, VLDB | PageRank from a trusted seed set, damping α≈0.85 | seeds | – | no | no | trust ≠ forwarding; see §3 |
| Anti-TrustRank / distrust | Krishnan & Raj 2006, AIRWeb; Guha, Kumar, Raghavan, Tomkins 2004, WWW | propagate distrust backwards from bad seeds; one-step distrust is safest | seeds | – | no | no | supports "one hop only" |
| **EigenTrust** | Kamvar, Schlosser, Garcia-Molina 2003, WWW, doi:10.1145/775152.775242 | global trust = principal eigenvector of normalised local trust, mixed with pre-trusted peers | seeds | – | no | no | same caveat as TrustRank |
| Label propagation | Zhou et al. 2004 NIPS "Learning with local and global consistency" | F = (1−α)(I−αS)^-1 Y | yes | – | no | no | runaway bounded only by α |

Telegram / war-specific (2022–2026), none of them gives per-channel factual accuracy vs ground truth [Likely]:
- "Telegram as a Battlefield" — 404 pro-Kremlin, 114 anti-Kremlin channels, ICWSM 2025 (ojs.aaai.org/index.php/ICWSM/article/download/35939/38093/40007) [Likely].
- "Graph-Based Detection of Disinformation Narrative Diffusion between Russian and Ukrainian Telegram Channels", arXiv 2607.11894 (2026): forward graph of 98 channels, 1.35 M messages, 26 narrative labels [Likely, from search snippet].
- "Modeling Information Narrative Detection and Evolution on Telegram during the Russia-Ukraine War", arXiv 2409.07684 (2024); "Characterizing and Detecting Propaganda-Spreading Accounts on Telegram", arXiv 2406.08084 (2024) [Likely].
- Gap: these classify **narratives/propaganda**, not **verifiable battlefield claims vs DeepState**. Our validation against DeepState is the novel part for the pitch [Guessing].

## 3. What the literature tells us for this case
1. **Labels exist → use them directly.** Unsupervised fixed-point methods (Sums, Investment, TruthFinder) reward the largest agreeing cluster. In a war where ~half the sources share one side's line, majority = propaganda, so unsupervised trust is unsafe [Guessing, but this is the AccuCopy motivation, Dong 2009].
2. **Copying breaks independence.** Without a discount, 20 channels forwarding one MoD post = 20 votes. AccuCopy: count one origin once; discount later independent-looking repeats by P(copy) [Likely].
3. **Long tail.** Most channels will have <10 resolved claims. CATD and Bayesian priors both shrink these toward a group value. A Beta prior per side is the simplest version [Certain].
4. **Graph propagation is a prior, not evidence.** TrustRank/EigenTrust iterate to a fixed point; in a dense mutual-forwarding cluster this lets one bad label spread to every member. Bound it: one hop, fixed weight ρ<1, and let a source's own resolved record override the prior after κ claims [Guessing; one-step distrust is the recommendation in Guha et al. 2004, Likely].
5. **Time.** Only labels with `t_resolve < t` may be used. Old evidence decays (Li et al. 2015 KDD). Beta pseudo-counts with exponential decay give both [Certain].

## 4. Recommended design: "semi-supervised one-coin Dawid–Skene with Beta side priors, copy discount and decay"
Hand-roll in numpy/pandas (~120 lines). One function `fit(as_of=t)` → source table; one function `predict(event, as_of=t)` → P(true).

### 4.1 Inputs
- `assertions`: rows `(post_id, source, event_id, z∈{+1,−1}, t_post, cascade_id, is_origin)`.
- `labels`: rows `(event_id, y∈{0,1}, t_resolve)` from DeepState/ISW.
- `side[source]` ∈ {pro_ru, pro_ua, media, other} from `all_channels.csv`.
- `cascade_id`: union-find over (a) explicit `forwarded_from` edges, (b) near-duplicates: MinHash/char-5-gram Jaccard ≥ 0.8 within 72 h on the same event. Earliest post in a cascade = origin.

### 4.2 Evidence units (one origin = one vote)
```
unit u = (event e, cascade k)          # all posts in a cascade collapse to one unit
src(u)   = source of the origin post
z(u)     = polarity of the origin post
copiers(u) = other sources in cascade k (they amplified, they did not observe)
```
Independent units on the same event (different cascades) still may be dependent. AccuCopy discount, ordered by `t_post`:
```
λ_u = Π_{earlier units v on e} (1 − c · d[src(u), src(v)])      # c ∈ [0,1]
d[s,t] = (F_st + 0.5) / (F_s + 2)    # F_s = s's resolved-false units; F_st = those where t asserted the same false claim earlier (≤48 h)
```
Shared **false** claims drive d, as in Dong 2009. d uses only labels with `t_resolve < as_of`.

### 4.3 Source accuracy (labelled claims)
Accuracy a_s = P(assertion by s is correct). Prior per side g:
```
m_g = (Σ_{s∈g} A_s + 1) / (Σ_{s∈g} (A_s+B_s) + 2)     # pooled side accuracy (empirical Bayes)
α_s = κ · m0_s ,  β_s = κ · (1 − m0_s)                  # κ = prior strength in pseudo-claims
w(i) = 0.5 ** ((as_of − t_resolve_i) / H)               # decay, half-life H days
correct_i = 1[z_i == (2y_i − 1)]
A_s = Σ_i w(i)·(correct_i · ω_i) ;  B_s = Σ_i w(i)·((1−correct_i) · ω_i)
ω_i = 1 for origin units, η for copier credit (copier amplified a claim that resolved)
a_s = (α_s + A_s) / (α_s + β_s + A_s + B_s)
```
`m0_s = m_g` normally. Cold-start transfer ("second author posted the same thing") changes only the prior mean, once:
```
m0_s = (1 − ρ_s) · m_g + ρ_s · Σ_t W_st · a_t^(0) / Σ_t W_st
W_st  = #units where s copied/repeated t (from cascades);  a_t^(0) = posterior of t computed with m0 = m_g (no recursion)
ρ_s   = ρ · Σ_t W_st / (Σ_t W_st + 5)                         # ρ ≤ 0.5
```
Bound: the transfer moves a_s by at most `ρ · κ/(κ + n_s) · |a_t − m_g|`, where n_s = s's own decayed resolved count. One hop, no fixed-point iteration, so no runaway [Certain, follows from the formula].

### 4.4 Event probability (pending and unlabelled claims)
Naive-Bayes combination, conditional independence after the copy discount. π_type = base rate of true events for that claim type (capture / repelled / strike), from labels before as_of:
```
ℓ_e = logit(π_type) + τ · Σ_{u on e, t_post(u) ≤ as_of} λ_u · ( z_u · logit(a_src(u)) − logit(π_type) )
P(e true) = sigmoid(ℓ_e)          # τ ∈ (0,1] temperature against residual dependence
```
Check: one source asserting yes gives P = a_s; one denial gives 1 − a_s [Certain].

### 4.5 Semi-supervised step (optional, keep only if walk-forward improves)
Pending events enter the counts as soft labels, like Dawid–Skene E/M with gold clamped (crowd-kit does the same clamping):
```
for it in range(5):
    p_e^(−s) = P(e true) computed WITHOUT s's own unit        # leave-one-out: no self-confirmation
    A_s += γ · Σ_pending w · p_e^(−s)    (for z=+1; use 1−p for z=−1)
    B_s += γ · Σ_pending w · (1 − p_e^(−s))
    recompute a_s
```
γ ∈ {0, 0.2, 0.5}. A pending event with a single source gives p = π, so it adds no signal. Default γ = 0 for the demo [Guessing].

### 4.6 Outputs
- `trust.db/source_trust(as_of, source, a_s, A_s, B_s, n_resolved, side, prior_mean)` — evidence = the list of resolved unit ids.
- `trust.db/claim_cred(as_of, event_id, p_true, n_units, n_posts)` — each unit keeps its post ids, so P traces to record spans.

### 4.7 Extension if time remains: two-coin (LTM-style)
Split a_s into a_s⁺ (precision of "captured" claims) and a_s⁻ (precision of denials). Pro-RU channels likely over-claim captures but may be accurate on denials [Guessing]. Same Beta formulas, two counters per source.

## 5. Tuning: walk-forward, no leakage
```
folds: T_k = 2023-01-01 + k·30d, k = 0..K
fit(as_of = T_k)                         # labels with t_resolve < T_k, assertions with t_post < T_k
predict events first asserted in [T_k, T_k+30d), each at as_of = t_first + 24h   # only units posted by then
score on their eventual y (events still unresolved are dropped from the score)
hold out the last 2 months; never use them for tuning
```
Grid (small, numpy is fast): κ ∈ {2,5,10,20,50}, H ∈ {30,90,180,∞} days, τ ∈ {0.3,0.5,0.7,1}, c ∈ {0,0.5,0.9}, η ∈ {0,0.5}, ρ ∈ {0,0.25,0.5}, γ ∈ {0,0.2}. ~2,900 configs; or random search with 200.
- **Primary metric: log loss** (mean over folds, weighted by events). **Report Brier** and a reliability plot (10 bins) for the demo. Log loss punishes confident errors, which is the propaganda failure mode [Certain about the metric property].
- Also report AUC (ranking) and per-side Brier, so a win is not only "pro-RU channels are wrong more often".
- Label rule (fixed before tuning, not tuned): capture claim = true if settlement point is inside DeepState occupied polygon within W=21 days after the claim date; false if not inside at W. Censoring: claims younger than W at as_of are pending, never labelled false early [Guessing at W; pick it once with Vincent].
- Dedupe events: same (settlement, predicate) within 7 days = one event, else repeated daily claims inflate n.

## 6. Baselines to beat (same folds, same metric)
| Id | Baseline | P(e true) |
|---|---|---|
| B0 | Climatology | π_type |
| B1 | All-equal trust | 4.4 with a_s = global mean accuracy for all s, no copy discount |
| B2 | Side-only trust | 4.4 with a_s = m_g |
| B3 | Majority vote | (#yes posts + 1) / (#posts + 2), posts not units |
| B4 | Ablation: no copy discount | full model with c=0, cascades not collapsed |
| B5 | Unsupervised TD | `truthdiscovery` TruthFinder or Sums, scores min-max to [0,1] (optional) |
The pitch claim is: full model < B2 < B1 < B3 on log loss. If B2 ≈ full model, per-source trust adds nothing beyond side, and we say so.

## 7. Libraries (checked on PyPI today)
| Package | Version / last upload | Contents | Fit |
|---|---|---|---|
| `truthdiscovery` (github.com/joesingo/truthdiscovery) | 1.0.4, 2023-03-29 [Certain] | MajorityVoting, Sums, UnboundedSums, AverageLog, Investment, PooledInvestment, TruthFinder, CRH [Certain] | 31 **pinned** deps (numpy==1.24.2, scipy==1.10.1, Flask, matplotlib, pytest) [Certain] → will fight the uv env. No labels, no copies, no time. Use only as reference code for B5, in a throwaway venv. |
| `crowd-kit` (Toloka) | 1.4.2, 2025-10-13 [Certain] | DawidSkene (accepts `true_labels` = gold clamping), OneCoinDawidSkene, GLAD, MACE, KOS, MMSR, Wawa, ZeroBasedSkill, GoldMajorityVote [Certain] | Deps include `transformers`, `nltk`, `pyarrow`, `scikit-learn` [Certain] — heavy. No priors by side, no decay, no copy discount, no as-of time. Good as a sanity check of our one-coin result. |
| `spectrum` | 0.10.0 [Certain] | signal-processing spectral analysis — **unrelated** [Certain] | none |
| SLiMFast / Rekatsinas code | no maintained PyPI package found [Likely] | – | none |

**Decision: hand-roll** (~120 lines numpy + pandas). The model is closed-form Beta counts plus one logit sum; the parts we need (side priors, decay, as-of filter, copy discount) are exactly what the libraries lack [Guessing on line count, Likely on feasibility in 2 h].

## 8. Risks
- Label noise: DeepState lag makes true claims look false for days → W must exceed typical DeepState lag [Likely].
- Selection bias: we only label claims DeepState can resolve (captures). Strike and "repelled" claims may get no labels → their trust falls back to capture-claim trust. State this in the demo [Certain].
- Event matching errors (LLM gets settlement wrong) appear as "false claims" and hurt the source unfairly. Keep the evidence span so errors are auditable [Certain].
- Public output: trust scores are fine; do not publish fresh per-post positions (CLAUDE.md rule).
