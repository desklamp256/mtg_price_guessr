#!/usr/bin/env python3
"""Build data/cards.json.gz from Scryfall's bulk data.

    python build_dataset.py                       # full build (for the server)
    python build_dataset.py --max-oracles 3000    # small build for local testing

Downloads Scryfall's "Default Cards" file (~80MB gzipped, every English
printing with TCGplayer prices) plus the set list, then writes a compact file
the game server loads at startup. Card images aren't stored: the game links
straight to Scryfall's image CDN.

What ends up in the output:
  * pool      – printings the game may ask about. Every printing worth $1+ is
                kept; only --bulk-keep (default 10%) of the cheaper ones are,
                since the sampler draws from bulk so rarely anyway.
  * oracles   – for each card in the pool, ALL its paper printings (for the
                "other printings" list), in release order.
  * printings – the per-printing details those two refer to.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from print_runs import estimate_copies

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "data", "cards.json.gz")
RAW_DIR = os.path.join(HERE, "data", "raw")
HEADERS = {"User-Agent": "mtg-price-guessr/1.0", "Accept": "application/json"}

# Layouts that aren't really "a Magic card" for pricing purposes
SKIP_LAYOUTS = {"token", "double_faced_token", "emblem", "art_series", "vanguard", "scheme", "planar"}
BULK_THRESHOLD = 1.00


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def download_bulk(force: bool) -> tuple[str, str]:
    """Fetch the Default Cards file unless today's copy is already on disk."""
    os.makedirs(RAW_DIR, exist_ok=True)
    index = json.loads(_get("https://api.scryfall.com/bulk-data"))
    entry = next(d for d in index["data"] if d["type"] == "default_cards")
    path = os.path.join(RAW_DIR, "default_cards.jsonl.gz")
    stamp_path = path + ".updated_at"
    stamp = open(stamp_path).read().strip() if os.path.exists(stamp_path) else None
    if force or stamp != entry["updated_at"] or not os.path.exists(path):
        print(f"Downloading {entry['jsonl_download_uri']} ...", flush=True)
        req = urllib.request.Request(entry["jsonl_download_uri"], headers=HEADERS)
        tmp = path + ".tmp"
        with urllib.request.urlopen(req, timeout=600) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
        os.replace(tmp, path)
        with open(stamp_path, "w") as f:
            f.write(entry["updated_at"])
    else:
        print("Bulk file already up to date.", flush=True)
    return path, entry["updated_at"]


def fetch_sets() -> dict:
    sets = {}
    url = "https://api.scryfall.com/sets"
    while url:
        page = json.loads(_get(url))
        for s in page["data"]:
            sets[s["code"]] = {"name": s["name"], "icon": s.get("icon_svg_uri")}
        url = page.get("next_page") if page.get("has_more") else None
    return sets


def _price(card: dict) -> tuple[float | None, str | None]:
    """The printing's price: non-foil if it has one, else foil, else etched."""
    prices = card.get("prices") or {}
    for key, finish in (("usd", "nonfoil"), ("usd_foil", "foil"), ("usd_etched", "etched")):
        value = prices.get(key)
        if value:
            return float(value), finish
    return None, None


def _images(card: dict) -> tuple[str | None, str | None]:
    if card.get("image_uris"):
        return card["image_uris"].get("normal"), None
    faces = card.get("card_faces") or []
    imgs = [f.get("image_uris", {}).get("normal") for f in faces]
    return (imgs[0] if imgs else None), (imgs[1] if len(imgs) > 1 else None)


def is_paper_printing(card: dict) -> bool:
    return (
        card.get("lang") == "en"
        and not card.get("digital")
        and "paper" in (card.get("games") or [])
        and card.get("layout") not in SKIP_LAYOUTS
        and not card.get("oversized")
        and card.get("oracle_id")
        and card.get("set_type") != "token"
    )


def can_be_asked(card: dict) -> bool:
    """Whether the game may pick this printing (it can still be listed regardless)."""
    return "Basic Land" not in (card.get("type_line") or "")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--bulk-keep", type=float, default=0.10,
                    help=f"fraction of sub-${BULK_THRESHOLD:.2f} printings to keep in the pool (default 0.10)")
    ap.add_argument("--max-oracles", type=int, default=0,
                    help="keep only this many distinct cards (random), for a small local dataset")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--force-download", action="store_true")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    bulk_path, updated_at = download_bulk(args.force_download)
    sets = fetch_sets()

    printings: dict[str, dict] = {}
    by_oracle: dict[str, list[str]] = defaultdict(list)
    pool: list[str] = []
    with gzip.open(bulk_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line.startswith("{"):
                continue
            card = json.loads(line)
            if not is_paper_printing(card):
                continue
            price, finish = _price(card)
            img, img_back = _images(card)
            pid = card["id"]
            printings[pid] = {
                "name": card["name"],
                "set": card["set"],
                "set_name": card["set_name"],
                "set_type": card.get("set_type"),
                "released": card.get("released_at"),
                "rarity": card.get("rarity"),
                "cn": card.get("collector_number"),
                "finishes": card.get("finishes") or [],
                "price": price,
                "price_finish": finish,
                "img": img,
                "img_back": img_back,
                "copies": estimate_copies(card),
                "scryfall": (card.get("scryfall_uri") or "").split("?")[0],
            }
            by_oracle[card["oracle_id"]].append(pid)
            if price is not None and img and can_be_asked(card):
                if price >= BULK_THRESHOLD or rng.random() < args.bulk_keep:
                    pool.append(pid)

    oracle_of = {pid: oid for oid, pids in by_oracle.items() for pid in pids}
    if args.max_oracles:
        pool_oracles = sorted({oracle_of[pid] for pid in pool})
        keep = set(rng.sample(pool_oracles, min(args.max_oracles, len(pool_oracles))))
        pool = [pid for pid in pool if oracle_of[pid] in keep]

    used_oracles = {oracle_of[pid] for pid in pool}
    oracles = {}
    for oid in used_oracles:
        pids = sorted(by_oracle[oid], key=lambda p: (printings[p]["released"] or "", printings[p]["set"], printings[p]["cn"] or ""))
        oracles[oid] = pids
    kept_printings = {pid: dict(printings[pid], oracle=oid) for oid, pids in oracles.items() for pid in pids}
    used_sets = {p["set"] for p in kept_printings.values()}

    out = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "scryfall_updated_at": updated_at,
        "sets": {code: sets.get(code, {"name": code, "icon": None}) for code in used_sets},
        "printings": kept_printings,
        "oracles": oracles,
        "pool": pool,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    tmp = args.out + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, args.out)

    prices = sorted(kept_printings[p]["price"] for p in pool)
    pct = lambda q: prices[min(len(prices) - 1, int(q * len(prices)))]
    print(f"Wrote {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB)")
    print(f"  pool: {len(pool)} printings of {len(oracles)} cards; {len(kept_printings)} printings listed")
    print(f"  raw pool prices: p10 ${pct(.1):.2f}  p50 ${pct(.5):.2f}  p90 ${pct(.9):.2f}  max ${prices[-1]:.2f}")


if __name__ == "__main__":
    sys.exit(main())
