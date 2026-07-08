import React from "react";
import { Composition, staticFile } from "remotion";
import { HeroRescue } from "./HeroRescue";
import { HeroMeme, HERO_DURATION } from "./HeroMeme";
import { LOOP_FRAMES, FPS } from "./tokens";

// Load brand fonts once for all compositions.
const style = document.createElement("style");
style.textContent = `
  @font-face {
    font-family: "GeistMono";
    src: url("${staticFile("fonts/GeistMono-Variable.ttf")}") format("truetype-variations");
    font-weight: 100 900;
  }
  @font-face {
    font-family: "Geist";
    src: url("${staticFile("fonts/Geist-Variable.ttf")}") format("truetype-variations");
    font-weight: 100 900;
  }
`;
document.head.appendChild(style);

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="HeroMeme"
        component={HeroMeme}
        durationInFrames={HERO_DURATION}
        fps={FPS}
        width={1280}
        height={640}
      />
      <Composition
        id="HeroRescue"
        component={HeroRescue}
        durationInFrames={LOOP_FRAMES}
        fps={FPS}
        width={1280}
        height={640}
        defaultProps={{ square: false }}
      />
      <Composition
        id="HeroRescueSquare"
        component={HeroRescue}
        durationInFrames={LOOP_FRAMES}
        fps={FPS}
        width={640}
        height={640}
        defaultProps={{ square: true }}
      />
    </>
  );
};
