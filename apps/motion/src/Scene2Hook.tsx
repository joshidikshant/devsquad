import React from "react";
import { interpolate, spring, Easing } from "remotion";
import { T, FPS } from "./tokens";
import { Hook, Flame, RobotClaude } from "./assets";

// ---------------------------------------------------------------------------
// SCENE 2 — THE INTERVENTION (local f0-30 @ 30fps · storyboard f72-102)
//
// The turn. A hook drops from the top on a taut line, catches, and snuffs the
// fire in one snap. Beats (local frames):
//   0–6   hook descends fast (translateY interpolate, brand ease)
//   6–14  "⟩ HOOK INTERCEPT" snaps in (spring scale); micro screen-tick
//   6–16  flames YANKED to 0 height + amber cools phosphor→black
//   14–30 phosphor scanline sweeps L→R; robot straightens stressed→em,
//         gains its EM badge by the end
//
// Pure function of the `frame` prop — no useCurrentFrame, testable in isolation.
// ---------------------------------------------------------------------------

const ease = Easing.bezier(...T.ease);

// Amber → black cool-down. Interpolates each RGB channel of T.warn toward 0 so
// the extinguished flame literally goes dark rather than snapping to a token.
const coolAmber = (t: number): string => {
  // T.warn #F5A623 → #000000
  const r = Math.round(0xf5 * (1 - t));
  const g = Math.round(0xa6 * (1 - t));
  const b = Math.round(0x23 * (1 - t));
  return `rgb(${r}, ${g}, ${b})`;
};

