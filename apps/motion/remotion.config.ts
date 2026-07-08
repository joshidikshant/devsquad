import { Config } from "@remotion/cli/config";

// DevSquad motion — brand-locked render defaults.
Config.setVideoImageFormat("png");
Config.setOverwriteOutput(true);
// GIFs: transparent-safe palette, high quality. Codec set per-command.
Config.setChromiumOpenGlRenderer("angle");
