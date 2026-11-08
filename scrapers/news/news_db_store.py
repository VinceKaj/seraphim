import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "record-store"))
import store_client as sc  # noqa: E402
from schemas import Article  # noqa: E402


def article_key(article: Article) -> str:
    # Stable key: the URL if present, otherwise a hash of title and date
    if article.url:
        return hashlib.sha256(article.url.encode("utf-8")).hexdigest()[:32]
    basis = f"{article.title}|{article.posted_at.isoformat()}|{article.publisher}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def load_articles(path: Path):
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            try:
                yield line_no, Article.model_validate_json(line)
            except Exception as e:
                print(f"line {line_no}: invalid record ({e})")


def store(path: Path) -> None:
    written = failed = 0
    for line_no, article in load_articles(path):
        try:
            sc.create("article", article.author, article_key(article), article)
            written += 1
        except Exception as e:
            failed += 1
            print(f"line {line_no}: store write failed ({e})")
    print(f"Stored {written} articles, {failed} failed.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python store_articles.py news_articles_full.out.jsonl")
    store(Path(sys.argv[1]))