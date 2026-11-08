"""LLM claim extraction (LangGraph): extract -> validate (deterministic) -> retry with errors (max 2) -> write.

- Model: claude-haiku-5-5 via langchain-anthropic structured output (forced tool call; no thinking/effort/temperature).
- Validation: every evidence span must be an exact substring of the post. Near-misses (quotes, apostrophes, spaces)
  are repaired in code by copying the slice from the original text; only unrepairable spans go back to the LLM.
  Settlement names resolve in the gazetteer (trust.db name_index) with oblast filter, then closest-to-line.
- Resume: LangGraph SQLite checkpointer, thread_id = record id + prompt version. Re-runs skip finished posts.
- Output: trust.db claim_llm (one row per claim) + extract_run (one row per post: status, tokens). Nothing is written
  to the record store here (see publish step after review).
Usage:
  uv run trust/extract.py --pilot 150 --since 2026-11-01 --until 2026-11-09     # stratified sample of candidates
  uv run trust/extract.py --all --since 2026-10-08 --until 2026-11-09 --max-cost 3
"""
import argparse
import asyncio
import json
import random
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Literal, TypedDict

import numpy as np
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import DATA, TRUST_DB, norm  # noqa: E402
from prefilter import variants  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from langchain_anthropic import ChatAnthropic  # noqa: E402
from langchain_core.callbacks import get_usage_metadata_callback  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import RetryPolicy  # noqa: E402

RECORDS = Path(__file__).resolve().parents[1] / "record-store" / "records.db"
MODEL = "claude-haiku-5-5"
PROMPT_VERSION = "v5"
PRICE = {"in": 0.10, "cache_read": 0.01, "cache_write": 0.125, "out": 0.50}   # $/MTok, Haiku 5.5

SYSTEM = """You extract territorial-control claims about the current Russia-Ukraine war (2014-now) from one post.

A claim says that a named settlement (city, town, village) is, was, or is becoming controlled, entered, attacked or
held by Russian or Ukrainian forces. Ignore: strikes, shelling, drones, casualties, politics,
lists of places where one side "hit / struck / inflicted losses on" the other ("нанесли поражение живой силе и
технике ... в районах населенных пунктов X", "уражено", "hit positions near X") — those are strikes, not claims, historical events
(e.g. WWII anniversaries "освобождение Киева 1943"), and "directions" ("Покровський напрямок" / "Pokrovsk
direction" is NOT a claim about Pokrovsk). Return {"claims": []} if there is no claim. One claim per settlement per
state.

The user message gives the channel and its side. Words like "our / наши / наші / мы / ми" mean the channel's side;
"enemy / противник / ворог / окупанти" mean the other side.

Fields:
- settlement_as_written: the name exactly as it appears in the post (keep case endings).
- settlement_nominative: dictionary (nominative) form in the post's language: "в Покровську" -> "Покровськ",
  "Костянтинівки" -> "Костянтинівка", "освободили Александровку" -> "Александровка", "Купянске" -> "Купянск".
- settlement_uk: ALWAYS give the official Ukrainian name in Ukrainian (e.g. "Сладкое" -> "Солодке", "Владимировка" ->
  "Володимирівка", "Pokrovsk" -> "Покровськ"); null if the settlement is in Russia (Kursk, Belgorod, Bryansk).
- oblast: oblast named in the post for this settlement (e.g. "Донецкая", "Харківська", "Zaporizhzhia"), else null.
- landmark: a specific object inside the settlement if named (railway station, church, plant, district), else null.
- state — the side is part of the label; pick exactly one:
    ru_captured / ua_captured       full control claimed by that side ("освобожден", "звільнили", "captured",
                                    "recaptured", "взят под контроль").
    ru_entered / ua_entered         that side's forces are inside / fighting in / infiltrated, without full control
                                    ("ведут бои в", "зашли в", "закріпилися", "infiltrated", "street fighting").
    ru_advanced_near / ua_advanced_near   that side advanced near / toward / at the outskirts
                                    ("просунувся поблизу", "продвинулись у", "намагалися просунутися неподалік").
    ru_attack_repelled              Russian forces attacked at/toward the settlement and Ukrainian forces repelled it.
    ua_attack_repelled              Ukrainian forces attacked and Russian forces repelled it.
    ru_holds / ua_holds             that side says it keeps control / holds positions in it.
    ru_withdrew / ua_withdrew       that side's forces withdrew / retreated from it.
- modality: asserted (author states it as fact), reported (author relays another's claim: "Минобороны сообщает",
  "Russian sources claim"), denied (author says the claim is false), unconfirmed (rumour, "possibly").
- attributed_to: who makes the claim when modality is reported/denied (e.g. "Russian MoD"), else null.
- event_date: YYYY-MM-DD if the post states when it happened, else null.
- evidence: the SHORTEST exact substring of the post that supports the claim AND contains the settlement name as
  written. Copy characters exactly (quotes, apostrophes ʼ ' ’, dashes, spaces, case). No translation, no joining
  of separate fragments.

Examples
[channel side ru] "Подразделения группировки «Центр» освободили населенные пункты Новоэкономическое и Дорожное в ДНР."
-> ru_captured/asserted "Новоэкономическое" (oblast "ДНР") and "Дорожное";
   evidence "освободили населенные пункты Новоэкономическое и Дорожное".
[channel side ua] "На Покровському напрямку ворог 23 рази атакував позиції наших захисників у районах населених
пунктів Котлине, Удачне та Новосергіївка."
-> ru_attack_repelled/asserted for "Котлине", "Удачне", "Новосергіївка".
[channel side ru] "Наши подразделения удерживают позиции в микрорайоне Юбилейный в Купянске."
-> ru_holds/asserted "Купянске" (nominative "Купянск"), landmark "микрорайон Юбилейный".
[channel side ru] "В районе Красного Лимана подразделения отразили атаку противника."
-> ua_attack_repelled/asserted "Красного Лимана" (nominative "Красный Лиман").
[channel side ua] "🔄 Мапу оновлено. Ворог просунувся поблизу Світлого та Василівки."
-> ru_advanced_near/asserted "Світлого" (nominative "Світле") and "Василівки" (nominative "Василівка").
[channel side osint] "Russian milbloggers claim Russian forces captured Rodynske, but geolocated footage does not
confirm this." -> ru_captured/reported, attributed_to "Russian milbloggers", evidence "Russian forces captured Rodynske".
"""

