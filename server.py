from __future__ import annotations

import gzip
import json
import math
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template, request

from config import (
    DATA_PATH,
    REVEAL_SECONDS,
    SHOW_PRINT_RUNS,
    TOTAL_ROUNDS,
    TURN_DURATION_SECONDS,
)
from sampler import PRICE_FLOOR, PriceSampler, floored

app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------------------------------------------------------------------------
# Card data
# ---------------------------------------------------------------------------
def _load_data() -> dict:
    if not os.path.exists(DATA_PATH):
        raise SystemExit(f"No card data at {DATA_PATH}. Run: python build_dataset.py")
    with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
        data = json.load(f)
    print(f"[mtg-price-guessr] loaded {len(data['pool'])} pool printings of {len(data['oracles'])} cards "
          f"(Scryfall data from {data.get('scryfall_updated_at', '?')[:10]})")
    return data


DATA = _load_data()
SAMPLER = PriceSampler([(pid, DATA["printings"][pid]["price"]) for pid in DATA["pool"]])

# Max/min guesses the API accepts; anything below the floor scores as the floor
MAX_GUESS = 1_000_000.0
# Being off by this factor (or more) in either direction scores zero
ZERO_POINTS_FACTOR = 10.0
# Players silent this long drop off the leaderboard (unless they have points)
LEADERBOARD_IDLE_SECONDS = 60


def score_guess(guess: float, truth: float) -> int:
    """100 for exact, falling linearly with log-distance to 0 at a 10x miss."""
    off = abs(math.log10(floored(guess) / floored(truth)))
    return int(round(100 * max(0.0, 1 - off / math.log10(ZERO_POINTS_FACTOR))))


def _printing_public(pid: str, with_price: bool) -> dict:
    p = DATA["printings"][pid]
    out = {
        "id": pid,
        "set": p["set"],
        "set_name": p["set_name"],
        "set_icon": (DATA["sets"].get(p["set"]) or {}).get("icon"),
        "year": (p["released"] or "")[:4],
        "rarity": p["rarity"],
        "cn": p["cn"],
        "finishes": p["finishes"],
        "img": p["img"],
        "img_back": p["img_back"],
        "copies": p["copies"] if SHOW_PRINT_RUNS else None,
    }
    if with_price:
        out["price"] = floored(p["price"]) if p["price"] is not None else None
        out["price_finish"] = p["price_finish"]
        out["scryfall"] = p["scryfall"]
    return out


def card_payload(pid: str, reveal: bool) -> dict:
    """The printing being priced plus every other printing of the card, oldest first.
    Prices (of any printing) are only included once the round is revealed."""
    p = DATA["printings"][pid]
    others = [o for o in DATA["oracles"][p["oracle"]] if o != pid]
    return {
        "name": p["name"],
        "finish": p["price_finish"],
        "printing": _printing_public(pid, with_price=reveal),
        "others": [_printing_public(o, with_price=reveal) for o in others],
    }


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------
@dataclass
class Player:
    player_id: str
    username: str
    score: int = 0
    last_seen: float = 0.0


@dataclass
class RoundInfo:
    round_index: int
    printing_id: str
    true_price: float
    ends_at: datetime
    reveal_ends_at: Optional[datetime] = None
    phase: str = "guessing"  # "guessing" or "reveal"


@dataclass
class GameState:
    active: bool = False
    game_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    total_rounds: int = TOTAL_ROUNDS
    current_round_index: int = 0
    current_round: Optional[RoundInfo] = None
    # Picked ahead of time so clients can preload its images during the reveal
    next_printing_id: Optional[str] = None
    players: Dict[str, Player] = field(default_factory=dict)
    # players taking part in the current game; ones who skip 2 rounds in a
    # row stop counting towards "everyone's locked in"
    active_players: set[str] = field(default_factory=set)
    misses: Dict[str, int] = field(default_factory=dict)
    # guesses[round_index][player_id] = price in USD
    guesses: Dict[int, Dict[str, float]] = field(default_factory=dict)
    # results[round_index][player_id] = {"guess": float, "points": int}
    results: Dict[int, Dict[str, dict]] = field(default_factory=dict)
    ready: Dict[int, set[str]] = field(default_factory=dict)
    used_oracles: set[str] = field(default_factory=set)
    rng: random.Random = field(default_factory=random.Random)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self):
        self.active = False
        self.game_id = uuid.uuid4().hex
        self.total_rounds = TOTAL_ROUNDS
        self.current_round_index = 0
        self.current_round = None
        self.next_printing_id = None
        for p in self.players.values():
            p.score = 0
        self.active_players = set()
        self.misses = {}
        self.guesses = {}
        self.results = {}
        self.ready = {}
        self.used_oracles = set()


