"""
RAG Data Loader — The Geek Cat

Ingests seed documents into Chroma collections so the agent can retrieve
relevant context during the research and copywriter nodes.

Collections managed here:
  - pod_catalog   → POD products (real products from thegeekcat.de/shop)
  - meme_repo     → IT / blockchain / AI memes and running jokes
  - brand_guidelines → global brand voice, tone, and rules

Run once to seed:
    python -m backend.rag.loader
"""

import logging
import os
import sys
from pathlib import Path

# Ensure backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from backend.rag.vectorstore import ChromaStore
from backend.rag.embeddings import get_embeddings

logger = logging.getLogger("geekcat.loader")


# ═══════════════════════════════════════════════════════════
#  1.  POD CATALOG  —  Products available on thegeekcat.de
# ═══════════════════════════════════════════════════════════

POD_PRODUCTS = [
    # ── Mugs / Nerd Gear ──
    {
        "text": (
            "Bitcoin Kaffeetasse 'HODL TIGHT' – glänzend weiße Keramiktasse mit "
            "Krypto-Katzen-Design. Perfekt für Trader und HODLer. Fassungsvermögen "
            "325 ml (11oz). Mikrowellen- und spülmaschinengeeignet. Verfügbar in "
            "Ceramic White, Yellow, Green."
        ),
        "metadata": {
            "sku": "MUG_HODL_001",
            "slug": "bitcoin-kaffeetasse-hodl-tight-krypto",
            "url": "https://thegeekcat.de/product/bitcoin-kaffeetasse-hodl-tight-krypto/",
            "category": "nerd-gear",
            "subcategory": "mug",
            "price_range": "8,05 € – 12,82 €",
            "tags": ["bitcoin", "krypto", "hodl", "tasse", "kaffee", "trader", "blockchain"],
        },
    },
    {
        "text": (
            "Programmierer Tasse 'Geek Cat Signature' – zweifarbige Keramiktasse "
            "(außen weiß, innen weinrot) mit ikonischem The-Geek-Cat-Logo. "
            "325 ml, hitzebeständig, spülmaschinenfest. Verfügbar in Ceramic Red, "
            "Black, Blue. Für sauberen Code und heißen Kaffee."
        ),
        "metadata": {
            "sku": "MUG_SIG_002",
            "slug": "programmierer-tasse-geek-cat-signature",
            "url": "https://thegeekcat.de/product/programmierer-tasse-geek-cat-signature/",
            "category": "nerd-gear",
            "subcategory": "mug",
            "price_range": "12,82 €",
            "tags": ["programmierer", "tasse", "kaffee", "coding", "entwickler", "geschenk", "cat-lover"],
        },
    },
    {
        "text": (
            "KI Tasse 'Prompt Meowster' – Keramiktasse mit Siamkatze als Hacker "
            "mit Brille und Cyber-Interface. 330 ml (11oz). Spülmaschinenfest "
            "(Druck hält über 3.000 Zyklen), mikrowellengeeignet. Perfekt für "
            "Prompt Engineers, Data Scientists und AI-Nerds."
        ),
        "metadata": {
            "sku": "MUG_PROMPT_003",
            "slug": "ki-tasse-fuer-programmierer-prompt-meowster",
            "url": "https://thegeekcat.de/product/ki-tasse-fuer-programmierer-prompt-meowster/",
            "category": "nerd-gear",
            "subcategory": "mug",
            "price_range": "8,86 €",
            "tags": ["ki", "ai", "prompt-engineering", "tasse", "hacker", "chatgpt", "midjourney"],
        },
    },

    # ── T-Shirts ──
    {
        "text": (
            "Bitcoin T-Shirt 'I Stole Bitcoins' – frecher Hacker-Kater beim "
            "Polizeifoto (Mugshot). Ironische Anspielung auf die Pioniere der "
            "digitalen Währungen. Premium Gildan 64000 Softstyle: 100% "
            "ringgesponnene Baumwolle. Eurofit-Passform. 16 Farben verfügbar."
        ),
        "metadata": {
            "sku": "TSHIRT_STOLE_001",
            "slug": "lustiges-bitcoin-t-shirt-i-stole-bitcoins",
            "url": "https://thegeekcat.de/product/lustiges-bitcoin-t-shirt-i-stole-bitcoins/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "14,30 € – 25,70 €",
            "tags": ["bitcoin", "krypto", "hacker", "mugshot", "blockchain", "humor", "katze"],
        },
    },
    {
        "text": (
            "Krypto T-Shirt 'HODL TIGHT' – entschlossener Kater mit HODL-Motto. "
            "Perfekt für Trader, die auch bei Red Candles ihren Humor behalten. "
            "Gildan 64000 Softstyle: 100% ringgesponnene Baumwolle. "
            "Verfügbar in 10 Farben. Größen S-3XL."
        ),
        "metadata": {
            "sku": "TSHIRT_HODL_002",
            "slug": "lustiges-krypto-t-shirt-hodl-tight",
            "url": "https://thegeekcat.de/product/lustiges-krypto-t-shirt-hodl-tight/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "11,20 € – 23,04 €",
            "tags": ["krypto", "hodl", "bitcoin", "trader", "katze", "blockchain", "investor"],
        },
    },
    {
        "text": (
            "Binäres Programmierer T-Shirt 'cat.exe' – ASCII-Art-Katze geformt "
            "aus binärem Code. Ein Easter Egg für Linux-Fans und alle, die in "
            "Nullen und Einsen denken. Ringgesponnene Baumwolle, DTG-Druck. "
            "Verfügbar in 12 Farben."
        ),
        "metadata": {
            "sku": "TSHIRT_CATEXE_003",
            "slug": "binaeres-programmierer-t-shirt-cat-exe",
            "url": "https://thegeekcat.de/product/binaeres-programmierer-t-shirt-cat-exe/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "13,69 € – 25,60 €",
            "tags": ["programmierer", "cat-exe", "linux", "ascii-art", "binary", "coder", "katze"],
        },
    },
    {
        "text": (
            "IT WORKED ON MY MACHINE T-Shirt – der Klassiker unter den "
            "Developer-Statements. Premium Bio-Baumwolle, nachhaltiger "
            "On-Demand-Druck in Deutschland. Erinnert an die Ära vor "
            "Container-Lösungen und Dependency Hell. 16 Farben."
        ),
        "metadata": {
            "sku": "TSHIRT_IWON_004",
            "slug": "it-worked-on-my-machine-t-shirt",
            "url": "https://thegeekcat.de/product/it-worked-on-my-machine-t-shirt/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "15,89 € – 28,56 €",
            "tags": ["developer", "humor", "programming", "it-works", "biobaumwolle", "nachhaltig"],
        },
    },
    {
        "text": (
            "Katzen Geschenk T-Shirt 'Working on it' – Geek-Kater in horizontaler "
            "Position vor dem Laptop mit Nerdbrille und Computermaus. Text: "
            "'The IT is working on it'. Gildan 64000 Softstyle Bio-Baumwolle. "
            "Perfekt für IT-Support, Sysadmins und Softwareentwickler."
        ),
        "metadata": {
            "sku": "TSHIRT_WOI_005",
            "slug": "katzen-geschenk-fuer-programmierer-shirt",
            "url": "https://thegeekcat.de/product/katzen-geschenk-fuer-programmierer-shirt/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "12,32 € – 23,04 €",
            "tags": ["katze", "programmierer", "geschenk", "it-support", "sysadmin", "working-on-it"],
        },
    },
    {
        "text": (
            "Hacker T-Shirt 'Code Meowster' – kombiniert Hacker-Attitüde mit "
            "Katzen-Charme. Für alle, die im Terminal zu Hause sind. "
            "100% ringgesponnene Baumwolle, Tubular-Fit, DTG-Druck. "
            "Verfügbar in Azalea, Charcoal, Red, Maroon, Navy, Black, Purple."
        ),
        "metadata": {
            "sku": "TSHIRT_CODE_006",
            "slug": "lustiges-hacker-t-shirt-fuer-entwickler-code-meowster",
            "url": "https://thegeekcat.de/product/lustiges-hacker-t-shirt-fuer-entwickler-code-meowster/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "15,89 € – 28,56 €",
            "tags": ["hacker", "code", "terminal", "developer", "katze", "meowster", "programmierung"],
        },
    },
    {
        "text": (
            "Katzen T-Shirt 'Signature Shirt' – ikonisches The-Geek-Cat-Logo: "
            "Katzenpfote trifft auf RJ45-Netzwerkstecker. Gildan 64000 Softstyle. "
            "Tubular-Fit, Vintage-Look-Finish. Verfügbar in Maroon, Cardinal Red, "
            "Dark Chocolate, Navy, Black, Purple."
        ),
        "metadata": {
            "sku": "TSHIRT_SIG_007",
            "slug": "lustiges-katzen-t-shirt-fuer-programmierer-signature",
            "url": "https://thegeekcat.de/product/lustiges-katzen-t-shirt-fuer-programmierer-signature/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "15,89 € – 28,56 €",
            "tags": ["katze", "programmierer", "signature", "logo", "rj45", "netzwerk", "pfote"],
        },
    },
    {
        "text": (
            "KI T-Shirt 'Prompt Meowster' – Siamkatze als Hacker mit Brille und "
            "Headset. Neon-Elemente in Pink und Blau. 100% ringgesponnene "
            "Bio-Baumwolle. Moderner Euro-Fit. Für Prompt Engineers und "
            "AI-Nerds, die LLMs wie ChatGPT und Midjourney beherrschen."
        ),
        "metadata": {
            "sku": "TSHIRT_PROMPT_008",
            "slug": "lustiges-ki-t-shirt-fuer-prompt-engineers",
            "url": "https://thegeekcat.de/product/lustiges-ki-t-shirt-fuer-prompt-engineers/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "13,11 € – 24,51 €",
            "tags": ["ki", "ai", "prompt-engineering", "chatgpt", "midjourney", "neon", "hacker"],
        },
    },
    {
        "text": (
            "Informatiker Geschenk T-Shirt 'Click to Pet' – System-Cursor trifft "
            "Pfoten-Icon. Der wichtigste Befehl, den du je ausführen wirst, "
            "als tragbares Design. Ringgesponnene Baumwolle, High-Density-Druck. "
            "Verfügbar in White, Sport Grey, Azalea, Natural, Sand, Daisy uvm."
        ),
        "metadata": {
            "sku": "TSHIRT_CLICK_009",
            "slug": "informatiker-geschenk-t-shirt-click-to-pet",
            "url": "https://thegeekcat.de/product/informatiker-geschenk-t-shirt-click-to-pet/",
            "category": "apparel",
            "subcategory": "t-shirt",
            "price_range": "13,69 € – 25,60 €",
            "tags": ["informatiker", "geschenk", "click-to-pet", "cursor", "pfote", "minimalist", "katze"],
        },
    },

    # ── Hoodies & Sweatshirts ──
    {
        "text": (
            "Krypto Hoodie 'HODL TIGHT' – Kapuzenpullover mit entschlossenem "
            "Kater und HODL-Motto. Premium Gildan 18500 Heavy Blend (271 g/m²). "
            "50% Baumwolle / 50% Polyester. Doppellagige Kapuze, Kängurutasche. "
            "Verfügbar in Black, Light Pink, Maroon, White. Größen S-5XL."
        ),
        "metadata": {
            "sku": "HOODIE_HODL_001",
            "slug": "lustiger-krypto-hoodie-hodl-tight",
            "url": "https://thegeekcat.de/product/lustiger-krypto-hoodie-hodl-tight/",
            "category": "apparel",
            "subcategory": "hoodie",
            "price_range": "25,58 € – 41,68 €",
            "tags": ["krypto", "hodl", "hoodie", "bitcoin", "kapuzenpullover", "gildan-18500"],
        },
    },
    {
        "text": (
            "Programmierer Hoodie 'Geek Cat Logo' – ikonisches The-Geek-Cat-Logo "
            "auf der Brust. Gildan 18500 Heavy Blend. Doppellagige Kapuze, "
            "Kängurutasche, Spandex-Rippbündchen. Verfügbar in 11 Farben "
            "(Cherry Red, Navy, Black, Purple uvm.). Größen S-5XL."
        ),
        "metadata": {
            "sku": "HOODIE_SIG_002",
            "slug": "programmierer-hoodie-software-entwickler",
            "url": "https://thegeekcat.de/product/programmierer-hoodie-software-entwickler/",
            "category": "apparel",
            "subcategory": "hoodie",
            "price_range": "25,68 € – 41,45 €",
            "tags": ["programmierer", "hoodie", "software-entwickler", "logo", "gildan-18500", "geschenk"],
        },
    },
    {
        "text": (
            "Katzen Hoodie 'Working on it' – Geek-Kater horizontal vor Laptop "
            "mit Text 'The IT is working on it'. Bio-Baumwolle / recyceltes "
            "Polyester. Unisex-Schnitt. Verfügbar in Charcoal, Deep Black, "
            "Oxford Navy. Größen XS-3XL."
        ),
        "metadata": {
            "sku": "HOODIE_WOI_003",
            "slug": "lustiges-katzen-geschenk-fuer-programmierer",
            "url": "https://thegeekcat.de/product/lustiges-katzen-geschenk-fuer-programmierer/",
            "category": "apparel",
            "subcategory": "hoodie",
            "price_range": "30,59 € – 35,82 €",
            "tags": ["katze", "programmierer", "geschenk", "working-on-it", "hoodie", "bio-baumwolle"],
        },
    },
    {
        "text": (
            "Krypto Sweatshirt 'HODL TIGHT' – Pullover mit entspanntem Kater, "
            "der seine Coins fest im Griff hat. Gildan 18000 Heavy Blend "
            "(271 g/m²). 50% Baumwolle / 50% Polyester. Gebürstetes Innenfutter. "
            "Verfügbar in 8 Farben (White, Light Blue, Ash, Sport Grey uvm.)."
        ),
        "metadata": {
            "sku": "SWEAT_HODL_004",
            "slug": "lustiges-krypto-sweatshirt",
            "url": "https://thegeekcat.de/product/lustiges-krypto-sweatshirt/",
            "category": "apparel",
            "subcategory": "sweatshirt",
            "price_range": "19,04 € – 54,52 €",
            "tags": ["krypto", "hodl", "sweatshirt", "bitcoin", "pullover", "gildan-18000", "comfort"],
        },
    },
]


