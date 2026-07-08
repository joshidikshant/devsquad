// Signal over Noise — "The Instrument" · Night Bench tokens (verbatim from
// dikshantjoshi-design styles.css :root). One accent: phosphor. Amber is the
// SANCTIONED instrument redline state only — nowhere decorative.
export const T = {
  bg: "#0A0D0C",
  bgSubtle: "#0E1210",
  fg: "#E9EFEA",
  fgMuted: "#9DACA3",
  fgSubtle: "#75857B",
  fgFaint: "#38423D",
  border: "#1E2622",
  borderStrong: "#2C3630",
  accent: "#00E5A0", // phosphor
  accentDim: "#0D2A1F",
  warn: "#F5A623", // instrument redline ONLY (meter in the red)
  fontMono: "GeistMono",
  fontSans: "Geist",
  // brand ease: cubic-bezier(0.6,0,0.1,1)
  ease: [0.6, 0, 0.1, 1] as [number, number, number, number],
} as const;

// Loop timing — 30fps.
export const FPS = 30;
export const LOOP_FRAMES = 150; // 5.0s seamless loop

// Named beats as frame fractions (single source of truth, 0..1).
export const BEAT = {
  coldOpen: 0.0,
  hookFire: 0.2,
  dispatch: 0.3,
  returnStart: 0.62,
  resolve: 0.88,
} as const;
