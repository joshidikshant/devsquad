// Signal over Noise — "The Instrument" · SCENE 1: "I'LL JUST DO IT MYSELF." (f0-72)
// The left meme panel, alive. Claude robot (stressed) at a desk while flames grow
// from the desk corners over the scene, warning chips flicker in, a THIS IS FINE
// mug sits on the desk, and the bottom caption lands. Peak chaos by f72.
//
// Remotion craft: pure function of the LOCAL `frame` prop (NOT useCurrentFrame,
// so the scene is testable in isolation). All motion via interpolate with the
// brand ease + a couple of springs for snappy hits. Individual scale/translate/
// rotate props only — never composed transform strings. Typewriter via string
// slicing off the frame. No CSS transitions/animations.

import React from "react";
import { interpolate, spring, Easing } from "remotion";
import { T } from "./tokens";
import { RobotClaude, Flame, Chip, Mug } from "./assets";

const FPS = 30;

// Brand ease, reused everywhere so motion reads as one instrument.
const EASE = Easing.bezier(T.ease[0], T.ease[1], T.ease[2], T.ease[3]);
const brand = (
  frame: number,
  input: [number, number],
  output: [number, number]
) =>
  interpolate(frame, input, output, {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASE,
  });

// Typewriter: slice the string off the frame, never per-char opacity.
const typed = (text: string, frame: number, start: number, cps: number) => {
  const chars = Math.floor(Math.max(0, frame - start) * (cps / FPS));
  return text.slice(0, chars);
};