# ═══════════════════════════════════════════════════════════
#  2.  MEME REPOSITORY  —  IT / blockchain / AI running jokes
# ═══════════════════════════════════════════════════════════

MEMES = [
    {
        "text": (
            "Two buttons meme: left button says 'Write tests', right button says "
            "'Push to production on Friday at 17:00'. Cat staring at buttons "
            "chooses the right one. Caption: 'Senior dev priorities.'"
        ),
        "metadata": {"format": "image_macro", "theme": "devops", "tags": ["testing", "friday", "production"]},
    },
    {
        "text": (
            "Distracted boyfriend meme: man looks at 'Kubernetes in production' "
            "while girlfriend 'Docker Compose locally' glares. The man is a cat. "
            "Caption: 'Every cloud-native developer.'"
        ),
        "metadata": {"format": "image_macro", "theme": "cloud", "tags": ["kubernetes", "docker", "compose"]},
    },
    {
        "text": (
            "Drake hotline meme: top panel 'Using ChatGPT to write code' — Drake "
            "cat disapproves. Bottom panel 'Using ChatGPT to write sarcastic "
            "commit messages' — Drake cat approves. Caption: 'Proper AI usage.'"
        ),
        "metadata": {"format": "image_macro", "theme": "ai", "tags": ["chatgpt", "ai", "commit", "humor"]},
    },
    {
        "text": (
            "Cat screaming at keyboard meme: the keyboard is a MacBook butterfly "
            "switch. Caption: 'Me debugging a null pointer exception at 2 AM.'"
        ),
        "metadata": {"format": "image_macro", "theme": "programming", "tags": ["debugging", "null-pointer", "keyboard"]},
    },
    {
        "text": (
            "This is fine meme — but the dog is replaced by a cat, the coffee cup "
            "says 'Java', and the flames are labelled: 'Microservices', 'Kafka', "
            "'Event Sourcing', 'CQRS'. Caption: 'Distributed systems architecture.'"
        ),
        "metadata": {"format": "image_macro", "theme": "architecture", "tags": ["microservices", "kafka", "distributed"]},
    },
    {
        "text": (
            "Galaxy brain meme: 1st panel 'I use tabs', 2nd panel 'I use spaces', "
            "3rd panel 'I use tabs and spaces and my code still compiles', "
            "4th panel (cat) 'I let the formatter decide and go pet my cat.'"
        ),
        "metadata": {"format": "image_macro", "theme": "programming", "tags": ["tabs", "spaces", "formatter", "ide"]},
    },
    {
        "text": (
            "Gru plan meme: Phase 1 'Deploy AI agent', Phase 2 'It writes marketing "
            "copy in German', Phase 3 '???', Phase 4 'Profit'. The ??? panel is "
            "replaced by a cat napping on a keyboard."
        ),
        "metadata": {"format": "image_macro", "theme": "business", "tags": ["ai-agent", "marketing", "profit", "gru"]},
    },
    {
        "text": (
            "Cat loaf on a server rack. Caption: 'Production deployment in progress. "
            "Estimated time to purr-oduction: 9 lives.'"
        ),
        "metadata": {"format": "image_macro", "theme": "devops", "tags": ["deployment", "server", "cat"]},
    },
    {
        "text": (
            "Woman yelling at cat meme: Woman 'Your microservice is down again', "
            "Cat 'I'm not in the office on Fridays'. Caption: 'On-call rotations.'"
        ),
        "metadata": {"format": "image_macro", "theme": "sre", "tags": ["on-call", "microservice", "friday"]},
    },
    {
        "text": (
            "El Risitas (Spanish laughing guy) meme but it's a cat. The text reads: "
            "'When the client says \"it worked in staging\" and you check prod and the "
            "database is on fire.'"
        ),
        "metadata": {"format": "image_macro", "theme": "devops", "tags": ["staging", "production", "database", "fire"]},
    },
]