STATES = ["ru_captured", "ua_captured", "ru_entered", "ua_entered", "ru_advanced_near", "ua_advanced_near",
          "ru_attack_repelled", "ua_attack_repelled", "ru_holds", "ua_holds", "ru_withdrew", "ua_withdrew"]


class Claim(BaseModel):
    settlement_as_written: str
    settlement_nominative: str
    settlement_uk: str | None = None
    oblast: str | None = None
    landmark: str | None = None
    state: Literal[tuple(STATES)]
    modality: Literal["asserted", "reported", "denied", "unconfirmed"]
    attributed_to: str | None = None
    event_date: str | None = None
    evidence: str = Field(description="Shortest exact substring of the post supporting the claim, containing the name")


class Claims(BaseModel):
    claims: list[Claim] = []


OBL = {"донец": "05", "днр": "05", "донеч": "05", "donetsk": "05", "луган": "14", "лнр": "14", "luhansk": "14",
       "харьк": "07", "харків": "07", "харьков": "07", "kharkiv": "07", "запор": "26", "zaporizh": "26",
       "днепр": "04", "дніпро": "04", "dnipro": "04", "сум": "21", "sumy": "21", "херсон": "08", "kherson": "08",
       "микола": "16", "никола": "16", "mykolaiv": "16"}


class Gazetteer:
    def __init__(self):
        db = sqlite3.connect(TRUST_DB)
        self.names = {}
        for n, g in db.execute("SELECT n.norm, n.gid FROM name_index n JOIN settlement s USING(gid) WHERE s.in_scope=1"):
            self.names.setdefault(n, set()).add(g)
        self.admin = dict(db.execute("SELECT gid, admin1 FROM settlement"))
        T = np.load(DATA / "truth.npz")
        self.d0, self.dist = T["dates"][0].astype(object), T["dist"]
        self.col = {int(g): j for j, g in enumerate(T["gids"])}

    def resolve(self, claim: dict, day: date):
        cands = set()
        o = (claim.get("oblast") or "").lower()
        if any(k in o for k in ("курск", "белгород", "брянск", "kursk", "belgorod", "bryansk")):
            return None, "in Russia (out of scope)"
        for raw in (claim["settlement_nominative"], claim.get("settlement_uk") or "", claim["settlement_as_written"]):
            if not raw:
                continue
            for v in variants(norm(raw)):
                if v in self.names:
                    cands = self.names[v]
                    break
            if cands:
                break
        if not cands:
            return None, "not found"
        o = (claim.get("oblast") or "").lower()
        a1 = next((v for k, v in OBL.items() if k in o), None)
        if a1:
            c2 = {g for g in cands if self.admin.get(g) == a1}
            if not c2:
                return None, f"no '{claim['settlement_nominative']}' in oblast {claim['oblast']}"
            cands = c2
        i = min(max((day - self.d0).days, 0), self.dist.shape[0] - 1)
        g = min(cands, key=lambda g: abs(float(self.dist[i, self.col[g]])))
        return int(g), None