STATE = GameState()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.timestamp() * 1000) if dt else None


def _pick_printing_locked() -> Optional[str]:
    """A fresh card for this game (no card shows up twice, in any printing)."""
    pid = SAMPLER.pick(STATE.rng, exclude=lambda p: DATA["printings"][p]["oracle"] in STATE.used_oracles)
    if pid is None:  # ran out of cards; allow repeats
        pid = SAMPLER.pick(STATE.rng)
    if pid is not None:
        STATE.used_oracles.add(DATA["printings"][pid]["oracle"])
    return pid


def _start_next_round_locked():
    pid = STATE.next_printing_id or _pick_printing_locked()
    STATE.next_printing_id = None
    STATE.current_round_index += 1
    STATE.current_round = RoundInfo(
        round_index=STATE.current_round_index,
        printing_id=pid,
        true_price=floored(DATA["printings"][pid]["price"]),
        ends_at=_now() + timedelta(seconds=TURN_DURATION_SECONDS),
    )
    STATE.guesses.setdefault(STATE.current_round_index, {})
    STATE.ready[STATE.current_round_index] = set()


def _finalize_round_locked():
    rnd = STATE.current_round
    guesses = STATE.guesses.get(rnd.round_index, {})
    round_results: Dict[str, dict] = {}
    for pid, guess in guesses.items():
        points = score_guess(guess, rnd.true_price)
        player = STATE.players.get(pid)
        if player:
            player.score += points
        round_results[pid] = {"guess": guess, "points": points}
    STATE.results[rnd.round_index] = round_results
    for pid in list(STATE.active_players):
        if pid in guesses:
            STATE.misses[pid] = 0
        else:
            STATE.misses[pid] = STATE.misses.get(pid, 0) + 1
            if STATE.misses[pid] >= 2:
                STATE.active_players.discard(pid)
    rnd.phase = "reveal"
    rnd.reveal_ends_at = _now() + timedelta(seconds=REVEAL_SECONDS)
    if STATE.current_round_index < STATE.total_rounds:
        STATE.next_printing_id = _pick_printing_locked()


def _finalize_round_early_if_all_ready_locked():
    rnd = STATE.current_round
    if not rnd or rnd.phase != "guessing" or not STATE.active_players:
        return
    if STATE.active_players.issubset(STATE.ready.get(rnd.round_index, set())):
        _finalize_round_locked()


def _advance_if_needed_locked():
    if not STATE.active or STATE.current_round is None:
        return
    rnd = STATE.current_round
    if rnd.phase == "guessing" and _now() >= rnd.ends_at:
        _finalize_round_locked()
    elif rnd.phase == "reveal" and _now() >= rnd.reveal_ends_at:
        if STATE.current_round_index >= STATE.total_rounds:
            STATE.active = False  # game over; last reveal stays visible
            return
        _start_next_round_locked()


def _touch_player_locked(player_id: Optional[str]) -> Optional[Player]:
    if not player_id:
        return None
    player = STATE.players.get(player_id)
    if player is None:
        player = Player(player_id=player_id, username=_generate_username())
        STATE.players[player_id] = player
    player.last_seen = time.time()
    return player


def _public_leaderboard() -> List[Dict]:
    cutoff = time.time() - LEADERBOARD_IDLE_SECONDS
    players = [p for p in STATE.players.values() if p.score > 0 or p.last_seen >= cutoff]
    players.sort(key=lambda p: (-p.score, p.username.lower()))
    return [{"player_id": p.player_id, "username": p.username, "score": p.score} for p in players]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    with STATE.lock:
        player = _touch_player_locked(data.get("player_id") or uuid.uuid4().hex)
        if data.get("desired_username"):
            player.username = _sanitize_username(data["desired_username"])
    return jsonify({"player_id": player.player_id, "username": player.username})


@app.route("/api/username", methods=["POST"])
def change_username():
    data = request.get_json(silent=True) or {}
    if not data.get("player_id") or not data.get("username"):
        return ("Missing fields", 400)
    with STATE.lock:
        player = _touch_player_locked(data["player_id"])
        player.username = _sanitize_username(data["username"])
        return jsonify({"ok": True, "username": player.username, "leaderboard": _public_leaderboard()})


