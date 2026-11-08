"""Side label per author: Huge's all_channels.csv ("Political view") + manual labels for channels it lacks.
Output: trust.db source_side(author, side, basis). side in ru | ua | media | osint | unknown.
Usage: uv run trust/source_sides.py
"""
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import TRUST_DB  # noqa: E402

CSV = Path("/home/vince/Huge/potato patato/all_channels.csv")
VIEW = {"Pro-Kremlin": "ru", "Pro-Ukraine": "ua", "Traditional Mass Media": "media"}
MANUAL = {  # basis: channel ownership / ISW usage. exilenova_plus left unknown on purpose.
    "ua": "GeneralStaffZSU DeepStateUA AFUStratCom VadymFilashkin dnipropetrovskaODA Koord_shtab ssternenko "
          "ButusovPlus kpszsu V_Zelenskiy_official",
    "ru": "mod_russia grvZapad tass_agency wargonzo rybar dva_majors DnevnikDesantnika divgen creamy_caprice "
          "muchnoyjugend88 motopatriot78 voin_dv severnnyi btr80 voenkorKotenok sashakots zvizdecmanhustu",
    "osint": "AMK_Mapping",
}

db = sqlite3.connect(TRUST_DB)
db.executescript("DROP TABLE IF EXISTS source_side; CREATE TABLE source_side(author TEXT PRIMARY KEY, side TEXT, basis TEXT);")
rows = {}
for r in csv.DictReader(open(CSV, encoding="utf-8")):
    rows[f"telegram:{r['Channel_name'].strip().lstrip('@')}"] = (VIEW.get(r["Political view"], "unknown"),
                                                                  f"all_channels.csv: {r['Political view']}")
for side, names in MANUAL.items():
    for n in names.split():
        rows.setdefault(f"telegram:{n}", (side, "manual"))
db.executemany("INSERT INTO source_side VALUES (?,?,?)", [(k, *v) for k, v in rows.items()])
db.commit()
print(dict(db.execute("SELECT side, count(*) FROM source_side GROUP BY 1").fetchall()))
