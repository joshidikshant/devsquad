import React, { useMemo, useRef } from "react";
import { useCurrentFrame } from "remotion";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { T, LOOP_FRAMES, BEAT } from "./tokens";

// Node layout in a tight normalized square (−1..1). Pulled IN from the old
// wide spread so the constellation fills the frame (legibility fix).
export const NODES = [
  { key: "gemini", pos: [-0.62, 0.46] as [number, number] },
  { key: "codex", pos: [0.66, 0.30] as [number, number] },
  { key: "grok", pos: [-0.30, -0.62] as [number, number] },
];

const PER_SPOKE = 90;
const N = PER_SPOKE * NODES.length;

// smoothstep + brand ease helpers (GLSL-side too, but JS drives uniforms)
const vert = /* glsl */ `
  uniform float uPhase;
  uniform vec2 uNodes[3];
  attribute float aSpoke;
  attribute float aOffset;
  varying float vBright;

  float ss(float a, float b, float x){ return smoothstep(a,b,x); }

  void main() {
    int idx = int(aSpoke);
    vec2 node = uNodes[idx];
    vec2 hub = vec2(0.0);

    float ph = uPhase;
    // outbound is a WINDOWED pulse: rises at dispatch, falls before return.
    // (A plain step stayed at 1.0 forever and re-lit outbound particles at the
    // seam once retW fell to 0 — the loop-pop bug.)
    float outRise = ss(${BEAT.dispatch.toFixed(2)}, ${BEAT.dispatch.toFixed(2)} + 0.08, ph);
    float outFall = 1.0 - ss(${BEAT.returnStart.toFixed(2)} - 0.06, ${BEAT.returnStart.toFixed(2)}, ph);
    float outW = outRise * outFall;
    // return rises after returnStart, then FULLY clears before the seam so
    // phase 1.0 == phase 0.0 (empty cold-open) — a seamless loop.
    float retRise = ss(${BEAT.returnStart.toFixed(2)}, 0.76, ph);
    float retFall = 1.0 - ss(0.80, 0.87, ph);
    float retW = retRise * retFall;

    // outbound: scattered raw work hub->node ; return: aligned signal node->hub
    float local = clamp((ph - ${BEAT.dispatch.toFixed(2)}) / (${BEAT.returnStart.toFixed(2)} - ${BEAT.dispatch.toFixed(2)}), 0.0, 1.0);
    float ret   = clamp((ph - ${BEAT.returnStart.toFixed(2)}) / (0.98 - ${BEAT.returnStart.toFixed(2)}), 0.0, 1.0);

    float tOut = fract(local * 1.0 + aOffset);
    float tRet = fract(ret * 1.0 + aOffset * 0.5);

    // per-agent personality
    float personality = 1.0;
    if (idx == 0) personality = exp(-pow((local - 0.5) * 3.0, 2.0)) * 1.4 + 0.3; // Gemini swell
    if (idx == 1) personality = step(0.5, fract(local * 4.0)) * 1.2 + 0.2;       // Codex staccato
    if (idx == 2) personality = 1.0;                                             // Grok steady sweep

    vec2 pOut = mix(hub, node, tOut);
    // scatter outbound perpendicular to the spoke (raw work)
    vec2 dir = normalize(node - hub);
    vec2 perp = vec2(-dir.y, dir.x);
    pOut += perp * (aOffset - 0.5) * 0.18 * (1.0 - tOut);

    vec2 pRet = mix(node, hub, tRet); // return is clean/aligned

    vec2 p = mix(pOut, pRet, retW * step(${BEAT.returnStart.toFixed(2)}, ph));
    // both windows are self-contained pulses that reach 0 before the seam
    float alive = max(outW * personality, retW * 1.3);

    vBright = alive;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 0.0, 1.0);
    gl_PointSize = mix(2.4, 3.6, retW) * (0.6 + 0.8 * alive);
  }
`;

const frag = /* glsl */ `
  precision highp float;
  uniform vec3 uOut;
  uniform vec3 uRet;
  uniform float uPhase;
  varying float vBright;
  void main() {
    // brightness carries meaning (no gl_PointCoord disc — brand rule 2A)
    float ret = smoothstep(${BEAT.returnStart.toFixed(2)}, 0.98, uPhase);
    vec3 col = mix(uOut, uRet, ret);
    gl_FragColor = vec4(col * (0.35 + vBright), clamp(vBright, 0.0, 1.0));
  }
`;

export const Particles: React.FC = () => {
  const frame = useCurrentFrame();
  const matRef = useRef<THREE.ShaderMaterial>(null);
  const { viewport } = useThree();

  const { geometry, uniforms } = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(N * 3);
    const spoke = new Float32Array(N);
    const offset = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      spoke[i] = Math.floor(i / PER_SPOKE);
      offset[i] = (i % PER_SPOKE) / PER_SPOKE;
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aSpoke", new THREE.BufferAttribute(spoke, 1));
    g.setAttribute("aOffset", new THREE.BufferAttribute(offset, 1));
    const u = {
      uPhase: { value: 0 },
      uNodes: { value: NODES.map((n) => new THREE.Vector2(n.pos[0], n.pos[1])) },
      uOut: { value: new THREE.Color(T.fgFaint) },
      uRet: { value: new THREE.Color(T.accent) },
    };
    return { geometry: g, uniforms: u };
  }, []);

  useFrame(() => {
    if (matRef.current) {
      matRef.current.uniforms.uPhase.value = (frame % LOOP_FRAMES) / LOOP_FRAMES;
    }
  });

  // scale points into the tight square that fills the frame
  const s = Math.min(viewport.width, viewport.height) * 0.46;

  return (
    <points geometry={geometry} scale={[s, s, 1]}>
      <shaderMaterial
        ref={matRef}
        vertexShader={vert}
        fragmentShader={frag}
        uniforms={uniforms}
        transparent
        blending={THREE.AdditiveBlending}
        depthWrite={false}
      />
    </points>
  );
};
