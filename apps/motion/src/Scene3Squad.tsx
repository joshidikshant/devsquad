// Signal over Noise — "The Instrument" · SCENE 3: EM MODE (local f0–60).
// The flex. Calm EM Claude at the hub; three teammate agents (GEMINI=READ,
// CODEX=DRAFT, GROK=CHECK) arranged around; labels type in; WorkTokens flow
// Claude -> each agent (dispatch) then distilled signal returns. Everything
// phosphor, orderly. Pure function of the `frame` prop — no useCurrentFrame.
//
// Craft law: interpolate with the brand ease, individual scale/translate/rotate
// props (never composed transform strings), typewriter via string slicing.

import React from "react";
import { interpolate, Easing } from "remotion";
import { T } from "./tokens";
import { RobotClaude, RobotAgent, WorkToken } from "./assets";

// Brand ease — cubic-bezier(0.6,0,0.1,1). One place, reused everywhere.
const EASE = Easing.bezier(T.ease[0], T.ease[1], T.ease[2], T.ease[3]);

// interpolate with clamped extrapolation + brand ease, as a terse helper.
const ease = (frame: number, input: number[], output: number[]): number =>
  interpolate(frame, input, output, {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASE,
  });

// Linear clamp (for path parameters t in [0,1] where we DON'T want easing —
// tokens travel at constant velocity along the wire).
const lin = (frame: number, input: number[], output: number[]): number =>
  interpolate(frame, input, output, {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

// Point along a straight segment a->b at parameter t in [0,1].
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

// One teammate's static geometry, resolved against the composition.
interface AgentSlot {
  key: "GEMINI" | "CODEX" | "GROK";
  // center of the agent's body plate, in composition px (dispatch target)
  cx: number;
  cy: number;
  // when this agent's reveal begins (local frames)
  appearAt: number;
  // when this agent's dispatch token launches from the hub
  dispatchAt: number;
}

export const Scene3Squad: React.FC<{
  frame: number;
  durationInFrames: number;
  width: number;
  height: number;
}> = ({ frame, width, height }) => {
  // ---- Layout anchors (everything relative to width/height) --------------
  const cx = width / 2;
  const cy = height / 2;

  // Claude hub sits slightly below center so the squad arcs above/around it.
  const claudeSize = Math.min(width, height) * 0.42; // ~269px on 1280x640
  const claudeW = claudeSize;
  const claudeH = (claudeSize * 140) / 120; // asset viewBox aspect
  const claudeX = cx - claudeW / 2;
  const claudeY = cy - claudeH * 0.42;

  // Hub dispatch origin — Claude's chest plate, in composition px.
  const hubX = cx;
  const hubY = claudeY + claudeH * (85 / 140);

  const agentSize = Math.min(width, height) * 0.26; // ~166px
  const agentW = agentSize;
  const agentH = (agentSize * 110) / 90;

  // Three slots forming a balanced arc AROUND the hub rather than piling two
  // teammates on one side. GEMINI upper-left and CODEX upper-right are a
  // symmetric flanking pair; GROK anchors the bottom, pulled toward center so
  // the three read as an even triangle wrapping Claude (fixes the left-heavy,
  // right-stacked composition). GROK's cy is kept at +0.22h so its label below
  // it still clears the bottom ~5% safe margin.
  const slots: AgentSlot[] = [
    {
      key: "GEMINI",
      cx: cx - width * 0.3,
      cy: cy - height * 0.16,
      appearAt: 6,
      dispatchAt: 16,
    },
    {
      key: "CODEX",
      cx: cx + width * 0.3,
      cy: cy - height * 0.16,
      appearAt: 12,
      dispatchAt: 22,
    },
    {
      key: "GROK",
      cx: cx + width * 0.16,
      cy: cy + height * 0.22,
      appearAt: 18,
      dispatchAt: 28,
    },
  ];

  // ---- Global scene-level motion ----------------------------------------
  // Gentle settle-in of the whole tableau (calm, not snappy).
  const sceneIn = ease(frame, [0, 10], [0, 1]);
  // A single faint phosphor "breathing" pulse across the squad mid-scene —
  // orderly heartbeat, not decoration. Deterministic sine sampled off frame.
  const pulse = 0.5 + 0.5 * Math.sin((frame / 60) * Math.PI * 2 - Math.PI / 2);
  const wireBase = 0.22 + 0.14 * pulse;

  // Claude fades/scales in first, calm.
  const claudeOpacity = ease(frame, [0, 8], [0, 1]);
  const claudeScale = ease(frame, [0, 12], [0.92, 1]);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        width,
        height,
        background: T.bg,
        fontFamily: `${T.fontMono}, monospace`,
        overflow: "hidden",
      }}
    >
      {/* ---- Dispatch/return wires (drawn under the robots) ---------------- */}
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ position: "absolute", inset: 0, overflow: "visible" }}
      >
        {slots.map((s) => {
          // wire draws on when the agent appears (stroke-dash reveal via
          // interpolated length — deterministic, no CSS animation).
          const drawn = ease(frame, [s.appearAt, s.appearAt + 12], [0, 1]);
          const len = Math.hypot(s.cx - hubX, s.cy - hubY);
          return (
            <line
              key={`wire-${s.key}`}
              x1={hubX}
              y1={hubY}
              x2={s.cx}
              y2={s.cy}
              stroke={T.accent}
              strokeWidth={1}
              strokeOpacity={wireBase * drawn}
              strokeDasharray={len}
              strokeDashoffset={len * (1 - drawn)}
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
      </svg>

      {/* ---- Claude hub (EM, calm) ---------------------------------------- */}
      <div
        style={{
          position: "absolute",
          left: claudeX,
          top: claudeY,
          width: claudeW,
          height: claudeH,
          opacity: claudeOpacity,
          scale: claudeScale,
          transformOrigin: "50% 60%",
          color: T.accent,
        }}
      >
        <RobotClaude variant="em" size={claudeW} color={T.accent} />
      </div>

      {/* ---- Teammate agents ---------------------------------------------- */}
      {slots.map((s) => {
        const appear = ease(frame, [s.appearAt, s.appearAt + 12], [0, 1]);
        const rise = ease(frame, [s.appearAt, s.appearAt + 12], [14, 0]);
        const agentX = s.cx - agentW / 2;
        // s.cy targets the body-plate center (~y=57/110 in the viewBox).
        const agentY = s.cy - agentH * (57 / 110);
        return (
          <div
            key={`agent-${s.key}`}
            style={{
              position: "absolute",
              left: agentX,
              top: agentY,
              width: agentW,
              height: agentH,
              opacity: appear,
              translate: `0px ${rise}px`,
              scale: sceneIn,
              transformOrigin: "50% 40%",
              color: T.accent,
            }}
          >
            <RobotAgent label={s.key} size={agentW} color={T.accent} />
          </div>
        );
      })}

      {/* ---- Node labels (typewriter via string slicing) ------------------ */}
      {slots.map((s) => {
        // label starts typing just as the agent finishes rising.
        const typeStart = s.appearAt + 8;
        const chars = Math.round(lin(frame, [typeStart, typeStart + 8], [0, s.key.length]));
        const shown = s.key.slice(0, chars);
        const labelOpacity = ease(frame, [typeStart, typeStart + 4], [0, 1]);
        // Caret shows ONLY while characters are still being added — i.e. before
        // the label is fully typed. The instant the string completes, the caret
        // disappears (no stuck "GROK_" at the end of the scene). It blinks on a
        // deterministic frame parity while active.
        const stillTyping = frame >= typeStart && shown.length < s.key.length;
        const caret = stillTyping && Math.floor(frame / 4) % 2 === 0 ? "_" : "";
        // place the label just below each agent.
        const labelY = s.cy + agentH * (57 / 110) + 10;
        return (
          <div
            key={`label-${s.key}`}
            style={{
              position: "absolute",
              left: s.cx,
              top: labelY,
              translate: "-50% 0",
              opacity: labelOpacity,
              color: T.accent,
              fontFamily: `${T.fontMono}, monospace`,
              fontSize: Math.max(11, agentW * 0.13),
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              whiteSpace: "nowrap",
            }}
          >
            {shown}
            {caret}
          </div>
        );
      })}

      {/* ---- Work tokens: dispatch (hub->agent) then return (agent->hub) --- */}
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ position: "absolute", inset: 0, overflow: "visible" }}
      >
        {slots.map((s) => {
          const tokenSize = Math.max(9, agentSize * 0.09);
          const dispatchStart = s.dispatchAt;
          const dispatchEnd = s.dispatchAt + 12;
          const returnStart = dispatchEnd + 4;
          const returnEnd = returnStart + 12;

          // Endpoints pulled slightly inside each body so tokens dock, not overlap.
          const ax = lerp(hubX, s.cx, 0.14);
          const ay = lerp(hubY, s.cy, 0.14);
          const bx = lerp(s.cx, hubX, 0.14);
          const by = lerp(s.cy, hubY, 0.14);

          const nodes: React.ReactNode[] = [];

          // Dispatch token: hub -> agent (phosphor packet outbound).
          if (frame >= dispatchStart && frame <= returnEnd) {
            const tD = lin(frame, [dispatchStart, dispatchEnd], [0, 1]);
            const dOn = frame >= dispatchStart && frame <= dispatchEnd;
            const dx = lerp(ax, bx, tD);
            const dy = lerp(ay, by, tD);
            // fade in at launch, fade out on arrival.
            const dOpacity = dOn
              ? ease(frame, [dispatchStart, dispatchStart + 3, dispatchEnd - 3, dispatchEnd], [0, 1, 1, 0])
              : 0;
            if (dOpacity > 0) {
              nodes.push(
                <g
                  key={`disp-${s.key}`}
                  style={{
                    translate: `${dx - tokenSize / 2}px ${dy - tokenSize / 2}px`,
                    opacity: dOpacity,
                    color: T.accent,
                  }}
                >
                  <WorkToken size={tokenSize} color={T.accent} />
                </g>
              );
            }

            // Return token: agent -> hub (distilled signal inbound). Brighter core
            // is implicit; we just run it back along the same wire.
            const rOn = frame >= returnStart && frame <= returnEnd;
            if (rOn) {
              const tR = lin(frame, [returnStart, returnEnd], [0, 1]);
              const rx = lerp(bx, ax, tR);
              const ry = lerp(by, ay, tR);
              const rOpacity = ease(
                frame,
                [returnStart, returnStart + 3, returnEnd - 3, returnEnd],
                [0, 1, 1, 0]
              );
              nodes.push(
                <g
                  key={`ret-${s.key}`}
                  style={{
                    translate: `${rx - tokenSize / 2}px ${ry - tokenSize / 2}px`,
                    opacity: rOpacity,
                    color: T.accent,
                  }}
                >
                  <WorkToken size={tokenSize} color={T.accent} />
                </g>
              );
            }
          }

          return <g key={`tokens-${s.key}`}>{nodes}</g>;
        })}
      </svg>

      {/* ---- Scene tag (small, instrument-style, top-left) ---------------- */}
      <div
        style={{
          position: "absolute",
          left: width * 0.04,
          top: height * 0.06,
          opacity: ease(frame, [2, 12], [0, 1]) * 0.7,
          color: T.fgSubtle,
          fontFamily: `${T.fontMono}, monospace`,
          fontSize: Math.max(10, width * 0.011),
          letterSpacing: "0.22em",
          textTransform: "uppercase",
        }}
      >
        EM MODE // SQUAD ONLINE
      </div>
    </div>
  );
};

export default Scene3Squad;
