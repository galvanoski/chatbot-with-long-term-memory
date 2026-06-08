"""
Reset ChromaDB collections (pod_catalog, meme_repo, brand_guidelines)
and re-seed from loader.py + programmerhumor.io — without touching filesystem.
"""

import logging
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

dotenv = _project_root / ".env"
if dotenv.exists():
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from backend.rag.vectorstore import ChromaStore
from backend.rag.loader import seed_pod_catalog, seed_meme_repo, seed_brand_guidelines
from backend.ingest_phumor import load as load_phumor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("geekcat.reset")

COLLECTIONS = ["pod_catalog", "meme_repo", "brand_guidelines"]

def run():
    store = ChromaStore()
    client = store._client

    for name in COLLECTIONS:
        try:
            client.delete_collection(name)
            logger.info("Deleted collection '%s'", name)
        except Exception as e:
            logger.warning("Could not delete '%s': %s", name, e)

    total = 0
    total += seed_pod_catalog(store)
    total += seed_meme_repo(store)
    total += seed_brand_guidelines(store)
    logger.info("Base seed: %d items loaded.", total)

    load_phumor()
    logger.info("Done.")

if __name__ == "__main__":
    run()
