# DevSquad Hero — Storyboard ("This Is Fine → EM Mode")

The animated version of the LinkedIn meme that landed. A ~7s (210f @ 30fps)
loop. Two-panel meme story — **left: Claude Code alone, on fire; right: calm
"EM" Claude with a working squad** — but rendered in the Signal-over-Noise
system (Night Bench #0A0D0C, phosphor #00E5A0, amber #F5A623 for fire/redline
only, Geist Mono uppercase). Same jokes, on-brand, and it MOVES.

Meme fidelity: keep the exact caption energy — *"CLAUDE.md said delegate." /
"I'll just do it myself." / Usage Limit Hit / Context Rot / Ignored CLAUDE.md*
→ flip to *"DevSquad 1 | CLAUDE.md 0"* with Gemini/Codex/Grok working and a
calm EM badge. Voice is dry, relatable, a little smug at the end.

Robots are clean vector (phosphor line-art on black), NOT sketchy clip-art.
Flames are stylized phosphor→amber vector tongues, not cartoon fire.

---

## The joke (why it converts)

Every dev has BEEN the left panel: told the agent to delegate, watched it do
everything itself, hit the usage limit, context rotted. The laugh is
recognition. The turn ("hooks don't ignore you") + the calm right panel is the
product promise delivered as a punchline. It already worked as a static meme;
motion makes the *transformation* the hero.

---

## SCENE 1 — "I'LL JUST DO IT MYSELF." (0.0–2.4s · f0–72)

**Job:** recognition + rising dread. This is the left meme panel, alive.

| Frame | Copy on screen | Visual |
|---|---|---|
| 0–12 | top caption types in: `CLAUDE.MD SAID DELEGATE.` | A single Claude robot at a desk, calm, phosphor line-art. Small `CLAUDE CODE` on its chest. |
| 12–36 | speech bubble pops: `"I'LL JUST DO IT MYSELF."` | Robot leans into the screen. First flame tongues (amber) start licking up from the desk corners. |
| 36–60 | two warning chips flicker in: `USAGE LIMIT HIT` · `CONTEXT ROT` (amber, boxed, instrument-style) | Fire spreads — flames rise around the robot, growing frame by frame. Robot's eyes go flat/annoyed. A coffee mug reads `THIS IS FINE`. |
| 60–72 | bottom caption: `CODING WITH CLAUDE CODE` ; a third chip: `IGNORED CLAUDE.MD` | Peak chaos — robot engulfed in phosphor-amber flame, fully on fire, deadpan. This is rock bottom. Hold 1 beat. |

Copy = the meme verbatim. The `THIS IS FINE` mug is the easter egg.

---

## SCENE 2 — THE INTERVENTION (2.4–3.4s · f72–102)

**Job:** the turn. A hook drops in and snuffs the fire. Satisfying.

| Frame | Copy | Visual |
|---|---|---|
| 72–78 | — | A **hook** (the ⌐ fishing-hook glyph from the meme's top) descends from the top of frame on a taut line, fast. |
| 78–84 | `⟩ HOOK INTERCEPT` snaps in briefly at center | The hook catches — a phosphor shock ripples out; the flames get YANKED downward/out in one snap (extinguished), amber → cools to black. Micro screen-tick. |
| 84–102 | wipe: a phosphor scanline sweeps left→right, and the scene transitions from the burning-left to the calm-right composition | The single robot straightens up, gains a small `EM` badge. The frame reorganizes: hub-and-squad forming. |

Copy: "HOOK" names the mechanism (it's the HOW). The payoff line waits.

---

## SCENE 3 — EM MODE (3.4–5.4s · f102–162)

**Job:** the flex. Calm competence. The right meme panel, alive.

| Frame | Copy | Visual |
|---|---|---|
| 102–162 | node labels type in as each teammate appears: `GEMINI` (holding a stack of files = READ) · `CODEX` (wrench = DRAFT) · `GROK` (magnifier = CHECK) | Calm `EM` Claude at the desk, relaxed posture, faint smile. Three teammate robots arranged behind/around, each doing its job with its icon. Small work-tokens flow from Claude → each teammate (dispatch) and distilled signal returns. Everything phosphor, cool, orderly. |

The teammate props (files / wrench / magnifier) map 1:1 to READ / DRAFT /
CHECK — instant "who does what."

---

## SCENE 4 — THE SCOREBOARD (5.4–7.0s · f162–210)

**Job:** land the punchline + the brand. Smug, earned.

| Frame | Copy | Visual |
|---|---|---|
| 162–180 | `DEVSQUAD 1 | CLAUDE.MD 0` scoreboard caption tracks in (top) | The calm EM tableau holds; a single phosphor pulse across the squad. |
| 180–198 | `DEVSQUAD` wordmark scales in (bottom, bold) | — |
| 198–205 | subline types: `HOOKS //` (phosphor) ` DON'T IGNORE YOU` | The headline delivered. |
| 205–210 | hold; the faintest amber flicker returns at the desk corner | Seamless hand-back to Scene 1 (f210 ≡ f0): the calm is about to become the next "I'll just do it myself." |

Two captions do the work: `DEVSQUAD 1 | CLAUDE.MD 0` (the meme's scoreline) and
`HOOKS // DON'T IGNORE YOU` (the README headline).

---

## Copy inventory (every word on screen)

- `CLAUDE.MD SAID DELEGATE.` (opening caption)
- `"I'LL JUST DO IT MYSELF."` (speech bubble)
- `USAGE LIMIT HIT` · `CONTEXT ROT` · `IGNORED CLAUDE.MD` (fire chips)
- `THIS IS FINE` (mug easter egg)
- `CODING WITH CLAUDE CODE` (scene-1 bottom caption)
- `⟩ HOOK INTERCEPT` (the turn)
- `GEMINI` · `CODEX` · `GROK` (+ icons: files / wrench / magnifier)
- `EM` (Claude's badge)
- `DEVSQUAD 1 | CLAUDE.MD 0` (scoreboard)
- `DEVSQUAD` (wordmark) · `HOOKS // DON'T IGNORE YOU` (payoff)

## Loop seam contract

f210 ≡ f0. Scene 4 ends on calm-with-a-flicker; Scene 1 opens on calm-igniting.
The flicker at f205–210 IS the f0 state beginning — no pop.

## Build notes (Remotion craft — official skill)

- `<Sequence from={} durationInFrames={} premountFor={fps}>` — one per scene.
- Robots + flames + props = clean inline SVG (vector, phosphor line-art),
  animated via `interpolate`/`spring` on individual `scale`/`translate`/`rotate`.
- Flames: SVG flame paths whose height/opacity interpolate up (spread) then get
  yanked to 0 at the hook (Scene 2). Amber→black.
- Captions/speech bubble/chips = DOM, Geist Mono, typewriter via string slicing
  (never per-char opacity).
- Particles (dispatch/return work-tokens in Scene 3) can stay @remotion/three
  or be simple SVG dots — the story is character-driven now, so SVG may read
  cleaner. Decide at build.
- Brand ease Easing.bezier(0.6,0,0.1,1); spring() only for the hook snap +
  wordmark. No CSS transitions. Deterministic (pure function of frame).
- 1280×640 landscape (README) + a 640×640 square variant (composition reuses
  the same scenes, recomposed for square).
