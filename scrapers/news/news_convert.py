import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from schemas import Article, Media

# Languages in your CSV mapped to ISO 639-1 codes. Add more as they appear.
LANG_CODES = {
    "English": "en",
    "Ukrainian": "uk",
    "Russian": "ru",
    "Belarusian": "be",
    "German": "de",
    "French": "fr",
    "Polish": "pl",
}

DATE_FORMAT = "%d-%m-%Y %H:%M:%S"  # e.g. "01-03-2022 13:53:11", day first


def _none_if_blank(value: str) -> str | None:
    value = (value or "").strip()
    return value or None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def row_to_article(row: dict, scraped_at: datetime) -> Article:
    publisher = row["PUBLISHER"].strip()
    byline = _none_if_blank(row["AUTHOR"])
    language = (row["LANGUAGE"] or "").strip()

    media = []
    if _none_if_blank(row["IMAGE URL"]):
        media.append(Media(type="photo", ref=row["IMAGE URL"].strip()))
    if _none_if_blank(row["VIDEO URL"]):
        media.append(Media(type="video", ref=row["VIDEO URL"].strip()))

    return Article(
        url=_none_if_blank(row["ARTICLE LINK"]),
        author=f"web:{_slug(publisher)}",
        author_name=byline or publisher,
        posted_at=datetime.strptime(row["PUBLISHED DATE (UTC)"].strip(), DATE_FORMAT).replace(tzinfo=timezone.utc),
        scraped_at=scraped_at,
        lang=LANG_CODES.get(language),
        text=(row["DESCRIPTION"] or "").strip(),
        media=media,
        raw=dict(row),  # untouched CSV row, including category and country
        title=row["TITLE"].strip(),
        publisher=publisher,
        byline=byline,
    )


def convert(csv_path: Path, out_path: Path) -> None:
    scraped_at = datetime.now(timezone.utc)
    written = skipped = 0

    # utf-8-sig strips a BOM if the export added one
    with csv_path.open(newline="", encoding="utf-8-sig") as src, \
         out_path.open("w", encoding="utf-8") as dst:
        reader = csv.DictReader(src)
        for line_no, row in enumerate(reader, start=2):  # line 1 is the header
            try:
                article = row_to_article(row, scraped_at)
                dst.write(article.model_dump_json() + "\n")
                written += 1
            except Exception as e:
                skipped += 1
                print(f"line {line_no}: skipped ({e})")

    print(f"Wrote {written} articles to {out_path}, skipped {skipped}.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python csv_to_articles.py input.csv output.jsonl")
    convert(Path(sys.argv[1]), Path(sys.argv[2]))