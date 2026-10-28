# mtg price guessr

A multiplayer party game: each round shows one printing of a Magic card, and
everyone has 30 seconds to guess its TCGplayer price. Built like ageoguessr:
buildless React served by Flask, with clients polling once a second.

## Quick start (local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python build_dataset.py --max-oracles 3000   # small dataset, ~1.5MB
PORT=5000 .venv/bin/python server.py
```

Visit http://localhost:5000 and click "Start a new game" (or type `newgame`
anywhere on the page; `endgame` stops it).

## On the server

```bash
.venv/bin/python build_dataset.py            # full dataset, ~8MB, takes ~10s after an ~80MB download
PORT=5000 .venv/bin/python server.py
```

Prices change daily, so rebuild every so often (e.g. a nightly cron running
`build_dataset.py`) and restart the server. The build downloads Scryfall's
"Default Cards" file into `data/raw/` only when Scryfall has a newer one.

Card images aren't stored; the page loads them straight from Scryfall's image
CDN (cards.scryfall.io), which has no rate limits.

## Settings (environment variables)

- `TURN_DURATION_SECONDS` (default 30), `REVEAL_SECONDS` (default 5), `TOTAL_ROUNDS` (default 50)
- `SHOW_PRINT_RUNS=0` hides the estimated print runs
- `DATA_PATH` – where the built dataset lives (default `data/cards.json.gz`)

## How cards are chosen

Picking printings uniformly would give a bulk common almost every round, so
`sampler.py` first draws a target price from the distribution we want, then
picks uniformly among the printings priced closest to it. The target is a
smooth (monotone cubic in log-price) curve through:

| percentile | 10 | 25 | 50 | 75 | 90 | 97 | 99.5 |
|---|---|---|---|---|---|---|---|
| price | $0.50 | $4 | $20 | $80 | $300 | $1,500 | $6,000 |

Run `python sampler.py` to see the realised distribution for your dataset.
Because cards are matched by price, throwing away bulk doesn't skew anything,
so the dataset keeps every printing worth $1+ but only 10% of cheaper ones
(`--bulk-keep`). No card appears twice in a game, in any printing.

Each printing is priced as its non-foil version when it has one, otherwise
foil, otherwise etched foil; the page says which one you're pricing. Basic
lands, tokens, digital-only and non-English printings are left out.

## Scoring

Prices and guesses below $0.25 count as $0.25. Points are
`100 × (1 − |log10(guess / price)|)`, floored at 0: exact is 100, 2× off is 70,
10× off is 0.

## Estimated print runs

Wizards doesn't publish print runs, so `print_runs.py` estimates them: it
starts from the commonly cited figures for Alpha through Fallen Empires, then
extrapolates from the set's era, its product type (main set, Masters, Commander
precon, Secret Lair, promo, ...), rarity and special treatments (borderless,
showcase, serialized). They're order-of-magnitude hints, and the tables there
are easy to tweak.