GAZ: Gazetteer | None = None
SIDES = dict(sqlite3.connect(TRUST_DB).execute("SELECT author, side FROM source_side").fetchall())
_QUOTES = str.maketrans({"’": "'", "ʼ": "'", "‘": "'", "`": "'", "«": '"', "»": '"', "„": '"', "“": '"', "”": '"',
                         "–": "-", "—": "-", " ": " "})


def repair_span(span: str, text: str) -> str | None:
    """Return an exact substring of text matching span, or None. Never invents text."""
    if span and span in text:
        return span
    canon = lambda s: unicodedata.normalize("NFC", s).translate(_QUOTES)  # noqa: E731  (1:1 char map)
    ct, cs = canon(text), re.sub(r"\s+", " ", canon(span)).strip()
    if len(ct) == len(text):
        i = ct.find(cs)
        if i >= 0:
            return text[i:i + len(cs)]
    if len(cs) >= 12:
        al = fuzz.partial_ratio_alignment(cs, text, score_cutoff=92)
        if al:
            cand = text[al.dest_start:al.dest_end]
            if cand.strip():
                return cand
    return None


class S(TypedDict, total=False):
    record_id: str
    source: str
    posted_at: str
    text: str
    claims: list[dict]
    errors: list[str]
    attempts: int
    status: str


def llm():
    return ChatAnthropic(model=MODEL, max_tokens=12000, max_retries=6).with_structured_output(Claims, include_raw=True)


_LLM = None


