import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Built by build_dataset.py
DATA_PATH = os.getenv("DATA_PATH", os.path.join(HERE, "data", "cards.json.gz"))

# Game settings
TURN_DURATION_SECONDS = int(os.getenv("TURN_DURATION_SECONDS", "30"))
REVEAL_SECONDS = int(os.getenv("REVEAL_SECONDS", "5"))
TOTAL_ROUNDS = int(os.getenv("TOTAL_ROUNDS", "50"))

# Show the (heuristic) estimated print run next to each printing
SHOW_PRINT_RUNS = os.getenv("SHOW_PRINT_RUNS", "1") != "0"

