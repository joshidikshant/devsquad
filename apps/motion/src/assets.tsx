// Signal over Noise — "The Instrument" · SHARED SVG asset kit for the DevSquad
// animated-meme hero. STATIC building blocks only: pure vector, no animation
// inside. Scenes animate them from the outside via individual `scale` /
// `translate` / `rotate` / `opacity` (Studio-editable, never composed transform
// strings). Every stroke resolves to `currentColor` or a T token so a scene can
// recolor by setting `color` (or overriding `stroke`) on the wrapper.
//
// Design law (Night Bench): phosphor line-art on black, 1px hairlines, sharp
// corners <=2px, NO shadows/glows-as-decoration. Amber (T.warn) is the sanctioned
// instrument-redline state ONLY (fire). Robots are instrument SCHEMATICS —
// simple + iconic, each under ~40 SVG elements — not cartoons or clip-art.

import React from "react";
import { T } from "./tokens";

// ---------------------------------------------------------------------------
// Shared types + helpers
// ---------------------------------------------------------------------------

// A scene may pass state to tint a static asset without re-authoring it.
export type AssetState = "calm" | "stressed" | "fire" | "cool";

type SvgProps = React.SVGProps<SVGSVGElement>;

interface AssetProps extends Omit<SvgProps, "children"> {
  /** Square render size in px. Aspect ratio is fixed by each viewBox. */
  size?: number;
  /** Optional semantic state; individual assets read what they need. */
  state?: AssetState;
}

// Baseline stroke geometry — one place so every asset stays a 1px hairline that
// scales crisply and never rounds a corner past 2px.
const HAIRLINE = 1;
const STROKE = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: HAIRLINE,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  vectorEffect: "non-scaling-stroke" as const, // stays 1px at any scene scale
};

// Root <svg> with sane defaults. `color` seeds currentColor for every child
// stroke; a scene overrides it (or passes an explicit `color` style) to recolor.
const Svg: React.FC<
  AssetProps & { viewBox: string; children: React.ReactNode; width: number; height: number }
> = ({ size = 120, viewBox, children, width, height, state, style, color, ...rest }) => {
  const w = size;
  const h = (size * height) / width;
  return (
    <svg
      viewBox={viewBox}
      width={w}
      height={h}
      xmlns="http://www.w3.org/2000/svg"
      // default ink = phosphor; scenes override via `color` prop or style
      style={{ color: (color as string) ?? T.accent, overflow: "visible", ...style }}
      {...rest}
    >
      {children}
    </svg>
  );
};

// ---------------------------------------------------------------------------
// <RobotClaude> — the protagonist. Rounded-square head (two eye dots + antenna),
// trapezoid body with a chest label, seated at a hairline desk.
//   variant="stressed" -> flat annoyed eyes (dashes), no smile
//   variant="em"       -> calm faint smile + a small "EM" badge rect
// ~30 elements. viewBox 120x140 (portrait-ish, desk included).
// ---------------------------------------------------------------------------

export const RobotClaude: React.FC<
  AssetProps & { variant?: "stressed" | "em"; label?: string }
> = ({ variant = "stressed", label = "CLAUDE CODE", size = 120, state, ...rest }) => {
  const stressed = variant === "stressed";
  return (
    <Svg size={size} viewBox="0 0 120 140" width={120} height={140} state={state} {...rest}>
      {/* antenna */}
      <line x1="60" y1="14" x2="60" y2="26" {...STROKE} />
      <circle cx="60" cy="11" r="2.5" {...STROKE} />

      {/* head — rounded square (<=2px radius) */}
      <rect x="38" y="26" width="44" height="34" rx="2" {...STROKE} />

      {/* eyes */}
      {stressed ? (
        <>
          {/* flat annoyed dashes */}
          <line x1="46" y1="42" x2="54" y2="42" {...STROKE} />
          <line x1="66" y1="42" x2="74" y2="42" {...STROKE} />
        </>
      ) : (
        <>
          {/* calm dots */}
          <circle cx="50" cy="41" r="2.2" fill="currentColor" stroke="none" />
          <circle cx="70" cy="41" r="2.2" fill="currentColor" stroke="none" />
        </>
      )}

      {/* mouth */}
      {stressed ? (
        // flat deadpan line
        <line x1="52" y1="52" x2="68" y2="52" {...STROKE} />
      ) : (
        // faint upturned smile
        <path d="M52 51 Q60 55 68 51" {...STROKE} />
      )}

      {/* neck */}
      <line x1="60" y1="60" x2="60" y2="66" {...STROKE} />

      {/* body — trapezoid (wider at base) */}
      <path d="M44 66 L76 66 L84 104 L36 104 Z" {...STROKE} />

      {/* chest label plate */}
      <rect x="46" y="76" width="28" height="12" rx="1" {...STROKE} />
      <text
        x="60"
        y="85"
        textAnchor="middle"
        fontFamily={T.fontMono}
        fontSize="5.5"
        letterSpacing="0.3"
        fill="currentColor"
        stroke="none"
      >
        {label}
      </text>

      {/* EM badge — only on the calm variant */}
      {!stressed && (
        <>
          <rect x="78" y="66" width="18" height="11" rx="1" {...STROKE} />
          <text
            x="87"
            y="74"
            textAnchor="middle"
            fontFamily={T.fontMono}
            fontSize="6.5"
            letterSpacing="0.5"
            fill="currentColor"
            stroke="none"
          >
            EM
          </text>
        </>
      )}

      {/* arms resting to the desk */}
      <path d="M44 74 L30 92 L30 108" {...STROKE} />
      <path d="M76 74 L90 92 L90 108" {...STROKE} />

      {/* hairline desk */}
      <line x1="8" y1="108" x2="112" y2="108" {...STROKE} />
      <line x1="18" y1="108" x2="18" y2="134" {...STROKE} />
      <line x1="102" y1="108" x2="102" y2="134" {...STROKE} />
    </Svg>
  );
};