# ═══════════════════════════════════════════════════════════
#  3.  BRAND GUIDELINES  —  Global brand voice and rules
# ═══════════════════════════════════════════════════════════

BRAND_GUIDELINES = [
    {
        "key": "tone",
        "text": (
            "Tone: sarcastic, ironic, and brutally humorous. Speak to the audience "
            "as fellow engineers who are simultaneously brilliant and tired. "
            "Use German with occasional English tech terms (Denglish) for authenticity. "
            "Never be boring. Never be corporate. Always be a little bit mean — "
            "but lovingly so, like a cat judging you from the bookshelf."
        ),
    },
    {
        "key": "audience",
        "text": (
            "Primary audience: German-speaking IT professionals (25-45), "
            "software engineers, DevOps/SREs, blockchain developers, AI/ML researchers, "
            "cybersecurity analysts, and cloud architects. They are early adopters, "
            "value efficiency, and have a dark sense of humour. They own at least one cat."
        ),
    },
    {
        "key": "language",
        "text": (
            "Language: German (Deutsch). Use 'Du' not 'Sie' — we're friends, "
            "not business partners. Sprinkle English tech jargon liberally: "
            "'Deployment', 'Pipeline', 'Refactor', 'Legacy', 'Cloud', 'Cluster'. "
            "Avoid overly formal constructions. A typical sentence: "
            "'Brudi, dein Deployment läuft wieder gegen die Wand — aber hier, "
            "dieses T-Shirt macht den Schmerz erträglich.'"
        ),
    },
    {
        "key": "cat_references",
        "text": (
            "Every single post MUST include at least one cat pun, cat reference, "
            "or feline metaphor. Examples: 'catched exception', 'purr-duction', "
            "'meow-nitoring', 'cat-astrophe', 'fur-mware', 'claw-ud', "
            "'hiss-tory', 'paw-ssword', 'litter-ally unmaintainable code'. "
            "The cat is not optional — it's the brand."
        ),
    },
    {
        "key": "hashtags",
        "text": (
            "Required hashtags per post: #TheGeekCat #KatzeTech. "
            "Optional dynamic hashtags (2-3): #DevOpsHumor, #BlockchainKatze, "
            "#AIKatzen, #ITMemes, #ProgrammierKatze, #LinuxLiebe, "
            "#CloudNative, #KatzeStattKrise. Never use more than 5 hashtags total."
        ),
    },
    {
        "key": "length",
        "text": (
            "Instagram posts: max 2200 characters (including hashtags). "
            "Facebook posts: max 5000 characters. "
            "Prefer shorter (150-300 chars) for higher engagement. "
            "The first line must hook immediately — ask a sarcastic question "
            "or state an absurd IT truth."
        ),
    },
    {
        "key": "content_pillars",
        "text": (
            "Content pillars (rotate): "
            "1) Product humour — feature a product with a funny IT scenario. "
            "2) Meme recycling — adapt trending tech memes with our brand cat. "
            "3) IT truth bombs — relatable pain points (legacy code, meetings, on-call). "
            "4) Cat supremacy — why cats are better than dogs (and better than most devs). "
            "5) Limited editions — FOMO-driven product drops with countdowns."
        ),
    },
    {
        "key": "call_to_action",
        "text": (
            "CTA style: ironic and low-pressure. Never 'Buy now' — instead: "
            "'Dein Cat-ffeinator braucht das' (your caffeine-addicted cat needs this), "
            "'Wenn nicht jetzt, wann dann? (wenn dein CI/CD grün ist)', "
            "'Bestell's, bevor der nächste Outgoing dein Budget frisst.'"
        ),
    },
]


