"""Demo 0 extractor (no LLM): formulaic Russian MoD capture claims -> settlement claims.

Matches "освобожден(ы) населенный пункт X", "освободили X и Y", "установлен контроль над населенным пунктом X",
"взят под контроль населенный пункт X". Names are resolved in the gazetteer (trust.db name_index);
ties are broken by the oblast named in the sentence, then by the candidate closest to the line on the claim date.

Every claim keeps an exact evidence span (the matched sentence slice of the original text).
Output: trust.db table claim_regex. Nothing is written to the record store (claims there are immutable;
write them only after the checkpoint review).
Usage: uv run trust/regex_claims.py [channel ...]   (default: RU-side channels in Huge's dumps)
"""
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "record-store"))
import store_client as sc  # noqa: E402
from geo import DATA, TRUST_DB, norm  # noqa: E402

HUGE = Path("/home/vince/Huge/potato patato/telegram_data")
DEFAULT = ["mod_russia", "Army_Russia", "ASGasparyan", "A_S_Sukonkin", "AG_DPR", "Alekhin_Telega", "Aksenov82",
           "AntonKrasovskii"]

NAME = r"[А-ЯЁ][А-ЯЁа-яё]+(?:[-‑][А-ЯЁа-яё]+)*(?:\s(?:[А-ЯЁ][А-ЯЁа-яё]+|Яр|Рог|Балка|Поляна|Лиман|Гай|Луг|Долина|Брод))*(?:\s\([А-ЯЁ][а-яё]+\))?"
NAMES = rf"(?P<names>{NAME}(?:\s*(?:,|и)\s*{NAME})*)"
KIND = r"(?:(?:населенн?ы[йе]|населённы[йе]|населенным|населёнными|населенными|населённым)\s+пункт(?:ы|ом|ами)?|н\.\s?п\.|город(?:а|ом)?|сел(?:о|а|ом)|пос[её]л(?:ок|ки|ком)|поселок)\s+"
VERB = (r"(?:освобожд[её]н[аоы]?|освободили|установлен\s+контроль\s+над|взят[аоы]?\s+под\s+контроль|"
        r"перешл[аои]\s+под\s+контроль)")
PAT = re.compile(rf"(?P<verb>{VERB})\s+(?:(?:в\s+)?(?:ходе\s+)?)?(?:{KIND})?{NAMES}")
PARTIAL = re.compile(r"(част[ьи]|половин|основная часть|большая часть|южн|северн|западн|восточн)\w*\s*$", re.I)
OBLAST = {"донецк": "05", "днр": "05", "луганск": "14", "лнр": "14", "харьков": "07", "запорож": "26",
          "днепропетров": "04", "сумск": "21", "херсон": "08", "николаев": "16", "курск": "41", "белгород": "09",
          "брянск": "10"}
STOP = {"Вооруженных", "Вооружённых", "России", "Российской", "Федерации", "ВС", "РФ", "Минобороны", "ВСУ",
        "Украины", "Народной", "Республики", "ДНР", "ЛНР", "Харьковской", "Донецкой", "Запорожской", "Сумской",
        "Днепропетровской", "Луганской", "Херсонской", "Курской", "Белгородской"}


ADJ = re.compile(r"^[А-ЯЁ][а-яё]+(?:ской|цкой|ской|ского|ских)$")


def variants(nm):
    """Nominative first, then undo common accusative/locative endings (Авдеевку -> Авдеевка)."""
    yield nm
    for a, b in (("ую", "ая"), ("ку", "ка"), ("у", "а"), ("ю", "я"), ("ке", "ка"), ("е", "а"), ("ом", "")):
        if nm.endswith(a):
            yield nm[: -len(a)] + b


def sentence_bounds(text, a, b):
    s = max(text.rfind(".", 0, a), text.rfind("\n", 0, a)) + 1
    e = min([i for i in (text.find(".", b), text.find("\n", b)) if i != -1] or [len(text)])
    return s, e