// ---------------------------------------------------------------------------
// <RobotAgent> — a teammate. Simpler + smaller than Claude: round head, boxy
// body, a label plate, and its PROP mapping 1:1 to its job:
//   GEMINI -> stack of files (READ) · CODEX -> wrench (DRAFT) · GROK -> magnifier (CHECK)
// ~22-26 elements. viewBox 90x110.
// ---------------------------------------------------------------------------

export const RobotAgent: React.FC<
  AssetProps & { label?: "GEMINI" | "CODEX" | "GROK" | string }
> = ({ label = "GEMINI", size = 90, state, ...rest }) => {
  return (
    <Svg size={size} viewBox="0 0 90 110" width={90} height={110} state={state} {...rest}>
      {/* antenna */}
      <line x1="34" y1="10" x2="34" y2="18" {...STROKE} />
      <circle cx="34" cy="8" r="2" {...STROKE} />

      {/* head — small rounded square */}
      <rect x="20" y="18" width="28" height="22" rx="2" {...STROKE} />
      {/* eyes — calm dots */}
      <circle cx="28" cy="29" r="1.8" fill="currentColor" stroke="none" />
      <circle cx="40" cy="29" r="1.8" fill="currentColor" stroke="none" />

      {/* neck */}
      <line x1="34" y1="40" x2="34" y2="44" {...STROKE} />

      {/* body — boxy rectangle */}
      <rect x="20" y="44" width="28" height="30" rx="2" {...STROKE} />
      {/* label plate */}
      <rect x="23" y="52" width="22" height="10" rx="1" {...STROKE} />
      <text
        x="34"
        y="59.5"
        textAnchor="middle"
        fontFamily={T.fontMono}
        fontSize="5.5"
        letterSpacing="0.2"
        fill="currentColor"
        stroke="none"
      >
        {label}
      </text>

      {/* prop-bearing arm anchors */}
      <line x1="48" y1="52" x2="60" y2="56" {...STROKE} />

      {/* prop — mapped to the job */}
      <AgentProp label={label} />
    </Svg>
  );
};

// Prop glyphs held to the right of a teammate. Each is a small iconic schematic.
const AgentProp: React.FC<{ label: string }> = ({ label }) => {
  if (label === "CODEX") {
    // wrench — DRAFT
    return (
      <g>
        <path
          d="M62 50 a5 5 0 1 0 6 6 l8 8 3 -3 -8 -8 a5 5 0 0 0 -6 -6 l3 3 -3 3 -3 -3 Z"
          {...STROKE}
        />
      </g>
    );
  }
  if (label === "GROK") {
    // magnifier — CHECK
    return (
      <g>
        <circle cx="66" cy="54" r="7" {...STROKE} />
        <line x1="71" y1="59" x2="80" y2="68" {...STROKE} />
      </g>
    );
  }
  // default GEMINI — stack of files (READ): three offset sheets
  return (
    <g>
      <rect x="58" y="58" width="18" height="14" rx="1" {...STROKE} />
      <rect x="61" y="54" width="18" height="14" rx="1" {...STROKE} />
      <rect x="64" y="50" width="18" height="14" rx="1" {...STROKE} />
      {/* text lines on the top sheet */}
      <line x1="67" y1="55" x2="79" y2="55" {...STROKE} />
      <line x1="67" y1="59" x2="76" y2="59" {...STROKE} />
    </g>
  );
};

// ---------------------------------------------------------------------------
// <Flame> — one stylized tongue as a SINGLE path, baseline at the BOTTOM of the
// viewBox (y=64), tip at top. A scene grows it by animating `scaleY` 0->1 with
// `transformOrigin: 'bottom'`, and shifts color phosphor -> amber via `color`.
// viewBox 40x64. Default ink = amber (fire is the redline state).
// ---------------------------------------------------------------------------