export const Scene2Hook: React.FC<{
  frame: number;
  durationInFrames: number;
  width: number;
  height: number;
}> = ({ frame, durationInFrames, width, height }) => {
  const cx = width / 2;
  const cy = height / 2;

  // Robot sits centered, seated at the desk. Its viewBox is 120x140.
  const robotSize = Math.min(width, height) * 0.62;
  const robotW = robotSize;
  const robotH = (robotSize * 140) / 120;
  // Desk line in the robot's viewBox is at y=108/140 → screen y of the desk.
  const robotTop = cy - robotH * 0.46;
  const deskY = robotTop + robotH * (108 / 140);

  // -- Hook descent -------------------------------------------------------
  // Hook viewBox is 40x120; the barb/tip sits near y≈96/120. We drop it so the
  // hook tip lands just above the desk, "catching" the fire at the corner.
  const hookSize = Math.min(width, height) * 0.34;
  const hookH = (hookSize * 120) / 40;
  const hookX = cx; // dead center over the robot
  // Final resting top: barb tip (96/120 of viewBox) reaches the desk corner.
  const hookRestTop = deskY - hookH * (96 / 120);
  const hookStartTop = -hookH; // fully above frame
  const hookTop = interpolate(frame, [0, 6], [hookStartTop, hookRestTop], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });

  // -- Catch snap (screen-tick) ------------------------------------------
  // A tiny vertical recoil the instant the hook bites, at f6.
  const tickSpring = spring({
    frame: frame - 6,
    fps: FPS,
    config: { damping: 9, stiffness: 320, mass: 0.5 },
  });
  // recoil: quick up-kick that settles — derived from the spring's overshoot.
  const screenTickY = interpolate(frame, [6, 8, 11], [0, -6, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });

  // -- Flame extinguish ---------------------------------------------------
  // Two flame tongues at the desk corners get yanked to 0 height at the catch.
  const flameSize = Math.min(width, height) * 0.26;
  const flameH = (flameSize * 64) / 40;
  const flameScaleY = interpolate(frame, [0, 6, 12], [1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  const flameCool = interpolate(frame, [6, 16], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  const flameColor = coolAmber(flameCool);
  const flameOpacity = interpolate(frame, [10, 16], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // -- Phosphor shock flash on catch -------------------------------------
  const flash = interpolate(frame, [6, 7, 12], [0, 0.16, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // -- "⟩ HOOK INTERCEPT" label (spring scale + typewriter) --------------
  const labelText = "⟩ HOOK INTERCEPT";
  // reveal by string slicing off the frame (never per-char opacity)
  const labelChars = Math.round(
    interpolate(frame, [6, 12], [0, labelText.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    })
  );
  const labelVisible = labelText.slice(0, labelChars);
  const labelScale = spring({
    frame: frame - 6,
    fps: FPS,
    config: { damping: 12, stiffness: 260, mass: 0.6 },
  });
  // brief: fade the whole label out toward the end of the scene.
  const labelOpacity = interpolate(frame, [6, 8, 22, 27], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // -- Scanline sweep L→R -------------------------------------------------
  // The sweep travels a hair off-screen on BOTH ends so neither the hairline
  // nor its trailing wash ever parks at the left edge. It starts just past the
  // left border (not at x=0) and finishes just past the right border.
  const sweepWashW = width * 0.12;
  const sweepX = interpolate(frame, [14, 30], [-sweepWashW, width + 2], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  const sweepOpacity = interpolate(frame, [14, 16, 27, 30], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // -- Robot straighten + stressed→em ------------------------------------
  // Straighten: a small rotate settling to 0 and a rise as it sits up.
  const straightenRot = interpolate(frame, [8, 20], [3, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  const straightenY = interpolate(frame, [8, 20], [4, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  // The scanline is what "flips" the variant: once the sweep passes the robot's
  // center, it reads as EM. Cross-fade the two variants across the sweep front.
  const robotCenterX = cx;
  const emReveal = interpolate(
    sweepX,
    [robotCenterX - robotW * 0.28, robotCenterX + robotW * 0.28],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const flameCornerDX = robotW * 0.34; // desk corners left/right of center

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        backgroundColor: T.bg,
        overflow: "hidden",
        // micro screen-tick on catch — individual translate prop
        translate: `0px ${screenTickY}px`,
      }}
    >
      {/* phosphor shock flash on catch (full-frame, additive-feel wash) */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: T.accent,
          opacity: flash,
          pointerEvents: "none",
        }}
      />

      {/* --- Flames at the desk corners (being extinguished) --- */}
      {[-1, 1].map((dir) => (
        <div
          key={dir}
          style={{
            position: "absolute",
            left: cx + dir * flameCornerDX - flameSize / 2,
            top: deskY - flameH,
            width: flameSize,
            height: flameH,
            opacity: flameOpacity,
            scale: `1 ${flameScaleY}`,
            transformOrigin: "bottom center",
          }}
        >
          <Flame size={flameSize} color={flameColor} />
        </div>
      ))}

      {/* --- The robot: stressed → em (cross-faded across the sweep) --- */}
      <div
        style={{
          position: "absolute",
          left: cx - robotW / 2,
          top: robotTop,
          width: robotW,
          height: robotH,
          translate: `0px ${straightenY}px`,
          rotate: `${straightenRot}deg`,
          transformOrigin: "bottom center",
        }}
      >
        {/* stressed underneath */}
        <div style={{ position: "absolute", inset: 0, opacity: 1 - emReveal }}>
          <RobotClaude variant="stressed" size={robotW} color={T.accent} />
        </div>
        {/* em on top, revealed by the sweep front */}
        <div style={{ position: "absolute", inset: 0, opacity: emReveal }}>
          <RobotClaude variant="em" size={robotW} color={T.accent} />
        </div>
      </div>

      {/* --- The hook descending on its taut line --- */}
      <div
        style={{
          position: "absolute",
          left: hookX - hookSize / 2,
          top: hookTop,
          width: hookSize,
          height: hookH,
        }}
      >
        <Hook size={hookSize} color={T.accent} />
      </div>

      {/* --- Scanline sweep L→R --- */}
      {/* Trailing wash first (behind the line): anchored to trail the sweep    */}
      {/* front. It is placed at sweepX (the front) with a NEGATIVE translate    */}
      {/* the width of the wash, so the strip always sits just LEFT of the       */}
      {/* front and slides fully off-screen at both ends — never clamped to      */}
      {/* x=0, which is what used to park a green bar at the left edge.          */}
      <div
        style={{
          position: "absolute",
          top: 0,
          bottom: 0,
          left: sweepX,
          width: sweepWashW,
          translate: `${-sweepWashW}px 0px`,
          background: `linear-gradient(to right, rgba(0,229,160,0), ${T.accentDim})`,
          opacity: sweepOpacity * 0.9,
          pointerEvents: "none",
        }}
      />
      {/* phosphor hairline sweep front */}
      <div
        style={{
          position: "absolute",
          top: 0,
          bottom: 0,
          left: sweepX,
          width: 2,
          background: T.accent,
          opacity: sweepOpacity,
          pointerEvents: "none",
        }}
      />

      {/* --- "⟩ HOOK INTERCEPT" — snaps in at center, typewriter reveal --- */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: cy - robotH * 0.5 - Math.min(width, height) * 0.06,
          display: "flex",
          justifyContent: "center",
          pointerEvents: "none",
          opacity: labelOpacity,
          scale: `${0.6 + 0.4 * labelScale}`,
        }}
      >
        <span
          style={{
            fontFamily: `${T.fontMono}, monospace`,
            fontSize: Math.min(width, height) * 0.05,
            fontWeight: 600,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            color: T.accent,
            border: `1px solid ${T.accent}`,
            background: T.bg,
            padding: "0.3em 0.7em",
            borderRadius: 2,
            whiteSpace: "nowrap",
            lineHeight: 1,
          }}
        >
          {labelVisible}
        </span>
      </div>
    </div>
  );
};