async def extract(s: S):
    global _LLM
    _LLM = _LLM or llm()
    msgs = [{"role": "system", "content": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": f"Channel: {s['source']} (side: {SIDES.get(s['source'], 'unknown')})\nPosted: {s['posted_at'][:16]}\n<post>\n{s['text']}\n</post>"}]
    if s.get("errors"):
        msgs.append({"role": "assistant", "content": json.dumps({"claims": s["claims"]}, ensure_ascii=False)
                     if s["claims"] else "(invalid output)"})
        msgs.append({"role": "user", "content": "Fix these problems and return the full corrected list:\n- "
                     + "\n- ".join(s["errors"])})
    try:
        res = await _LLM.ainvoke(msgs)
        stop = (res["raw"].response_metadata or {}).get("stop_reason")
        if stop == "max_tokens":
            return {"claims": [], "errors": [], "attempts": s.get("attempts", 0) + 1, "status": "llm_error: truncated"}
        if res.get("parsing_error") or res.get("parsed") is None:
            n = s.get("attempts", 0) + 1
            if n < 3:   # retryable: validate() sends the fix-up instruction
                return {"claims": [], "attempts": n, "status": "parse_retry"}
            return {"claims": [], "errors": [], "attempts": n, "status": f"llm_error: parse {res.get('parsing_error')}"[:300]}
        claims = [c.model_dump() for c in res["parsed"].claims]
    except Exception as e:  # refusal / schema failure
        return {"claims": [], "errors": [], "attempts": s.get("attempts", 0) + 1, "status": f"llm_error: {e}"[:300]}
    return {"claims": claims, "attempts": s.get("attempts", 0) + 1}


def validate(s: S):
    if s.get("status", "").startswith("llm_error"):
        return {"errors": []}
    if s.get("status") == "parse_retry":
        return {"errors": ["Your output did not parse. Return `claims` as a JSON array of objects (not a string)."],
                "status": ""}
    errs, day = [], date.fromisoformat(s["posted_at"][:10])
    for i, c in enumerate(s["claims"]):
        fixed = repair_span(c["evidence"], s["text"])
        if fixed is None:
            errs.append(f"claim {i} ({c['settlement_as_written']}): evidence is not an exact substring of the post: "
                        f"{c['evidence']!r}")
        else:
            c["evidence"] = fixed
            if norm(c["settlement_as_written"]) not in norm(fixed) and s.get("attempts", 0) < 2:
                errs.append(f"claim {i}: evidence must contain the settlement name as written "
                            f"({c['settlement_as_written']!r}); give a span that names it.")
        g, why = GAZ.resolve(c, day)
        c["gid"] = g
        if g is None and why != "in Russia (out of scope)" and s.get("attempts", 0) < 2:
            errs.append(f"claim {i}: settlement {c['settlement_nominative']!r} {why} in our Ukrainian gazetteer. "
                        "Give its official nominative name in the post's language, or drop the claim if it is not a "
                        "settlement in Ukraine.")
    return {"errors": errs}


def route(s: S):
    return "extract" if s.get("errors") and s.get("attempts", 0) < 3 else "done"


def done(s: S):
    if s.get("status", "").startswith("llm_error"):
        return {}
    bad = [c for c in s["claims"] if c["evidence"] not in s["text"]]
    return {"status": "ok" if not bad else "span_fail", "claims": [c for c in s["claims"] if c["evidence"] in s["text"]]}


def build():
    g = StateGraph(S)
    g.add_node("extract", extract, retry_policy=RetryPolicy(max_attempts=3))
    g.add_node("validate", validate)
    g.add_node("done", done)
    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", route, ["extract", "done"])
    g.add_edge("done", END)
    return g


GROUPS = {"ru": ["mod_russia", "grvZapad", "wargonzo", "rybar", "dva_majors", "tass_agency", "ASGasparyan",
                 "Alekhin_Telega", "Army_Russia", "A_S_Sukonkin", "AG_DPR", "DnevnikDesantnika", "divgen",
                 "creamy_caprice", "motopatriot78", "voin_dv", "severnnyi", "btr80", "voenkorKotenok", "sashakots"],
          "ua": ["GeneralStaffZSU", "AFUStratCom", "ssternenko", "ButusovPlus", "Koord_shtab", "dnipropetrovskaODA",
                 "VadymFilashkin", "exilenova_plus"],
          "map": ["DeepStateUA", "AMK_Mapping"]}


def pick(a):
    db = sqlite3.connect(TRUST_DB)
    if a.ids:
        ids = [l.strip() for l in open(a.ids) if l.strip()]
        rec = sqlite3.connect(f"file:{RECORDS}?mode=ro", uri=True)
        return [(i, *rec.execute("SELECT source, json_extract(data,'$.posted_at') FROM records WHERE id=?", (i,)).fetchone())
                for i in ids]
    rows = db.execute("SELECT record_id, source, posted_at FROM candidate WHERE posted_at >= ? AND posted_at < ?",
                      (a.since, a.until)).fetchall()
    if a.all:
        return rows
    rnd = random.Random(7)
    grp = lambda s: next((k for k, v in GROUPS.items() if s.split(":", 1)[1] in v), "bsky" if s.startswith("bsky") else "other")  # noqa: E731
    by = {}
    for r in rows:
        by.setdefault(grp(r[1]), []).append(r)
    quota = {"ru": 0.33, "ua": 0.33, "map": 0.14, "bsky": 0.2, "other": 0.0}
    out = []
    for k, q in quota.items():
        pool = by.get(k, [])
        out += rnd.sample(pool, min(len(pool), round(a.pilot * q)))
    return out


async def main_async(a):
    global GAZ
    GAZ = Gazetteer()
    picked = pick(a)
    rec = sqlite3.connect(f"file:{RECORDS}?mode=ro", uri=True)
    texts = {}
    for rid, _, _ in picked:
        r = rec.execute("SELECT json_extract(data,'$.text') FROM records WHERE id=?", (rid,)).fetchone()
        texts[rid] = r[0] if r else ""
    db = sqlite3.connect(TRUST_DB, timeout=120)  # other readers may hold the lock (rollback journal)
    db.executescript("""CREATE TABLE IF NOT EXISTS claim_llm(record_id TEXT, n INTEGER, version TEXT, source TEXT,
        posted_at TEXT, gid INTEGER, name TEXT, nominative TEXT, oblast TEXT, landmark TEXT, state TEXT, actor TEXT,
        modality TEXT, attributed_to TEXT, event_date TEXT, span TEXT, PRIMARY KEY(record_id, n, version));
      CREATE TABLE IF NOT EXISTS extract_run(record_id TEXT, version TEXT, source TEXT, posted_at TEXT, status TEXT,
        attempts INTEGER, n_claims INTEGER, sample TEXT, PRIMARY KEY(record_id, version));""")
    done_ids = {r[0] for r in db.execute("SELECT record_id FROM extract_run WHERE version=?", (PROMPT_VERSION,))}
    todo = [(rid, src, pa) for rid, src, pa in picked if rid not in done_ids and texts.get(rid)]
    est_in = sum(len(texts[r]) for r, _, _ in todo) / 2.5     # ~2.5 chars/token for Cyrillic
    est = (est_in * PRICE["in"] + len(todo) * 2000 * PRICE["cache_read"] + len(todo) * 250 * PRICE["out"]) / 1e6 * 1.3
    print(f"{len(picked)} picked, {len(todo)} to run; estimated cost ${est:.2f} (cap ${a.max_cost})", flush=True)
    if est > a.max_cost:
        sys.exit("estimated cost over cap; stopping")
    if a.dry:
        return
    sample = "ids" if a.ids else ("pilot" if not a.all else "all")
    async with AsyncSqliteSaver.from_conn_string(str(DATA / "extract_ckpt.db")) as saver:
        app = build().compile(checkpointer=saver)
        t0 = time.time()
        with get_usage_metadata_callback() as cb:
            starts = [0, 1] + list(range(65, len(todo), 64))     # first post alone: writes the prompt cache
            for k, k2 in zip(starts, starts[1:] + [len(todo)]):
                chunk = todo[k:k2]
                if not chunk:
                    continue
                inputs = [{"record_id": r, "source": s, "posted_at": p, "text": texts[r], "attempts": 0} for r, s, p in chunk]
                cfgs = [{"configurable": {"thread_id": f"{r}:{PROMPT_VERSION}"}, "max_concurrency": a.concurrency}
                        for r, _, _ in chunk]
                res = await app.abatch(inputs, cfgs, return_exceptions=True)
                for (rid, src, pa), out in zip(chunk, res):
                    if isinstance(out, Exception):
                        db.execute("INSERT OR REPLACE INTO extract_run VALUES (?,?,?,?,?,?,?,?)",
                                   (rid, PROMPT_VERSION, src, pa, f"exception: {out}"[:300], 0, 0, sample))
                        continue
                    cl = out.get("claims", [])
                    db.execute("INSERT OR REPLACE INTO extract_run VALUES (?,?,?,?,?,?,?,?)",
                               (rid, PROMPT_VERSION, src, pa, out.get("status"), out.get("attempts"), len(cl), sample))
                    db.executemany("INSERT OR REPLACE INTO claim_llm VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                   [(rid, n, PROMPT_VERSION, src, pa, c.get("gid"), c["settlement_as_written"],
                                     c["settlement_nominative"], c.get("oblast"), c.get("landmark"), c["state"],
                                     c["state"][:2], c["modality"], c.get("attributed_to"), c.get("event_date"),
                                     c["evidence"]) for n, c in enumerate(cl)])
                db.commit()
                u = cb.usage_metadata.get(MODEL, {}) if cb.usage_metadata else {}
                det = dict(u.get("input_token_details", {}) or {})
                det["cache_creation"] = (det.get("cache_creation", 0) or 0) + det.get("ephemeral_5m_input_tokens", 0) \
                    + det.get("ephemeral_1h_input_tokens", 0)
                cost = ((u.get("input_tokens", 0) - det.get("cache_read", 0) - det.get("cache_creation", 0)) * PRICE["in"]
                        + det.get("cache_read", 0) * PRICE["cache_read"] + det.get("cache_creation", 0) * PRICE["cache_write"]
                        + u.get("output_tokens", 0) * PRICE["out"]) / 1e6
                print(f"  {k + len(chunk)}/{len(todo)} posts, {time.time() - t0:.0f}s, tokens {u}, cost ${cost:.3f}",
                      flush=True)
                if cost > a.max_cost:
                    sys.exit("cost cap reached")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--max-cost", type=float, default=1.0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ids", help="file with record ids to extract (overrides --pilot/--all sampling)")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
