// Signal over Noise — "The Instrument" · SCENE 4: THE SCOREBOARD (f0–48 local).
// Job: land the punchline + the brand. Smug, earned. Mostly DOM typography over
// a faintly-held calm EM tableau, with the loop-seam amber flicker at the very
// end (f43–48 IS the beginning of Scene 1's ignition — no pop at f48≡f0).
//
// Pure function of the `frame` prop (0-based, LOCAL to this scene). No
// useCurrentFrame — self-contained + testable in isolation. All motion via
// interpolate/spring on INDIVIDUAL scale/translate props (never composed
// transform strings). Typewriter = string slicing off frame.

import React from "react";
import { interpolate, spring, Easing } from "remotion";
import { T, FPS } from "./tokens";
import { RobotClaude, RobotAgent } from "./assets";

const EASE = Easing.bezier(T.ease[0], T.ease[1], T.ease[2], T.ease[3]);

// Brand-eased interpolate, clamped both ends — the scene's default motion.
const ease = (
  frame: number,
  input: [number, number],
  output: [number, number]
): number =>
  interpolate(frame, input, output, {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASE,
  });

// Typewriter: reveal `text` by slicing to a char count that ramps linearly over
// [start, end]. Never per-char opacity.
const typed = (text: string, frame: number, start: number, end: number): string => {
  const chars = Math.round(ease(frame, [start, end], [0, text.length]));
  return text.slice(0, chars);
};

