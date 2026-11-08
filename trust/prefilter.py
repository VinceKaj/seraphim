"""Prefilter (no LLM): posts that name at least one front-zone settlement.

Reads records.db read-only. Matches capitalised 1–3 word sequences against the gazetteer (trust.db name_index), with
Russian/Ukrainian case endings undone (Покровську -> Покровськ, Костянтинівці -> Костянтинівка). Common-word names
(Победа, Мирное, ...) need a military keyword in the same post; oblast-capital names (BIG) never count.
Output: trust.db candidate(record_id, source, side, posted_at, lang, n_names, gids, names).
Usage: uv run trust/prefilter.py --since 2026-10-31 --until 2026-11-08 [--sources telegram:% bsky:%]
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import TRUST_DB, norm  # noqa: E402

RECORDS = Path(__file__).resolve().parents[1] / "record-store" / "records.db"
WORD = re.compile(r"[A-ZА-ЯЁІЇЄҐ][a-zа-яёіїєґ'’ʼ\-]+(?:\s[A-ZА-ЯЁІЇЄҐ][a-zа-яёіїєґ'’ʼ\-]+){0,2}")
# no adjective endings (-ському/-ском): "Покровському напрямку" is a direction, not the town
ENDINGS = [("ської", "ська"), ("цях", "ці"), ("ці", "ка"), ("ку", "ка"), ("ки", "ка"), ("кою", "ка"),
           ("ою", "а"), ("ій", "а"), ("ої", "а"), ("ого", "е"), ("ому", "е"), ("ім", "е"), ("ом", ""),
           ("ому", ""), ("у", ""), ("а", ""), ("і", "а"), ("е", "а"), ("е", ""), ("ю", "я"), ("ую", "ая"),
           ("ой", "ая"), ("ом", "ое"), ("ого", "ое"), ("ом", "ий"), ("ого", "ий"), ("ах", "и"), ("ах", "ы"),
           ("ске", "ск"), ("ска", "ск"), ("ську", "ськ"), ("ська", "ськ"), ("ського", "ське"), ("ске", "ское")]
COMMON = {"победа", "свобода", "мирное", "мирне", "новое", "нове", "труд", "восток", "запад", "север", "степь",
          "прогресс", "дружба", "заря", "зоря", "рассвет", "надежда", "надія", "перемога", "веселое", "веселе",
          "красное", "червоне", "весна", "майское", "травневе", "україна", "украина", "россия", "росія", "zoria",
          "pobeda", "victory", "peace", "mirne", "nova", "new", "south", "north", "east", "west", "kyiv", "rus",
          "мир", "ukraine", "russia", "ukraina", "україни", "украины", "never", "star", "marine", "mars", "stalin",
          "azov", "new york", "shakhtar", "medvedev", "chechnya", "общая", "новая", "черного", "черное", "московского",
          "орел", "орёл", "volyn", "волынь", "волинь", "lviv", "rivne", "poltava", "yalta", "mariupol", "sevastopol"}
BIG = {"kyiv", "dnipro", "donetsk", "luhansk", "kharkiv", "zaporizhzhia", "kherson",
          "sumy", "odesa", "mykolaiv", "днепр", "дніпро", "донецк", "донецьк", "луганск", "луганськ", "харьков",
          "харків", "запорожье", "запоріжжя", "херсон", "сумы", "суми", "крым", "крим"}
MILITARY = re.compile(r"(штурм|атак|наступ|освобо|звільн|просун|продвин|контрол|обстр|бо[йї]|захоп|оборон|"
                      r"окуп|позиці|позици|attack|assault|captur|advanc|liberat|front|troops|shelling)", re.I)


def variants(n):
    yield n
    for a, b in ENDINGS:
        if n.endswith(a) and len(n) - len(a) >= 3:
            yield n[: -len(a)] + b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--sources", nargs="*", default=["telegram:%", "bsky:%"])
    a = ap.parse_args()
    db = sqlite3.connect(TRUST_DB)
    names = {}
    for n, g in db.execute("SELECT n.norm, n.gid FROM name_index n JOIN settlement s USING(gid) "
                           "WHERE s.in_scope=1"):
        if len(n) >= 4:
            names.setdefault(n, set()).add(g)
    side = {}
    for author, s in db.execute("SELECT author, side FROM source_side") if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_side'").fetchone() else []:
        side[author] = s
    rec = sqlite3.connect(f"file:{RECORDS}?mode=ro", uri=True)
    where = " OR ".join("source LIKE ?" for _ in a.sources)
    q = (f"SELECT id, source, json_extract(data,'$.posted_at') p, json_extract(data,'$.lang'), "
         f"json_extract(data,'$.text') FROM records WHERE kind='post' AND ({where}) AND p >= ? AND p < ?")
    db.execute("DROP TABLE IF EXISTS candidate")
    db.executescript("CREATE TABLE IF NOT EXISTS candidate(record_id TEXT PRIMARY KEY, source TEXT, side TEXT, "
                     "posted_at TEXT, lang TEXT, n_names INTEGER, gids TEXT, names TEXT);")
    n_all = n_keep = 0
    rows = []
    for rid, src, pa, lang, text in rec.execute(q, (*a.sources, a.since, a.until)):
        n_all += 1
        text = text or ""
        if len(text) < 30:
            continue
        mil = bool(MILITARY.search(text))
        found = {}
        for m in WORD.finditer(text):
            toks = m.group(0).split()
            for k in range(len(toks), 0, -1):          # longest first: "Часів Яр" before "Часів"
                nm = norm(" ".join(toks[:k]))
                hit = next((names[v] for v in variants(nm) if v in names), None)
                if hit and nm not in BIG and (mil or nm not in COMMON):
                    found[" ".join(toks[:k])] = hit
                    break
        if found:
            gids = sorted({g for s in found.values() for g in s})
            rows.append((rid, src, side.get(src), pa, lang, len(found), json.dumps(gids), json.dumps(sorted(found))))
            n_keep += 1
    db.executemany("INSERT OR REPLACE INTO candidate VALUES (?,?,?,?,?,?,?,?)", rows)
    db.commit()
    print(f"{n_all} posts in window, {n_keep} candidates ({100 * n_keep / max(n_all, 1):.1f}%)")
    for r in db.execute("SELECT CASE WHEN source LIKE 'bsky:%' THEN 'bsky' ELSE source END s, count(*) FROM candidate "
                        "WHERE posted_at >= ? AND posted_at < ? GROUP BY 1 ORDER BY 2 DESC LIMIT 30",
                        (a.since, a.until)):
        print("  ", r)


if __name__ == "__main__":
    main()