// Interpolate between two hex colors (phosphor -> amber for the fire warm-up).
const lerpHex = (a: string, b: string, t: number) => {
  const c = Math.max(0, Math.min(1, t));
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const mix = pa.map((v, i) => Math.round(v + (pb[i] - v) * c));
  return `#${mix.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
};

export const Scene1Fire: React.FC<{
  frame: number;
  durationInFrames: number;
  width: number;
  height: number;
}> = ({ frame, durationInFrames, width, height }) => {
  // Composition is 1280x640; everything positions relative to width/height.
  const cx = width / 2;
  const cy = height / 2;

  // -------------------------------------------------------------------------
  // Global fire progress 0->1 across the whole scene. Drives flame height,
  // color warm-up, robot dread, and the deadpan screen-tick at peak.
  // -------------------------------------------------------------------------
  const fire = brand(frame, [12, durationInFrames], [0, 1]);

  // A faint amber wash behind the desk that comes up with the fire.
  const washAlpha = brand(frame, [30, durationInFrames], [0, 0.14]);

  // Robot: settles in, then leans toward the screen when the bubble pops (f12),
  // then a low-frequency deadpan shudder as chaos peaks.
  const robotIn = spring({ frame, fps: FPS, config: { damping: 200 } });
  const robotScale = interpolate(robotIn, [0, 1], [0.92, 1]);
  const lean = brand(frame, [12, 30], [0, -6]); // translateY up (lean into screen)
  const shudder =
    fire > 0.55 ? Math.sin(frame * 1.9) * (fire - 0.55) * 4 : 0; // deadpan tremor
  const robotY = cy - 12 + lean + shudder;

  // Robot ink warms very slightly toward amber at peak chaos (engulfed), but
  // stays mostly phosphor — the robot is not itself a flame.
  const robotColor = lerpHex(T.accent, T.warn, Math.max(0, fire - 0.55) * 0.7);

  const robotSize = Math.min(width, height) * 0.62; // ~fills vertical action
  const robotW = (robotSize * 120) / 140;

  // -------------------------------------------------------------------------
  // Flames — several, staggered, growing from the desk corners inward. Each
  // grows scaleY 0->1 with transformOrigin bottom, flickers, warms amber.
  // -------------------------------------------------------------------------
  const deskY = cy + robotSize * 0.28; // baseline the flames sit on (desk line)
  const flameBaseW = Math.max(width, height) * 0.55; // spread envelope
  const flameSize = Math.min(width, height) * 0.34;
  const flameW = (flameSize * 40) / 64;

  // Positions across the desk: corners first (dread starts at edges), then
  // creeping toward center as the fire spreads.
  const flames = [
    { x: cx - flameBaseW * 0.5, delay: 12, h: 0.9, flick: 2.3 }, // far left corner
    { x: cx + flameBaseW * 0.5, delay: 12, h: 0.9, flick: 2.1 }, // far right corner
    { x: cx - flameBaseW * 0.28, delay: 26, h: 1.05, flick: 2.7 },
    { x: cx + flameBaseW * 0.28, delay: 26, h: 1.05, flick: 2.5 },
    { x: cx - flameBaseW * 0.08, delay: 44, h: 1.2, flick: 3.1 }, // center-left, latest + tallest
    { x: cx + flameBaseW * 0.08, delay: 44, h: 1.2, flick: 2.9 },
  ];

  // -------------------------------------------------------------------------
  // Chips — flicker in on their beats. USAGE LIMIT HIT / CONTEXT ROT at f36,
  // IGNORED CLAUDE.MD at f60. Flicker = deterministic on/off near the entrance,
  // then hold solid.
  // -------------------------------------------------------------------------
  const chipFlicker = (start: number) => {
    if (frame < start) return 0;
    const dt = frame - start;
    if (dt < 8) {
      // two quick blinks before settling (instrument warning waking up)
      const blink = Math.floor(dt / 2) % 2 === 0 ? 1 : 0.15;
      return blink;
    }
    return brand(frame, [start + 8, start + 12], [0.15, 1]);
  };
  const chipRise = (start: number) => brand(frame, [start, start + 10], [10, 0]);

  // -------------------------------------------------------------------------
  // Captions
  // -------------------------------------------------------------------------
  const topCaption = typed("CLAUDE.MD SAID DELEGATE.", frame, 0, 26);
  const topCaret = frame < 14 && Math.floor(frame / 4) % 2 === 0 ? "_" : "";

  // Speech bubble pops at f12 with a spring, then holds.
  const bubbleIn = spring({
    frame: frame - 12,
    fps: FPS,
    config: { damping: 14, stiffness: 180, mass: 0.7 },
  });
  const bubbleScale = interpolate(bubbleIn, [0, 1], [0.6, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const bubbleOpacity = brand(frame, [12, 18], [0, 1]);

  // Bottom caption tracks in at f52. "CODING WITH CLAUDE CODE" = 23 chars; at
  // 52 cps it finishes at ~f65 (frame ≥ 52 + 23/(52/30) ≈ 65.3), a safe ~6
  // frames before the f72 scene end. Guarantees it is fully typed + centered.
  const bottomText = typed("CODING WITH CLAUDE CODE", frame, 52, 52);
  const bottomOpacity = brand(frame, [52, 58], [0, 1]);

  // Peak-chaos screen tick: a tiny whole-scene jolt right at rock bottom (f66+).
  const tick = frame >= 66 ? Math.sin((frame - 66) * 3.3) * 1.4 : 0;

  const monoStack = `${T.fontMono}, monospace`;

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        width,
        height,
        background: T.bg,
        overflow: "hidden",
        translate: `0px ${tick}px`,
      }}
    >
      {/* amber redline wash behind the desk as the fire builds */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: height * 0.55,
          background: `linear-gradient(to top, ${T.warn}, transparent)`,
          opacity: washAlpha,
          pointerEvents: "none",
        }}
      />

      {/* ------------------------------------------------------------------ */}
      {/* Top caption — types in */}
      {/* ------------------------------------------------------------------ */}
      <div
        style={{
          position: "absolute",
          top: height * 0.09,
          left: 0,
          right: 0,
          textAlign: "center",
          fontFamily: monoStack,
          fontSize: Math.round(height * 0.045),
          letterSpacing: "0.14em",
          textTransform: "uppercase",
          color: T.fg,
          whiteSpace: "pre",
        }}
      >
        {topCaption}
        <span style={{ color: T.accent }}>{topCaret}</span>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* The robot (stressed), centered on the desk */}
      {/* ------------------------------------------------------------------ */}
      <div
        style={{
          position: "absolute",
          left: cx - robotW / 2,
          top: robotY - robotSize / 2,
          width: robotW,
          height: robotSize,
          scale: String(robotScale),
        }}
      >
        <RobotClaude
          variant="stressed"
          size={robotW}
          style={{ color: robotColor }}
        />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Flames — staggered, growing from desk corners inward */}
      {/* ------------------------------------------------------------------ */}
      {flames.map((f, i) => {
        // per-flame growth 0->1, delayed, eased
        const grow = brand(frame, [f.delay, durationInFrames], [0, 1]);
        // flicker on the height so tongues never sit dead-still
        const flick = 1 + Math.sin(frame * f.flick + i) * 0.06 * grow;
        const scaleY = grow * f.h * flick;
        // color warms phosphor -> amber as this tongue grows
        const col = lerpHex(T.accent, T.warn, grow);
        const opacity = brand(frame, [f.delay, f.delay + 6], [0, 1]);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: f.x - flameW / 2,
              top: deskY - flameSize,
              width: flameW,
              height: flameSize,
              transformOrigin: "bottom center",
              scale: `1 ${scaleY}`,
              opacity,
            }}
          >
            <Flame size={flameW} style={{ color: col }} />
          </div>
        );
      })}

      {/* ------------------------------------------------------------------ */}
      {/* THIS IS FINE mug — sits on the desk to the robot's side.           */}
      {/* Enlarged and pushed further right + docked flat on the desk line   */}
      {/* so it clears the center-right flame tongue and its label is legible.*/}
      {/* ------------------------------------------------------------------ */}
      {(() => {
        const mugSize = Math.min(width, height) * 0.185;
        const mugW = (mugSize * 60) / 64;
        const mugIn = brand(frame, [20, 30], [0, 1]);
        return (
          <div
            style={{
              position: "absolute",
              left: cx + robotW * 0.66,
              top: deskY - mugSize,
              width: mugW,
              height: mugSize,
              opacity: mugIn,
              scale: String(interpolate(mugIn, [0, 1], [0.8, 1])),
              transformOrigin: "bottom center",
            }}
          >
            <Mug size={mugW} style={{ color: T.accent }} />
          </div>
        );
      })()}

      {/* ------------------------------------------------------------------ */}
      {/* Warning chips — flicker in on their beats */}
      {/* ------------------------------------------------------------------ */}
      {/* USAGE LIMIT HIT — upper-left, f36 */}
      <div
        style={{
          position: "absolute",
          left: width * 0.06,
          top: height * 0.3,
          opacity: chipFlicker(36),
          translate: `0px ${chipRise(36)}px`,
          fontSize: Math.round(height * 0.028),
        }}
      >
        <Chip style={{ fontSize: Math.round(height * 0.028) }}>USAGE LIMIT HIT</Chip>
      </div>

      {/* CONTEXT ROT — upper-right, f36 (right-anchored so it never clips).  */}
      {/* Extra right margin + explicit right column so the (now narrower)    */}
      {/* speech bubble can no longer paint over its leading edge.            */}
      <div
        style={{
          position: "absolute",
          right: width * 0.04,
          top: height * 0.24,
          display: "flex",
          justifyContent: "flex-end",
          opacity: chipFlicker(38),
          translate: `0px ${chipRise(38)}px`,
        }}
      >
        <Chip style={{ fontSize: Math.round(height * 0.028) }}>CONTEXT ROT</Chip>
      </div>

      {/* IGNORED CLAUDE.MD — lower-right, f60 (rock bottom) */}
      <div
        style={{
          position: "absolute",
          right: width * 0.04,
          top: height * 0.6,
          display: "flex",
          justifyContent: "flex-end",
          opacity: chipFlicker(60),
          translate: `0px ${chipRise(60)}px`,
        }}
      >
        <Chip style={{ fontSize: Math.round(height * 0.028) }}>IGNORED CLAUDE.MD</Chip>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Speech bubble — "I'LL JUST DO IT MYSELF." — pops at f12.            */}
      {/* Narrowed + wrapped to two lines and pulled left of the robot's head */}
      {/* so it stays inside the center column and no longer collides with    */}
      {/* the CONTEXT ROT chip on the right.                                  */}
      {/* ------------------------------------------------------------------ */}
      <div
        style={{
          position: "absolute",
          left: cx - robotW * 0.28,
          top: robotY - robotSize * 0.5,
          opacity: bubbleOpacity,
          scale: String(bubbleScale),
          transformOrigin: "bottom left",
        }}
      >
        <div
          style={{
            position: "relative",
            border: `1px solid ${T.accent}`,
            borderRadius: 2,
            background: T.bg,
            color: T.accent,
            fontFamily: monoStack,
            fontSize: Math.round(height * 0.036),
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            padding: "0.5em 0.7em",
            // Constrain width so the phrase wraps to two lines instead of
            // running out into the right column and crowding the chip.
            maxWidth: Math.round(width * 0.26),
            textAlign: "center",
            lineHeight: 1.25,
          }}
        >
          "I'LL JUST DO IT MYSELF."
          {/* tail — a small hairline notch pointing down-left toward the robot */}
          <span
            style={{
              position: "absolute",
              left: 18,
              bottom: -8,
              width: 14,
              height: 14,
              borderLeft: `1px solid ${T.accent}`,
              borderBottom: `1px solid ${T.accent}`,
              background: T.bg,
              rotate: "-45deg",
            }}
          />
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Bottom caption — CODING WITH CLAUDE CODE */}
      {/* ------------------------------------------------------------------ */}
      <div
        style={{
          position: "absolute",
          bottom: height * 0.07,
          left: 0,
          right: 0,
          textAlign: "center",
          fontFamily: monoStack,
          fontSize: Math.round(height * 0.05),
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          fontWeight: 700,
          color: T.fg,
          opacity: bottomOpacity,
          whiteSpace: "pre",
        }}
      >
        {bottomText}
      </div>
    </div>
  );
};