# ═══════════════════════════════════════════════════════════
#  4.  SAMPLE BRAND RULES  —  seeded per user on first login
# ═══════════════════════════════════════════════════════════

SAMPLE_USER_BRAND_RULES = {
    "tone": "Sarcastic and ironic, like a senior dev reviewing a junior's PR",
    "preferred_platform": "instagram",
    "posting_frequency": "3x per week",
    "excluded_topics": "politics, religion, nsfw",
    "preferred_meme_style": "dark humor about tech failures",
}


# ═══════════════════════════════════════════════════════════
#  LOADER
# ═══════════════════════════════════════════════════════════

def seed_pod_catalog(store: ChromaStore) -> int:
    """Load POD products into the pod_catalog collection."""
    existing = store.search_global("pod_catalog", "cat t-shirt", k=1)
    if existing:
        logger.info("pod_catalog already seeded (%d items found), skipping", len(existing))
        return 0

    count = store.add_global_items("pod_catalog", POD_PRODUCTS)
    logger.info("seeded pod_catalog with %d products", count)
    return count


def seed_meme_repo(store: ChromaStore) -> int:
    """Load IT memes into the meme_repo collection."""
    existing = store.search_global("meme_repo", "cat programming meme", k=1)
    if existing:
        logger.info("meme_repo already seeded, skipping")
        return 0

    count = store.add_global_items("meme_repo", MEMES)
    logger.info("seeded meme_repo with %d memes", count)
    return count


