"""Shared geo helpers: projection, name normalisation, paths."""
import math
import re
import unicodedata
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
TRUST_DB = ROOT / "trust.db"
FRONTLINE = Path("/home/vince/Kacper/public/data/frontline")   # read-only
DS_RAW = Path("/home/vince/Kacper/.cache/deepstate-raw")         # read-only

# Local equirectangular projection (km) centred on the front. Error < 5% over the front zone.
LAT0, LON0 = 48.5, 35.0
KX, KY = math.cos(math.radians(LAT0)) * 111.32, 110.57


def proj(lon, lat):
    return (np.asarray(lon) - LON0) * KX, (np.asarray(lat) - LAT0) * KY


def proj_xy(coords: np.ndarray) -> np.ndarray:
    out = np.empty_like(coords, dtype=float)
    out[:, 0] = (coords[:, 0] - LON0) * KX
    out[:, 1] = (coords[:, 1] - LAT0) * KY
    return out


_APOS = re.compile(r"[’ʼ'`‘´\"«»„“”]")
_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    """Lower-case, ё→е, drop apostrophes/quotes, unify dashes and spaces."""
    s = unicodedata.normalize("NFC", s).lower().replace("ё", "е")
    s = _APOS.sub("", s)
    s = re.sub(r"[‐-―\-]+", "-", s)
    s = re.sub(r"\s*-\s*", "-", s)
    return _WS.sub(" ", s).strip()
