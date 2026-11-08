import json
import math
import re
from collections import defaultdict
from pathlib import Path

import store_client as sc
from schemas import Article, Post

GAMMA = 0.3
INIT_TRUST = 0.8
ITERATIONS = 30
PRIOR = 0.5
PRIOR_WEIGHT = 5
MIN_TRUST, MAX_TRUST = 0.01, 0.99
TIER_TO_TRUST = {"trusted": 0.9, "untrusted": 0.3}

EVENTS = {
    "pokrovsk_fighting": {"patterns": [r"\bPokrovsk\b"]},
    "zaporizhzhia_npp":  {"patterns": [r"Zaporizhzhia nuclear", r"ZNPP"]},
    "ceasefire_talks":   {"patterns": [r"ceasefire", r"peace talks"]},
}


def load_records():
    """Yield validated Post and Article objects from the store."""
    for kind, model in (("post", Post), ("article", Article)):
        since = 0
        while True:
            batch = sc.tail(since=since, kind=kind, limit=1000)
            if not batch:
                break
            for rec in batch:
                try:
                    yield model.model_validate(rec["data"])
                except Exception as e:
                    print(f"skipped {kind} seq={rec.get('seq')}: {e}")
            since = batch[-1]["seq"]


def build_observations(records) -> set[tuple[str, str]]:
    """Return {(source, claim)} pairs. A claim is an event topic on a given day."""
    obs = set()
    for r in records:
        text = r.text or ""
        day = r.posted_at.strftime("%Y-%m-%d")
        for key, spec in EVENTS.items():
            if any(re.search(p, text, re.IGNORECASE) for p in spec["patterns"]):
                obs.add((r.author, f"{key}|{day}"))
    return obs


def clamp(x: float) -> float:
    return max(MIN_TRUST, min(MAX_TRUST, x))


def tau(t: float) -> float:
    return -math.log(1 - clamp(t))


def truthfinder(obs: set, anchors: dict[str, float]) -> tuple[dict, dict]:
    sources_of = defaultdict(set)
    claims_of = defaultdict(set)
    for s, c in obs:
        sources_of[c].add(s)
        claims_of[s].add(c)

    trust = {s: anchors.get(s, INIT_TRUST) for s in claims_of}
    conf: dict[str, float] = {}

    for _ in range(ITERATIONS):
        for c, ss in sources_of.items():
            sigma = sum(tau(trust[s]) for s in ss)
            conf[c] = 1 / (1 + math.exp(-GAMMA * sigma))

        for s, cs in claims_of.items():
            if s in anchors:
                continue
            trust[s] = clamp(sum(conf[c] for c in cs) / len(cs))

    return trust, conf


def rank_sources(trust: dict, claims_count: dict) -> list[tuple[str, float, int]]:
    ranked = []
    for s, t in trust.items():
        n = claims_count[s]
        adjusted = (t * n + PRIOR * PRIOR_WEIGHT) / (n + PRIOR_WEIGHT)
        ranked.append((s, round(adjusted, 3), n))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def load_anchors(path: Path = Path("sources.json")) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        src: TIER_TO_TRUST[e["tier"]]
        for src, e in data["sources"].items()
        if e.get("reviewed") and e["tier"] in TIER_TO_TRUST
    }


def run() -> None:
    records = list(load_records())
    obs = build_observations(records)
    anchors = load_anchors()

    trust, conf = truthfinder(obs, anchors)

    claims_count = defaultdict(int)
    for s, _ in obs:
        claims_count[s] += 1

    print(f"{len(records)} records, {len(obs)} observations, "
          f"{len(trust)} sources, {len(conf)} claims")
    print(f"{'source':<40} {'trust':>6} {'claims':>7}  anchored")
    for s, t, n in rank_sources(trust, claims_count):
        mark = "yes" if s in anchors else ""
        print(f"{s:<40} {t:>6} {n:>7}  {mark}")


if __name__ == "__main__":
    run()