const { useEffect, useRef, useState } = React;
const h = React.createElement;

// Guess slider spans this range on a log scale
const SLIDER_MIN = 0.25;
const SLIDER_MAX = 50000;
const SLIDER_STEPS = 1000;

function msToClock(ms) {
  if (ms <= 0) return "0:00";
  const s = Math.ceil(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function formatPrice(p) {
  if (p == null || !isFinite(p)) return "—";
  if (p < 100) return `$${p.toFixed(2)}`;
  return `$${Math.round(p).toLocaleString("en-US")}`;
}

function formatCopies(n) {
  if (n == null) return "?";
  if (n >= 1e6) return `~${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `~${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return `~${n}`;
}

// Keep slider-produced prices to "nice" values: whole cents under $1, else
// 2 significant figures. toPrecision avoids float noise like 1.2000000000000002.
function niceRound(p) {
  if (p < 1) return Number(p.toFixed(2));
  return Number(p.toPrecision(2));
}

function sliderToPrice(v) {
  return niceRound(SLIDER_MIN * Math.pow(SLIDER_MAX / SLIDER_MIN, v / SLIDER_STEPS));
}

function priceToSlider(p) {
  const clamped = Math.min(SLIDER_MAX, Math.max(SLIDER_MIN, p));
  return Math.round((Math.log(clamped / SLIDER_MIN) / Math.log(SLIDER_MAX / SLIDER_MIN)) * SLIDER_STEPS);
}

function offByText(guess, truth, floor) {
  const g = Math.max(floor, guess);
  const t = Math.max(floor, truth);
  const ratio = g / t;
  if (Math.abs(ratio - 1) < 0.005) return "exact";
  return ratio > 1 ? `${ratio.toFixed(ratio < 10 ? 2 : 0)}× high` : `${(1 / ratio).toFixed(ratio > 0.1 ? 2 : 0)}× low`;
}

const FINISH_LABEL = { nonfoil: "Non-foil", foil: "Foil", etched: "Etched foil" };
const RARITY_LABEL = { common: "C", uncommon: "U", rare: "R", mythic: "M", special: "S", bonus: "B" };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error("request failed");
  return await res.json();
}

function useRegistration() {
  const [player, setPlayer] = useState(() => {
    try {
      const raw = localStorage.getItem("mtgpg_player");
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });

  useEffect(() => {
    // Always register on load: the server forgets players when it restarts
    api("/api/register", {
      method: "POST",
      body: JSON.stringify(player ? { player_id: player.player_id, desired_username: player.username } : {}),
    }).then((p) => {
      setPlayer(p);
      try { localStorage.setItem("mtgpg_player", JSON.stringify(p)); } catch {}
    }).catch(() => {});
  }, []);

  const updateUsername = async (username) => {
    if (!player?.player_id || !username.trim()) return;
    const res = await api("/api/username", { method: "POST", body: JSON.stringify({ player_id: player.player_id, username }) });
    const updated = { ...player, username: res.username };
    setPlayer(updated);
    try { localStorage.setItem("mtgpg_player", JSON.stringify(updated)); } catch {}
  };

  return { player, updateUsername };
}

function CircularTimer({ endsAtMs, totalDurationMs, nowMs }) {
  const deltaMs = endsAtMs ? Math.max(0, endsAtMs - nowMs) : 0;
  const progress = totalDurationMs > 0 ? Math.max(0, Math.min(100, ((totalDurationMs - deltaMs) / totalDurationMs) * 100)) : 0;
  const isUrgent = deltaMs <= 5000 && deltaMs > 0;
  return h("div", { className: "circular-timer" },
    h("div", { className: `timer-circle ${isUrgent ? "urgent" : ""}`, style: { "--progress": `${progress}%` } }),
    h("div", { className: "timer-inner" }, endsAtMs ? msToClock(deltaMs) : "--")
  );
}

function App() {
  const { player, updateUsername } = useRegistration();
  const [state, setState] = useState(null);
  const [clockOffset, setClockOffset] = useState(0); // server time - local time
  const [nowMs, setNowMs] = useState(Date.now());
  const keyBufferRef = useRef("");
  const playerIdRef = useRef(null);
  playerIdRef.current = player?.player_id || null;

  // Poll state every second
  useEffect(() => {
    let active = true;
    async function tick() {
      try {
        const pid = playerIdRef.current;
        const data = await api(`/api/state${pid ? `?player_id=${encodeURIComponent(pid)}` : ""}`);
        if (!active) return;
        setState(data);
        setClockOffset(data.server_time_ms - Date.now());
      } catch (e) {}
    }
    tick();
    const t = setInterval(tick, 1000);
    return () => { active = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 200);
    return () => clearInterval(t);
  }, []);

  // Join whenever a new game starts
  const gameId = state?.game?.game_id;
  useEffect(() => {
    if (!player?.player_id || !gameId) return;
    api("/api/join", { method: "POST", body: JSON.stringify({ player_id: player.player_id }) }).catch(() => {});
  }, [gameId, player?.player_id]);

  // Preload the next round's card image during the reveal
  const preload = state?.game?.preload || [];
  useEffect(() => {
    preload.forEach((src) => { const img = new Image(); img.src = src; });
  }, [preload.join("|")]);

  // Typed shortcuts: "newgame" / "endgame" anywhere outside an input
  useEffect(() => {
    function onKey(e) {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      keyBufferRef.current = (keyBufferRef.current + (e.key || "").toLowerCase()).slice(-7);
      if (keyBufferRef.current === "newgame") {
        api("/api/newgame", { method: "POST", body: "{}" }).catch(() => {});
        keyBufferRef.current = "";
      } else if (keyBufferRef.current === "endgame") {
        api("/api/endgame", { method: "POST", body: "{}" }).catch(() => {});
        keyBufferRef.current = "";
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const game = state?.game;
  const phase = game?.phase;
  const serverNow = nowMs + clockOffset;
  const card = game?.card;
  const floor = state?.price_floor ?? 0.25;

  const startGame = () => api("/api/newgame", { method: "POST", body: "{}" }).catch(() => {});

  return h("div", { className: "app-shell" },
    h("div", { className: "main-col" },
      h("div", { className: "panel header" },
        h("div", { className: "brand" }, "mtg price guessr"),
        h("div", { className: "round-info" },
          game?.round_number ? `Round ${game.round_number} / ${game.total_rounds}` : "",
          game && !game.active && game.round_number ? " · game over" : ""
        ),
        game?.active && h(CircularTimer, {
          endsAtMs: game.turn_ends_at_ms,
          totalDurationMs: (game.turn_duration_seconds || 0) * 1000,
          nowMs: serverNow,
        })
      ),
      card
        ? h(CardStage, { card, phase },
            h(GuessPanel, { key: `${game.game_id}-${game.round_number}`, game, player, state, floor, onStart: startGame }))
        : h("div", { className: "panel idle" },
            h("div", { className: "idle-title" }, "How much is this Magic card worth?"),
            h("p", null, "Each round shows one printing of a card. Guess its TCGplayer market price before the 30-second timer runs out. ",
              "Points depend on how close you are in ratio terms: exact is 100, 2× off is 70, 10× off is 0."),
            h("button", { className: "primary-btn", onClick: startGame }, "Start a new game")
          )
    ),
    h("div", { className: "side-col" },
      h("div", { className: "panel username-box" },
        h("label", null, "Your name"),
        h(UsernameEditor, { player, onSubmit: updateUsername })
      ),
      h(Leaderboard, { rows: state?.leaderboard || [], me: player?.player_id, results: game?.reveal?.results }),
      h("div", { className: "footnote" },
        `Prices: TCGplayer market via Scryfall${state?.data_date ? `, as of ${state.data_date}` : ""}. `,
        `Anything under ${formatPrice(floor)} counts as ${formatPrice(floor)}. `,
        "Print runs are rough estimates. Type \"newgame\" or \"endgame\" to control the game."
      )
    )
  );
}

function CardStage({ card, phase, children }) {
  const [showBack, setShowBack] = useState(false);
  const [hover, setHover] = useState(null); // {img, x, y}
  const p = card.printing;
  useEffect(() => setShowBack(false), [p.id]);
  const revealed = phase === "reveal";

  return h("div", { className: "panel card-stage" },
    h("div", { className: "card-main" },
      h("div", { className: "card-image-wrap" },
        h("img", {
          className: "card-image",
          src: showBack && p.img_back ? p.img_back : p.img,
          alt: card.name,
          onClick: () => p.img_back && setShowBack((v) => !v),
          style: { cursor: p.img_back ? "pointer" : "default" },
        }),
        p.img_back && h("div", { className: "flip-hint" }, "click to flip")
      ),
      h("div", { className: "card-details" },
        h("div", { className: "card-name" }, card.name),
        h("div", { className: "set-line" },
          p.set_icon && h("img", { className: "set-icon big", src: p.set_icon, alt: "" }),
          h("span", null, p.set_name),
          h("span", { className: "muted" }, ` (${p.set.toUpperCase()} #${p.cn})`)
        ),
        h("div", { className: "facts" },
          h(Fact, { label: "Year", value: p.year }),
          h(Fact, { label: "Rarity", value: p.rarity }),
          h(Fact, { label: "Est. copies printed", value: p.copies == null ? null : formatCopies(p.copies) }),
          h(Fact, { label: "Available as", value: p.finishes.map((f) => FINISH_LABEL[f] || f).join(", ") })
        ),
        h("div", { className: `finish-badge ${card.finish}` }, `Pricing the ${(FINISH_LABEL[card.finish] || card.finish).toLowerCase()} version`),
        children
      )
    ),
    h("div", { className: "others" },
      h("div", { className: "others-title" },
        card.others.length ? `Other printings (${card.others.length}), oldest first` : "No other printings"
      ),
      card.others.length > 0 && h("div", { className: "others-list" },
        h("div", { className: "other-row header-row" },
          h("span", null, "Year"), h("span", null, "Set"), h("span", null, "Rar."),
          h("span", null, "Finishes"), h("span", { className: "num" }, "Est. copies"),
          revealed && h("span", { className: "num" }, "Price")
        ),
        card.others.map((o) => h("div", {
          key: o.id,
          className: `other-row ${revealed ? "with-price" : ""}`,
          onMouseEnter: (e) => setHover({ img: o.img, x: e.clientX, y: e.clientY }),
          onMouseMove: (e) => setHover({ img: o.img, x: e.clientX, y: e.clientY }),
          onMouseLeave: () => setHover(null),
        },
          h("span", null, o.year),
          h("span", { className: "set-cell" },
            o.set_icon && h("img", { className: "set-icon", src: o.set_icon, alt: "" }),
            h("span", null, o.set_name)
          ),
          h("span", { className: `rarity ${o.rarity}` }, RARITY_LABEL[o.rarity] || "?"),
          h("span", { className: "muted" }, o.finishes.map((f) => (f === "nonfoil" ? "NF" : f === "foil" ? "F" : "E")).join("/")),
          h("span", { className: "num" }, formatCopies(o.copies)),
          revealed && h("span", { className: "num" },
            o.price == null ? "—" : `${formatPrice(o.price)}${o.price_finish !== "nonfoil" ? ` (${o.price_finish === "foil" ? "F" : "E"})` : ""}`)
        ))
      )
    ),
    hover && hover.img && h("img", {
      className: "hover-preview",
      src: hover.img,
      alt: "",
      style: {
        left: Math.min(hover.x + 16, window.innerWidth - 240),
        top: Math.max(8, Math.min(hover.y - 160, window.innerHeight - 340)),
      },
    })
  );
}

function Fact({ label, value }) {
  if (value == null || value === "") return null;
  return h("div", { className: "fact" },
    h("div", { className: "fact-label" }, label),
    h("div", { className: "fact-value" }, value)
  );
}

function GuessPanel({ game, player, state, floor, onStart }) {
  const [text, setText] = useState("");
  const [sent, setSent] = useState(null);
  const [lockedIn, setLockedIn] = useState(false);
  const sendTimer = useRef(null);
  const guessing = game.active && game.phase === "guessing";

  const parsed = parseFloat(text.replace(/[$,\s]/g, ""));
  const valid = isFinite(parsed) && parsed > 0;

  async function send(price, lockIn) {
    if (!player?.player_id) return;
    try {
      const res = await api("/api/guess", {
        method: "POST",
        body: JSON.stringify({ player_id: player.player_id, price, lock_in: !!lockIn }),
      });
      if (res.accepted) {
        setSent(price);
        if (lockIn) setLockedIn(true);
      }
    } catch (e) {}
  }

  // Latest value counts: send shortly after typing stops
  function onText(v) {
    setText(v);
    setLockedIn(false);
    clearTimeout(sendTimer.current);
    const p = parseFloat(v.replace(/[$,\s]/g, ""));
    if (isFinite(p) && p > 0) sendTimer.current = setTimeout(() => send(p, false), 300);
  }

  function onSlider(e) {
    const p = sliderToPrice(parseInt(e.target.value, 10));
    onText(String(p));
  }

  function lockIn() {
    if (!valid) return;
    clearTimeout(sendTimer.current);
    send(parsed, true);
  }

  useEffect(() => () => clearTimeout(sendTimer.current), []);

  if (game.phase === "reveal" && game.reveal) {
    return h(RevealPanel, { game, state, player, floor, onStart });
  }

  const ready = game.ready;
  return h("div", { className: "guess-panel" },
    h("div", { className: "guess-row" },
      h("div", { className: "price-input" },
        h("span", { className: "dollar" }, "$"),
        h("input", {
          type: "text",
          inputMode: "decimal",
          placeholder: "your guess",
          value: text,
          disabled: !guessing,
          autoFocus: true,
          onChange: (e) => onText(e.target.value),
          onKeyDown: (e) => { if (e.key === "Enter") lockIn(); },
        })
      ),
      h("button", { className: "primary-btn", onClick: lockIn, disabled: !guessing || !valid || lockedIn },
        lockedIn ? "Locked in ✔" : "Lock in"),
      ready && h("span", { className: "muted ready-count" }, `${ready.count}/${ready.total} locked in`)
    ),
    h("input", {
      type: "range",
      className: "price-slider",
      min: 0,
      max: SLIDER_STEPS,
      value: valid ? priceToSlider(parsed) : 0,
      disabled: !guessing,
      onChange: onSlider,
    }),
    h("div", { className: "slider-ticks" },
      [0.25, 1, 10, 100, 1000, 10000, 50000].map((p) => h("span", {
        key: p,
        style: { left: `${(priceToSlider(p) / SLIDER_STEPS) * 100}%` },
      }, p < 1 ? "25¢" : p >= 1000 ? `$${p / 1000}k` : `$${p}`))
    ),
    h("div", { className: "muted guess-status" },
      sent != null ? `Your guess: ${formatPrice(sent)}${sent < floor ? ` (scores as ${formatPrice(floor)})` : ""}` : "Type a price or drag the slider. Your latest guess counts."
    )
  );
}

function RevealPanel({ game, state, player, floor, onStart }) {
  const { true_price: truth, results } = game.reveal;
  const names = Object.fromEntries((state.leaderboard || []).map((r) => [r.player_id, r.username]));
  const rows = Object.entries(results).map(([pid, r]) => ({ pid, name: names[pid] || "?", ...r }));
  rows.sort((a, b) => b.points - a.points || Math.abs(Math.log(a.guess / truth)) - Math.abs(Math.log(b.guess / truth)));
  const gameOver = !game.active;

  const scryfall = game.card?.printing?.scryfall;
  return h("div", { className: "reveal-panel" },
    h("div", { className: "reveal-head" },
      h("span", { className: "muted" }, "Actual price"),
      h("span", { className: "true-price-value" }, formatPrice(truth)),
      scryfall && h("a", { href: scryfall, target: "_blank", rel: "noreferrer" }, "Scryfall ↗")
    ),
    rows.length === 0
      ? h("div", { className: "muted" }, "Nobody guessed this one.")
      : h("ol", { className: "reveal-list" },
          rows.map((r, i) => h("li", { key: r.pid, className: r.pid === player?.player_id ? "me" : "" },
            h("span", { className: "muted" }, `${i + 1}.`),
            h("span", { className: "r-name" }, r.name),
            h("span", { className: "r-guess" }, formatPrice(r.guess)),
            h("span", { className: "muted r-off" }, offByText(r.guess, truth, floor)),
            h("span", { className: "r-points" }, `+${r.points}`)
          ))
        ),
    gameOver && h("div", { className: "game-over" },
      h("span", null, "Game over!"),
      h("button", { className: "primary-btn", onClick: onStart }, "Play again")
    )
  );
}

function Leaderboard({ rows, me, results }) {
  return h("div", { className: "panel leaderboard" },
    h("h3", null, "Leaderboard"),
    rows.length === 0 && h("div", { className: "muted" }, "No one here yet."),
    rows.map((row, i) => h("div", { key: row.player_id, className: `lb-row ${row.player_id === me ? "me" : ""}` },
      h("span", { className: "lb-rank" }, i + 1),
      h("span", { className: "lb-name" }, row.username),
      results && results[row.player_id] && h("span", { className: "lb-bump" }, `+${results[row.player_id].points}`),
      h("span", { className: "lb-score" }, row.score)
    ))
  );
}

function UsernameEditor({ player, onSubmit }) {
  const [value, setValue] = useState(player?.username || "");
  useEffect(() => { setValue(player?.username || ""); }, [player?.username]);
  const submit = () => { if (value !== player?.username) onSubmit(value); };
  return h("div", { className: "username-row" },
    h("input", {
      value,
      maxLength: 24,
      onChange: (e) => setValue(e.target.value),
      onBlur: submit,
      onKeyDown: (e) => { if (e.key === "Enter") e.target.blur(); },
    }),
    h("button", { onClick: submit }, "Save")
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(h(App));
