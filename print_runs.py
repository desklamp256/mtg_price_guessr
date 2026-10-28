"""Rough estimates of how many copies of a printing exist.

Wizards has never published print runs (they stopped after the early sets), so
this is a heuristic, not data. It's anchored to the commonly cited figures for
the 1993-94 sets, then extrapolated from the set's era, its product type,
the card's rarity and whether it's a special treatment (borderless, showcase,
serialized, ...). Treat the numbers as order-of-magnitude hints: they're good
at telling an Alpha rare from a Revised common or a Secret Lair from a
Commander precon, not at telling 300k from 500k.

Everything here is a plain table so it's easy to tweak.
"""
from __future__ import annotations

# Copies of each RARE in the early sets, from commonly cited print-run figures
# (Alpha ~2.6M cards total, Beta ~7.3M, Unlimited ~35M, ...).
EARLY_SET_RARE_COPIES = {
    "lea": 1_100,     # Alpha
    "leb": 3_300,     # Beta
    "2ed": 18_500,    # Unlimited
    "arn": 12_000,    # Arabian Nights
    "atq": 25_000,    # Antiquities
    "leg": 30_000,    # Legends
    "drk": 60_000,    # The Dark
    "3ed": 400_000,   # Revised
    "fem": 300_000,   # Fallen Empires
    "sum": 1_000,     # Summer Magic (recalled and mostly destroyed)
}
# In the 1993-94 sheets commons/uncommons were much more plentiful relative to rares
EARLY_RARITY_MULT = {"common": 14.5, "uncommon": 4.0, "rare": 1.0}

# Fixed-size products: everything in them was printed once per box/deck sold
FIXED_SET_COPIES = {
    "ced": 9_500,     # Collectors' Edition
    "cei": 5_000,     # International Collectors' Edition
}

# Copies of each rare in a typical main expansion released in a given year
ERA_RARE_COPIES = [
    (1995, 300_000),
    (2003, 250_000),
    (2009, 400_000),
    (2020, 700_000),
    (9999, 500_000),
]

# Relative to a rare in the same product
RARITY_MULT = {
    "common": 6.0,
    "uncommon": 2.5,
    "rare": 1.0,
    "mythic": 0.5,
    "special": 0.3,
    "bonus": 0.3,
}

# Multiplier on the era's main-set rare figure, by Scryfall set_type
SET_TYPE_MULT = {
    "core": 1.0,
    "expansion": 1.0,
    "masters": 0.25,
    "draft_innovation": 0.5,
    "starter": 0.5,
    "funny": 0.3,
    "planechase": 0.3,
    "archenemy": 0.3,
    "treasure_chest": 0.1,
    "alchemy": 0.0,
}

# Flat copies-per-card for products where rarity doesn't drive the count
SET_TYPE_FLAT = {
    "commander": 150_000,   # each precon has one of each card
    "duel_deck": 60_000,
    "premium_deck": 40_000,
    "from_the_vault": 30_000,
    "spellbook": 30_000,
    "box": 30_000,          # Secret Lair drops and similar
    "memorabilia": 20_000,  # World Championship decks, gold-bordered
    "promo": 20_000,
    "masterpiece": 5_000,   # roughly 1 per 144 packs
    "arsenal": 10_000,
}


def _era_rare_copies(year: int) -> int:
    for cutoff, copies in ERA_RARE_COPIES:
        if year < cutoff:
            return copies
    return ERA_RARE_COPIES[-1][1]


def estimate_copies(card: dict) -> int | None:
    """Estimated copies ever printed of this Scryfall card object (one printing)."""
    set_code = card.get("set", "")
    set_type = card.get("set_type", "")
    rarity = card.get("rarity", "common")
    year = int((card.get("released_at") or "2000")[:4])
    promo_types = set(card.get("promo_types") or [])
    frame_effects = set(card.get("frame_effects") or [])

    if "serialized" in promo_types:
        return 500
    if set_code in FIXED_SET_COPIES:
        return FIXED_SET_COPIES[set_code]
    if set_code in EARLY_SET_RARE_COPIES:
        mult = EARLY_RARITY_MULT.get(rarity, 1.0)
        return _round(EARLY_SET_RARE_COPIES[set_code] * mult)

    if set_type in SET_TYPE_FLAT:
        copies = float(SET_TYPE_FLAT[set_type])
        if set_type == "promo" and promo_types & {"prerelease", "buyabox", "bundle", "promopack"}:
            copies *= 3
    else:
        mult = SET_TYPE_MULT.get(set_type, 0.3)
        if mult == 0:
            return None
        copies = _era_rare_copies(year) * mult * RARITY_MULT.get(rarity, 1.0)

    # Collector-booster style treatments are a small slice of a set's run
    is_treatment = (
        card.get("border_color") == "borderless"
        or frame_effects & {"showcase", "extendedart", "inverted", "etched"}
        or promo_types & {"textured", "galaxyfoil", "surgefoil", "halofoil", "raisedfoil", "confettifoil"}
    )
    if is_treatment and set_type in ("expansion", "core", "masters", "draft_innovation"):
        copies *= 0.2
    # A foil-only printing in a set that normally has non-foils is a foil-sheet card
    if card.get("finishes") == ["foil"] and set_type in ("expansion", "core") and year < 2020:
        copies *= 0.15
    return _round(copies)


def _round(x: float) -> int:
    """Two significant figures; anything more would be false precision."""
    if x <= 0:
        return 0
    from math import floor, log10
    digits = max(0, int(floor(log10(x))) - 1)
    return int(round(x / 10 ** digits) * 10 ** digits)
