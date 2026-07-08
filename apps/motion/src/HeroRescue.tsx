import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  Easing,
} from "remotion";
import { ThreeCanvas } from "@remotion/three";
import { T, LOOP_FRAMES, BEAT } from "./tokens";
import { Particles, NODES } from "./Particles";

const ease = Easing.bezier(...T.ease);

// Node screen positions as % of frame — MUST match Particles NODES layout,
// mapped from normalized (−1..1, y-up) to CSS (%, y-down). Tight = fills frame.
const nodeScreen = (pos: [number, number], square: boolean) => {
  const spread = square ? 0.34 : 0.30; // fraction of frame the constellation occupies
  return {
    left: `${50 + pos[0] * spread * 100 * (square ? 1 : 0.62)}%`,
    top: `${50 - pos[1] * spread * 100}%`,
  };
};

const AGENTS = [
  { key: "gemini", label: "GEMINI", verb: "READ" },
  { key: "codex", label: "CODEX", verb: "DRAFT" },
  { key: "grok", label: "GROK", verb: "CHECK" },
];

export const HeroRescue: React.FC<{ square?: boolean }> = ({ square = false }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const p = (frame % LOOP_FRAMES) / LOOP_FRAMES;

  // Context meter: climbs to redline in panic, drains on return. Continuous at seam.
  const climb = interpolate(p, [BEAT.coldOpen, BEAT.hookFire], [0.42, 1.0], {
    extrapolateRight: "clamp",
    easing: ease,
  });
  const drain = interpolate(p, [BEAT.returnStart, BEAT.resolve], [1.0, 0.16], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  const reClimb = interpolate(p, [BEAT.resolve, 1.0], [0.16, 0.42], {
    extrapolateLeft: "clamp",
    easing: ease,
  });
  const lvl = p < BEAT.returnStart ? climb : p < BEAT.resolve ? drain : reClimb;
  const ctxK = Math.round(58 + lvl * 78); // 58K..136K
  const redline = lvl > 0.72;
  const meterColor = redline ? T.warn : T.accent;

  // spokes flash in at hook, hold, fade across seam
  const spokeOn = interpolate(p, [BEAT.hookFire, BEAT.hookFire + 0.04], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // spokes + node frames clear by 0.90 so cold-open (0.0) is a lone hub,
  // matching phase 1.0 for a seamless loop.
  const spokeOff = 1 - interpolate(p, [0.82, 0.90], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const spokeVis = spokeOn * spokeOff;

  // hook-fire snap flash — a brief, restrained tick (not a full-screen wash).
  // A thin bright SCANLINE does the work; the fill flash is subtle.
  const flash = interpolate(p, [BEAT.hookFire, BEAT.hookFire + 0.015, BEAT.hookFire + 0.05], [0, 0.14, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  // horizontal scanline sweeps down through the hub at the hook
  const scanY = interpolate(p, [BEAT.hookFire - 0.02, BEAT.hookFire + 0.05], [0, 100], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  const scanVis = interpolate(p, [BEAT.hookFire - 0.02, BEAT.hookFire, BEAT.hookFire + 0.05], [0, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const unit = Math.min(width, height);
  const labelSize = unit * 0.026; // ~2x the old size
  const wordSize = unit * 0.082;
  const meterR = unit * 0.115; // sized to sit inside the node ring, clear of labels

  return (
    <AbsoluteFill style={{ backgroundColor: T.bg, fontFamily: T.fontMono, overflow: "hidden" }}>
      {/* faint calibration grid */}
      <AbsoluteFill
        style={{
          backgroundImage: `linear-gradient(${T.border} 1px, transparent 1px), linear-gradient(90deg, ${T.border} 1px, transparent 1px)`,
          backgroundSize: `${unit * 0.05}px ${unit * 0.05}px`,
          opacity: 0.35,
          maskImage: "radial-gradient(circle at 50% 46%, black, transparent 78%)",
        }}
      />

      {/* particle canvas */}
      <ThreeCanvas width={width} height={height} orthographic camera={{ zoom: 1, position: [0, 0, 5] }} gl={{ alpha: true }}>
        <Particles />
      </ThreeCanvas>

      {/* spokes (SVG hairlines hub->node) */}
      <svg width={width} height={height} style={{ position: "absolute", inset: 0, opacity: spokeVis }}>
        {NODES.map((n, i) => {
          const s = nodeScreen(n.pos, square);
          const x = (parseFloat(s.left) / 100) * width;
          const y = (parseFloat(s.top) / 100) * height;
          return <line key={i} x1={width / 2} y1={height / 2} x2={x} y2={y} stroke={T.accentDim} strokeWidth={1.5} />;
        })}
      </svg>

      {/* hook-fire: NO full-frame wash (screen-blend of phosphor over near-black
          lifts the whole frame). A bright scanline sweeping the hub carries the
          beat; a whisper-subtle vignette lift sells the "snap" without a wash. */}
      <AbsoluteFill style={{ backgroundColor: T.accentDim, opacity: flash * 0.5 }} />
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: `${scanY}%`,
          height: 2,
          background: T.accent,
          opacity: scanVis,
        }}
      />
      {scanVis > 0.01 && (
        <div style={{ position: "absolute", left: 0, right: 0, top: `${scanY}%`, height: unit * 0.06, background: `linear-gradient(${T.accent}22, transparent)`, opacity: scanVis * 0.6 }} />
      )}

      {/* node labels + frames */}
      {AGENTS.map((a, i) => {
        const s = nodeScreen(NODES[i].pos, square);
        const arrived = interpolate(p, [BEAT.dispatch + i * 0.03, BEAT.dispatch + 0.12 + i * 0.03], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }) * spokeVis;
        return (
          <div key={a.key} style={{ position: "absolute", ...s, transform: "translate(-50%,-50%)", textAlign: "center" }}>
            <div
              style={{
                width: unit * 0.028,
                height: unit * 0.028,
                border: `1px solid ${T.borderStrong}`,
                borderColor: arrived > 0.5 ? T.accent : T.borderStrong,
                background: arrived > 0.5 ? T.accentDim : "transparent",
                margin: "0 auto",
                marginBottom: unit * 0.012,
              }}
            />
            <div style={{ fontSize: labelSize, fontWeight: 600, letterSpacing: "0.16em", color: T.fg, whiteSpace: "nowrap" }}>
              {a.label} <span style={{ color: T.fgFaint }}>/ {a.verb}</span>
            </div>
          </div>
        );
      })}

      {/* CLAUDE hub label */}
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: `calc(50% + ${meterR * 0.5}px)`,
          transform: "translate(-50%,0)",
          fontSize: labelSize,
          fontWeight: 600,
          letterSpacing: "0.2em",
          color: T.fg,
        }}
      >
        CLAUDE
      </div>

      {/* CONTEXT METER — enlarged, centered over hub */}
      <div style={{ position: "absolute", left: "50%", top: `${square ? 14 : 12}%`, transform: "translate(-50%,0)", textAlign: "center" }}>
        <div style={{ fontSize: labelSize * 0.7, letterSpacing: "0.24em", color: T.fgSubtle }}>CONTEXT</div>
        <div style={{ fontSize: labelSize * 1.5, fontWeight: 600, color: meterColor, letterSpacing: "0.04em", marginTop: unit * 0.006 }}>
          {ctxK}K {lvl > 0.5 ? "▲" : "▼"}
        </div>
      </div>

      {/* radial meter arc around hub */}
      <svg
        width={meterR * 2.4}
        height={meterR * 2.4}
        style={{ position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)" }}
      >
        {(() => {
          const cx = meterR * 1.2;
          const cy = meterR * 1.2;
          const r = meterR;
          const start = -Math.PI * 0.75;
          const sweep = Math.PI * 1.5 * lvl;
          const x2 = cx + r * Math.cos(start + sweep);
          const y2 = cy + r * Math.sin(start + sweep);
          const large = sweep > Math.PI ? 1 : 0;
          const bx = cx + r * Math.cos(start);
          const by = cy + r * Math.sin(start);
          return (
            <>
              <circle cx={cx} cy={cy} r={r} fill="none" stroke={T.border} strokeWidth={2} strokeDasharray={`${r * Math.PI * 1.5} ${r * Math.PI}`} transform={`rotate(135 ${cx} ${cy})`} />
              {lvl > 0.02 && (
                <path d={`M ${bx} ${by} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`} fill="none" stroke={meterColor} strokeWidth={2.5} />
              )}
            </>
          );
        })()}
      </svg>

      {/* wordmark — bigger, tighter under the hub */}
      <div style={{ position: "absolute", left: "50%", bottom: square ? "9%" : "11%", transform: "translate(-50%,0)", textAlign: "center" }}>
        <div style={{ fontSize: wordSize, fontWeight: 600, letterSpacing: "0.28em", color: T.fg }}>DEVSQUAD</div>
        <div style={{ fontSize: labelSize * 0.82, letterSpacing: "0.22em", color: T.fgSubtle, marginTop: unit * 0.01 }}>
          HOOKS <span style={{ color: T.accent }}>//</span> DON&apos;T IGNORE YOU
        </div>
      </div>
    </AbsoluteFill>
  );
};
