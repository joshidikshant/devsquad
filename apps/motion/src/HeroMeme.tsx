import React from "react";
import { AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig } from "remotion";
import { T } from "./tokens";
import { Scene1Fire } from "./Scene1Fire";
import { Scene2Hook } from "./Scene2Hook";
import { Scene3Squad } from "./Scene3Squad";
import { Scene4Score } from "./Scene4Score";

// Scene timeline (30fps). One <Sequence> per scene — the official Remotion
// structure. Inside a <Sequence>, useCurrentFrame() is already remapped to a
// LOCAL 0-based frame, so each scene is self-contained and testable.
type SceneComp = React.FC<{
  frame: number;
  durationInFrames: number;
  width: number;
  height: number;
}>;

export const SCENES: { comp: SceneComp; from: number; dur: number; name: string }[] = [
  { comp: Scene1Fire, from: 0, dur: 72, name: "S1 · Fire" },
  { comp: Scene2Hook, from: 72, dur: 30, name: "S2 · Hook" },
  { comp: Scene3Squad, from: 102, dur: 60, name: "S3 · Squad" },
  { comp: Scene4Score, from: 162, dur: 48, name: "S4 · Score" },
];

export const HERO_DURATION = 210;

const SceneBridge: React.FC<{ Comp: SceneComp; dur: number }> = ({ Comp, dur }) => {
  const frame = useCurrentFrame(); // 0-based within the enclosing Sequence
  const { width, height } = useVideoConfig();
  return <Comp frame={frame} durationInFrames={dur} width={width} height={height} />;
};

export const HeroMeme: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: T.bg, fontFamily: T.fontMono, overflow: "hidden" }}>
      {SCENES.map((s, i) => (
        <Sequence key={i} from={s.from} durationInFrames={s.dur} premountFor={30} name={s.name}>
          <SceneBridge Comp={s.comp} dur={s.dur} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
