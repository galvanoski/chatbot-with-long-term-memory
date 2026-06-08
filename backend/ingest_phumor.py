"""
Fetch memes from programmerhumor.io/memes/cats and load into meme_repo collection.

Usage:
    python -m backend.ingest_phumor
"""

import logging
import os
import sys
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup
from backend.rag.vectorstore import ChromaStore

# Ensure project root is on sys.path (so `python -m backend.ingest_phumor` works)
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Load .env from project root
dotenv = _project_root / ".env"
if dotenv.exists():
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("geekcat.ingest_phumor")

URL = "https://programmerhumor.io/memes/cats"
COLLECTION = "meme_repo"

def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")

def parse_memes(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for post in soup.select("div.post"):
        classes = post.get("class", [])
        if "category-affiliate" in classes or "affiliate" in classes:
            continue

        title_el = post.select_one("h2.post-title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)

        excerpt_el = post.select_one("div.post-excerpt-short")
        description = excerpt_el.get_text(strip=True) if excerpt_el else ""

        categories = [a.get_text(strip=True) for a in post.select("a.category")]

        text = f"{title}\n\n{description}" if description else title
        tags = [cat.lower() for cat in categories if cat.lower() != "programming"]

        items.append({
            "text": text,
            "metadata": {
                "source": "programmerhumor.io",
                "format": "meme",
                "theme": "cats",
                "tags": tags or ["cats"],
            },
        })

    return items

def load(force: bool = False):
    store = ChromaStore()

    if force:
        try:
            store._client.delete_collection(COLLECTION)
            logger.info("Deleted collection '%s'", COLLECTION)
        except Exception:
            pass

    logger.info("Fetching %s ...", URL)
    html = fetch_html(URL)
    memes = parse_memes(html)
    logger.info("Found %d memes", len(memes))

    if not memes:
        logger.warning("No memes found — aborting")
        return

    coll = store.get_global_collection(COLLECTION)

    existing = coll.get()
    existing_texts = set(existing.get("documents", []) or [])

    new_items = [m for m in memes if m["text"] not in existing_texts]
    if not new_items:
        logger.info("All memes already in collection — nothing to do")
        return

    count = store.add_global_items(COLLECTION, new_items)
    logger.info("Loaded %d new memes into %s", count, COLLECTION)

if __name__ == "__main__":
    load()