export const Flame: React.FC<AssetProps> = ({ size = 60, state, color, ...rest }) => {
  return (
    <Svg
      size={size}
      viewBox="0 0 40 64"
      width={40}
      height={64}
      state={state}
      // fire defaults to amber; a scene can pass color=T.accent for the cool
      // (phosphor) base of the gradient and interpolate toward T.warn.
      color={(color as string) ?? T.warn}
      {...rest}
    >
      {/* single tongue: rises from a wide base (y=64) to a licked tip, with an
          inner notch so it reads as flame, not a leaf. Closed path. */}
      <path
        d="M20 64
           C 10 56 8 46 14 38
           C 16 34 15 30 12 26
           C 20 30 22 24 20 14
           C 26 22 30 28 30 40
           C 30 52 26 58 20 64 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth={HAIRLINE}
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </Svg>
  );
};

// ---------------------------------------------------------------------------
// <Hook> — the fishing-hook glyph (the "hooks" pun). Hangs from a vertical line
// at the TOP of the viewBox so a scene can `translateY` the whole glyph down on
// a taut line. Phosphor stroke. viewBox 40x120 (tall — the line is the drop).
// ---------------------------------------------------------------------------

export const Hook: React.FC<AssetProps> = ({ size = 60, state, ...rest }) => {
  return (
    <Svg size={size} viewBox="0 0 40 120" width={40} height={120} state={state} {...rest}>
      {/* taut line from the top */}
      <line x1="20" y1="0" x2="20" y2="66" {...STROKE} />
      {/* eyelet */}
      <circle cx="20" cy="70" r="3.5" {...STROKE} />
      {/* shank down + a clean J-curve back up to a single barb */}
      <path
        d="M20 73.5
           L20 96
           A 10 10 0 1 0 32 88"
        {...STROKE}
      />
      {/* barb — one clean spike off the hook tip */}
      <path d="M32 88 L27 90 M32 88 L33 83" {...STROKE} />
    </Svg>
  );
};

// ---------------------------------------------------------------------------
// <Mug> — coffee mug with a tiny label ("THIS IS FINE" easter egg). Hairline
// vector, sharp body, one steam wisp. viewBox 60x64.
// ---------------------------------------------------------------------------

export const Mug: React.FC<AssetProps & { label?: string }> = ({
  label = "THIS IS FINE",
  size = 60,
  state,
  ...rest
}) => {
  return (
    <Svg size={size} viewBox="0 0 60 64" width={60} height={64} state={state} {...rest}>
      {/* steam wisp */}
      <path d="M24 8 q4 4 0 8 M32 6 q4 4 0 8" {...STROKE} />
      {/* body */}
      <rect x="10" y="22" width="34" height="34" rx="2" {...STROKE} />
      {/* handle */}
      <path d="M44 30 h6 a6 6 0 0 1 0 16 h-6" {...STROKE} />
      {/* label plate */}
      <rect x="13" y="34" width="28" height="12" rx="1" {...STROKE} />
      <text
        x="27"
        y="42.5"
        textAnchor="middle"
        fontFamily={T.fontMono}
        fontSize="4.6"
        letterSpacing="0.2"
        fill="currentColor"
        stroke="none"
      >
        {label}
      </text>
    </Svg>
  );
};

// ---------------------------------------------------------------------------
// <Chip> — a boxed warning chip: 1px hairline, sharp corners, mono uppercase.
// For USAGE LIMIT HIT / CONTEXT ROT / IGNORED CLAUDE.MD. Width auto-sizes to the
// text via an inline-flex DOM wrapper (NOT an <svg>) so scenes can lay several
// out with normal layout and animate opacity/translate. Defaults to amber ink
// (warning = redline); pass a color style to override.
// ---------------------------------------------------------------------------

export const Chip: React.FC<{
  children: React.ReactNode;
  /** ink + border color; defaults to the amber redline. */
  color?: string;
  style?: React.CSSProperties;
}> = ({ children, color = T.warn, style }) => {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.4em",
        padding: "0.35em 0.6em",
        border: `1px solid ${color}`,
        borderRadius: 2,
        color,
        background: T.bg,
        fontFamily: `${T.fontMono}, monospace`,
        fontSize: 14,
        lineHeight: 1,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {/* leading redline tick — reads as an instrument warning marker */}
      <span
        aria-hidden
        style={{
          display: "inline-block",
          width: 5,
          height: 5,
          background: color,
        }}
      />
      {children}
    </span>
  );
};

// ---------------------------------------------------------------------------
// <WorkToken> — a tiny unit of dispatched work. A hairline square with a filled
// phosphor core (a "packet"). Scenes translate several along Claude->teammate
// paths for dispatch, and back (as distilled signal) on return. viewBox 12x12.
// ---------------------------------------------------------------------------

export const WorkToken: React.FC<AssetProps> = ({ size = 10, state, ...rest }) => {
  return (
    <Svg size={size} viewBox="0 0 12 12" width={12} height={12} state={state} {...rest}>
      <rect x="1.5" y="1.5" width="9" height="9" rx="1" {...STROKE} />
      <rect x="4" y="4" width="4" height="4" fill="currentColor" stroke="none" />
    </Svg>
  );
};