def seed_brand_guidelines(store: ChromaStore) -> int:
    """Load global brand guidelines into brand_guidelines collection."""
    existing = store.search_global("brand_guidelines", "tone sarcastic", k=1)
    if existing:
        logger.info("brand_guidelines already seeded, skipping")
        return 0

    items = [
        {
            "id": g["key"],
            "text": g["text"],
            "metadata": {"key": g["key"]},
        }
        for g in BRAND_GUIDELINES
    ]
    count = store.add_global_items("brand_guidelines", items)
    logger.info("seeded brand_guidelines with %d rules", count)
    return count


def seed_user_rules(store: ChromaStore, user_id: str = "default") -> int:
    """Seed default brand rules for a specific user (idempotent)."""
    from backend.memory.long_term import LongTermMemory
    from backend.memory.search.sqlite_fts5 import SQLiteFTS5

    ltm = LongTermMemory(vector_store=store, bm25=SQLiteFTS5())
    existing = ltm.get_brand_rules(user_id)
    count = 0
    for key, value in SAMPLE_USER_BRAND_RULES.items():
        if key not in existing:
            ltm.save_brand_rule(user_id, key, value)
            count += 1
    if count:
        logger.info("seeded %d brand rules for user '%s'", count, user_id)
    return count


def run_all(user_id: str = "default"):
    """Run all seed loaders."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    store = ChromaStore()

    total = 0
    total += seed_pod_catalog(store)
    total += seed_meme_repo(store)
    total += seed_brand_guidelines(store)
    total += seed_user_rules(store, user_id)

    if total == 0:
        logger.info("All collections already seeded — nothing to do.")
    else:
        logger.info("Seeding complete: %d new items loaded.", total)

    return total


if __name__ == "__main__":
    run_all()