def main():
    chans = sys.argv[1:] or DEFAULT
    db = sqlite3.connect(TRUST_DB)
    idx = {}
    for n, g in db.execute("SELECT norm, gid FROM name_index"):
        idx.setdefault(n, set()).add(g)
    meta = {g: (cc, a1, sc_) for g, cc, a1, sc_ in db.execute("SELECT gid, cc, admin1, in_scope FROM settlement")}
    T = np.load(DATA / "truth.npz")
    tdates, tgid, dist = T["dates"], T["gids"], T["dist"]
    col = {int(g): j for j, g in enumerate(tgid)}
    d0 = tdates[0].astype(object)

    def absdist(g, d):
        i = min(max((d - d0).days, 0), len(tdates) - 1)
        return abs(float(dist[i, col[g]])) if g in col else 1e9

    db.executescript("""DROP TABLE IF EXISTS claim_regex;
    CREATE TABLE claim_regex(record_id TEXT, n INTEGER, channel TEXT, msg_id INTEGER, posted_at TEXT, gid INTEGER,
      name TEXT, oblast TEXT, n_cand INTEGER, in_scope INTEGER, partial INTEGER, verb TEXT, span TEXT, fwd TEXT,
      PRIMARY KEY(record_id, n));""")
    stats = {}
    for ch in chans:
        f = HUGE / ch / "messages.jsonl"
        if not f.exists():
            print("missing", ch)
            continue
        st = stats.setdefault(ch, dict(msgs=0, matches=0, resolved=0, unresolved=set()))
        rows = []
        for line in open(f, encoding="utf-8"):
            m = json.loads(line)
            text = m.get("text") or ""
            st["msgs"] += 1
            rec = sc.rid(sc.key("post", f"telegram:{ch}", str(m["id"])))
            pdate = date.fromisoformat(m["date"][:10])
            n = 0
            for mt in PAT.finditer(text):
                st["matches"] += 1
                s, e = sentence_bounds(text, mt.start(), mt.end())
                sent = text[s:e]
                partial = int(bool(PARTIAL.search(text[max(0, mt.start() - 40):mt.start()])) or
                              bool(re.search(r"(част[ьи]|половин)", mt.group(0))))
                sent_obl = {v for k, v in OBLAST.items() if k in sent.lower()}
                base = mt.start("names")
                for nmt in re.finditer(NAME, mt.group("names")):
                    toks = nmt.group(0).split()
                    keep = []
                    for t in toks:
                        if t in STOP or ADJ.match(t):
                            break
                        keep.append(t)
                    raw = " ".join(keep)
                    if not raw:
                        continue
                    after = text[base + nmt.start(): base + nmt.end() + 60].lower()
                    obl = next((v for k, v in OBLAST.items() if k in after), None)
                    if obl is None and len(sent_obl) == 1:
                        obl = next(iter(sent_obl))
                    cands = set()
                    for nm in variants(norm(re.sub(r"\s*\(.*\)", "", raw))):
                        cands = idx.get(nm, set())
                        if cands:
                            break
                    if not cands:
                        st["unresolved"].add(raw)
                        continue
                    if obl:
                        cands = {g for g in cands if meta[g][1] == obl}
                        if not cands:
                            st["unresolved"].add(f"{raw} [{obl}]")
                            continue
                    g = min(cands, key=lambda g: absdist(g, pdate))
                    st["resolved"] += 1
                    rows.append((rec, n, ch, m["id"], m["date"], g, raw, obl, len(cands), meta[g][2], partial,
                                 mt.group("verb").lower(), sent, json.dumps(m.get("forwarded_from"))))
                    assert sent in text
                    n += 1
        db.executemany("INSERT OR REPLACE INTO claim_regex VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        un = sorted(st["unresolved"])
        print(f"{ch}: msgs={st['msgs']} matches={st['matches']} resolved_names={st['resolved']} "
              f"unresolved={len(un)} e.g. {un[:12]}", flush=True)


if __name__ == "__main__":
    main()