export const Scene4Score: React.FC<{
  frame: number;
  durationInFrames: number;
  width: number;
  height: number;
}> = ({ frame, durationInFrames, width, height }) => {
  // --- Layout anchors, all relative to the composition box -------------------
  const cx = width / 2;
  const cy = height / 2;

  // --- Beat map (local frames @30fps) ----------------------------------------
  // f0–18  scoreboard caption tracks in (top) + one phosphor pulse across squad
  // f18–36 DEVSQUAD wordmark scales in (bottom, bold)
  // f36–43 subline types HOOKS // DON'T IGNORE YOU
  // f43–48 faint amber flicker returns at the desk corner (loop seam → Scene 1)

  // --- Faint held EM tableau (background) ------------------------------------
  // The calm right-panel composition, holding dim in the back. A single phosphor
  // pulse (f0–18) breathes brightness across the squad, then settles.
  const tableauBase = 0.16; // dim — this is backdrop, not subject
  const pulse = ease(frame, [0, 9], [0, 1]) * ease(frame, [9, 18], [1, 0]);
  const tableauOpacity = tableauBase + pulse * 0.14;

  const robotScale = width / 1280; // tableau sized to the 1280 reference
  const claudeSize = 150 * robotScale;
  const agentSize = 96 * robotScale;

  // --- Scoreboard caption (top) — tracks in: letter-spacing opens + fades up --
  const capOpacity = ease(frame, [0, 12], [0, 1]);
  const capTrackPx = ease(frame, [0, 18], [1, 7]); // letter-spacing opens
  const capTranslateY = ease(frame, [0, 14], [-10, 0]);

  // --- DEVSQUAD wordmark (bottom, bold) — snappy scale-in via spring ----------
  const wordSpring = spring({
    frame: frame - 18,
    fps: FPS,
    config: { damping: 200, mass: 0.9, stiffness: 140 },
    durationInFrames: 18,
  });
  const wordScale = interpolate(wordSpring, [0, 1], [0.82, 1]);
  const wordOpacity = ease(frame, [18, 26], [0, 1]);

  // --- Subline (typewriter) --------------------------------------------------
  const sublinePhosphor = typed("HOOKS //", frame, 36, 40);
  const sublineRest = typed(" DON'T IGNORE YOU", frame, 40, 43);
  // caret blinks only while typing is live (f36–43), on a deterministic frame
  // parity so it's a pure function of frame.
  const typingLive = frame >= 36 && frame < 43;
  const caretOn = typingLive && Math.floor(frame / 4) % 2 === 0;

  // --- Loop-seam amber flicker (desk corner) ---------------------------------
  // The faintest amber returns at f43–48. This IS Scene 1's first ember. Two
  // deterministic ticks so it reads as a flicker, not a fade.
  const flickerWindow = ease(frame, [43, 45], [0, 1]);
  const flickerTick = frame >= 43 ? (Math.floor(frame / 2) % 2 === 0 ? 1 : 0.35) : 0;
  const emberOpacity = flickerWindow * flickerTick * 0.5;

  const scoreCaption = "DEVSQUAD 1 | CLAUDE.MD 0";

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        width,
        height,
        background: T.bg,
        overflow: "hidden",
        fontFamily: `${T.fontMono}, monospace`,
      }}
    >
      {/* ---------------------------------------------------------------- */}
      {/* Faint held EM tableau — calm Claude + squad, dim in the back.     */}
      {/* ---------------------------------------------------------------- */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width,
          height,
          opacity: tableauOpacity,
        }}
      >
        {/* Claude (EM) centered, seated */}
        <div
          style={{
            position: "absolute",
            left: cx,
            top: cy,
            translate: `-50% -46%`,
          }}
        >
          <RobotClaude variant="em" size={claudeSize} />
        </div>

        {/* Three teammates arrayed behind/around — READ / DRAFT / CHECK */}
        <div
          style={{
            position: "absolute",
            left: cx,
            top: cy,
            translate: `${-320 * robotScale}px ${-30 * robotScale}px`,
          }}
        >
          <RobotAgent label="GEMINI" size={agentSize} />
        </div>
        <div
          style={{
            position: "absolute",
            left: cx,
            top: cy,
            translate: `${230 * robotScale}px ${-56 * robotScale}px`,
          }}
        >
          <RobotAgent label="CODEX" size={agentSize} />
        </div>
        <div
          style={{
            position: "absolute",
            left: cx,
            top: cy,
            translate: `${290 * robotScale}px ${20 * robotScale}px`,
          }}
        >
          <RobotAgent label="GROK" size={agentSize} />
        </div>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* Scoreboard caption (top) — DEVSQUAD 1 | CLAUDE.MD 0               */}
      {/* ---------------------------------------------------------------- */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: height * 0.14,
          width,
          textAlign: "center",
          opacity: capOpacity,
          translate: `0 ${capTranslateY}px`,
        }}
      >
        <span
          style={{
            display: "inline-block",
            color: T.fgMuted,
            fontSize: Math.round(height * 0.038),
            fontWeight: 500,
            letterSpacing: `${capTrackPx}px`,
            textTransform: "uppercase",
            whiteSpace: "nowrap",
          }}
        >
          {scoreCaption}
        </span>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* DEVSQUAD wordmark (bottom, bold, letter-spaced)                  */}
      {/* ---------------------------------------------------------------- */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: height * 0.66,
          width,
          textAlign: "center",
          opacity: wordOpacity,
          scale: `${wordScale}`,
        }}
      >
        <span
          style={{
            display: "inline-block",
            color: T.fg,
            fontSize: Math.round(height * 0.13),
            fontWeight: 700,
            letterSpacing: `${Math.round(height * 0.02)}px`,
            textTransform: "uppercase",
            whiteSpace: "nowrap",
            // pull the trailing letter-spacing off the right edge so the
            // block stays optically centered.
            paddingLeft: Math.round(height * 0.02),
          }}
        >
          DEVSQUAD
        </span>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* Subline (typewriter) — HOOKS // DON'T IGNORE YOU                  */}
      {/* ---------------------------------------------------------------- */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: height * 0.86,
          width,
          textAlign: "center",
        }}
      >
        <span
          style={{
            display: "inline-block",
            fontSize: Math.round(height * 0.036),
            fontWeight: 500,
            letterSpacing: `${Math.round(height * 0.006)}px`,
            textTransform: "uppercase",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ color: T.accent }}>{sublinePhosphor}</span>
          <span style={{ color: T.fgMuted }}>{sublineRest}</span>
          {/* typing caret — a phosphor block while live */}
          <span
            aria-hidden
            style={{
              display: "inline-block",
              width: "0.55em",
              height: "1em",
              marginLeft: "0.1em",
              verticalAlign: "-0.12em",
              background: T.accent,
              opacity: caretOn ? 1 : 0,
            }}
          />
        </span>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* Loop-seam amber ember — faint flicker at the desk corner.         */}
      {/* Positioned at the left desk edge of the centered Claude tableau.  */}
      {/* This is the first frame of Scene 1's fire, handed back at f48≡f0. */}
      {/* ---------------------------------------------------------------- */}
      <div
        style={{
          position: "absolute",
          left: cx,
          top: cy,
          translate: `${-70 * robotScale}px ${52 * robotScale}px`,
          opacity: emberOpacity,
        }}
      >
        <div
          style={{
            width: Math.round(6 * robotScale + 3),
            height: Math.round(6 * robotScale + 3),
            background: T.warn,
            borderRadius: 1,
          }}
        />
      </div>
    </div>
  );
};