@app.route("/api/newgame", methods=["POST"])
def new_game():
    with STATE.lock:
        STATE.reset()
        STATE.active = True
        # Everyone currently on the page is in
        cutoff = time.time() - 10
        STATE.active_players = {p.player_id for p in STATE.players.values() if p.last_seen >= cutoff}
        _start_next_round_locked()
    return jsonify({"ok": True, "game_id": STATE.game_id})


@app.route("/api/endgame", methods=["POST"])
def end_game():
    """Stop the current game where it stands. Scores stay on the leaderboard
    until the next newgame."""
    with STATE.lock:
        STATE.active = False
        STATE.current_round = None
        STATE.next_printing_id = None
    return jsonify({"ok": True})


@app.route("/api/guess", methods=["POST"])
def guess():
    data = request.get_json(silent=True) or {}
    player_id = data.get("player_id")
    try:
        price = float(data.get("price"))
    except (TypeError, ValueError):
        return ("Invalid guess", 400)
    if not player_id or not math.isfinite(price) or price <= 0:
        return ("Invalid guess", 400)
    price = min(MAX_GUESS, price)
    with STATE.lock:
        _advance_if_needed_locked()
        rnd = STATE.current_round
        if not STATE.active or rnd is None:
            return jsonify({"accepted": False, "reason": "no_active_game"})
        if rnd.phase != "guessing" or _now() > rnd.ends_at:
            return jsonify({"accepted": False, "reason": "round_over"})
        _touch_player_locked(player_id)
        STATE.active_players.add(player_id)
        STATE.misses[player_id] = 0
        STATE.guesses[rnd.round_index][player_id] = price
        if data.get("lock_in"):
            STATE.ready[rnd.round_index].add(player_id)
            _finalize_round_early_if_all_ready_locked()
    return jsonify({"accepted": True})


@app.route("/api/join", methods=["POST"])
def join_game():
    data = request.get_json(silent=True) or {}
    if not data.get("player_id"):
        return ("Missing fields", 400)
    with STATE.lock:
        _touch_player_locked(data["player_id"])
        STATE.active_players.add(data["player_id"])
        STATE.misses[data["player_id"]] = 0
    return jsonify({"ok": True})


@app.route("/api/state")
def get_state():
    with STATE.lock:
        _touch_player_locked(request.args.get("player_id"))
        _advance_if_needed_locked()
        rnd = STATE.current_round
        game = {
            "active": STATE.active,
            "game_id": STATE.game_id,
            "round_number": STATE.current_round_index,
            "total_rounds": STATE.total_rounds,
            "turn_duration_seconds": TURN_DURATION_SECONDS if rnd and rnd.phase == "guessing" else REVEAL_SECONDS,
            "phase": rnd.phase if rnd else None,
            "turn_ends_at_ms": None,
            "card": None,
            "reveal": None,
            "ready": None,
            "preload": [],
        }
        if rnd:
            revealed = rnd.phase == "reveal"
            game["card"] = card_payload(rnd.printing_id, reveal=revealed)
            game["turn_ends_at_ms"] = _ms(rnd.reveal_ends_at if revealed else rnd.ends_at)
            if revealed:
                game["reveal"] = {
                    "true_price": rnd.true_price,
                    "results": STATE.results.get(rnd.round_index, {}),
                }
            else:
                ready_set = STATE.ready.get(rnd.round_index, set())
                game["ready"] = {
                    "count": len(STATE.active_players & ready_set),
                    "total": len(STATE.active_players),
                    "ready_player_ids": sorted(ready_set),
                }
            if STATE.next_printing_id:
                game["preload"] = [DATA["printings"][STATE.next_printing_id]["img"]]
        return jsonify({
            "server_time_ms": _ms(_now()),
            "game": game,
            "leaderboard": _public_leaderboard(),
            "price_floor": PRICE_FLOOR,
            "data_date": (DATA.get("scryfall_updated_at") or "")[:10],
        })


def _sanitize_username(name: str) -> str:
    name = (name or "").strip()
    return name[:24] if name else _generate_username()


def _generate_username() -> str:
    adjectives = ["Foil", "Mythic", "Tapped", "Untapped", "Shiny", "Graded", "Misprinted", "Sleeved",
                  "Mint", "Played", "Reserved", "Bulk"]
    nouns = ["Goblin", "Elf", "Wurm", "Sliver", "Angel", "Dragon", "Squirrel", "Lotus", "Mox",
             "Merfolk", "Zombie", "Thopter"]
    return f"{random.choice(adjectives)}{random.choice(nouns)}{random.randint(10, 99)}"


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1", threaded=True)
