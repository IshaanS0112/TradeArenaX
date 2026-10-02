import { OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import type { LiquiditySurface } from "../api/types";
import type { ModuleData } from "./types";
import { AxisNote, Button, EmptyState, ErrorState, Skeleton } from "../design/primitives";
import { BookHeatmap } from "./BookHeatmap";

/** The order book as terrain: price x time x resting quantity. */

const VERTEX = /* glsl */ `
  uniform sampler2D uData;
  uniform float uHeight;
  varying float vSigned;
  varying float vMagnitude;

  void main() {
    vec4 cell = texture2D(uData, uv);
    vSigned = cell.g;              // 1.0 bid, -1.0 ask, 0.0 empty
    vMagnitude = cell.r;           // quantity, normalised to the run's peak
    vec3 displaced = position;
    displaced.z += vMagnitude * uHeight;   // plane is rotated, so z is height
    gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
  }
`;

const FRAGMENT = /* glsl */ `
  uniform vec3 uBid;
  uniform vec3 uAsk;
  uniform vec3 uFloor;
  varying float vSigned;
  varying float vMagnitude;

  void main() {
    if (vSigned == 0.0) {
      gl_FragColor = vec4(uFloor, 1.0);
      return;
    }
    vec3 base = vSigned > 0.0 ? uBid : uAsk;
    // sqrt ramp: depth is skewed, and a linear ramp renders everything but the deepest level.
    float intensity = clamp(sqrt(vMagnitude), 0.12, 1.0);
    gl_FragColor = vec4(mix(uFloor, base, intensity), 1.0);
  }
`;

function SurfaceMesh({ surface, height }: { surface: LiquiditySurface; height: number }) {
  const invalidate = useThree((state) => state.invalidate);
  const gl = useThree((state) => state.gl);
  const scene = useThree((state) => state.scene);
  const camera = useThree((state) => state.camera);
  const frames = surface.steps.length;
  const rows = surface.width;

  const texture = useMemo(() => {
    let peak = 0;
    for (const v of surface.grid) peak = Math.max(peak, Math.abs(v));
    peak = peak || 1;

    const pixels = new Float32Array(frames * rows * 4);
    for (let t = 0; t < frames; t += 1) {
      for (let j = 0; j < rows; j += 1) {
        const value = surface.grid[t * rows + j] ?? 0;
        const index = (j * frames + t) * 4;
        pixels[index] = Math.abs(value) / peak;
        pixels[index + 1] = value === 0 ? 0 : Math.sign(value);
        pixels[index + 2] = 0;
        pixels[index + 3] = 1;
      }
    }
    const tex = new THREE.DataTexture(pixels, frames, rows, THREE.RGBAFormat, THREE.FloatType);
    tex.minFilter = THREE.NearestFilter;
    tex.magFilter = THREE.NearestFilter;
    tex.needsUpdate = true;
    return tex;
  }, [surface, frames, rows]);

  const uniforms = useMemo(
    () => ({
      uData: { value: texture },
      uHeight: { value: height },
      uBid: { value: new THREE.Color("#26d07c") },
      uAsk: { value: new THREE.Color("#f2544b") },
      uFloor: { value: new THREE.Color("#0e121a") },
    }),
    [texture, height],
  );

  // frameloop="demand" draws nothing unless something asks for a frame, and the data arrives after.
  useEffect(() => {
    uniforms.uHeight.value = height;
    let frame = 0;
    let handle = 0;
    const paint = () => {
      invalidate();
      // invalidate() alone is not enough on the first mount: under StrictMode the canvas mounts.
      gl.render(scene, camera);
      frame += 1;
      if (frame < 3) handle = requestAnimationFrame(paint);
    };
    paint();
    return () => cancelAnimationFrame(handle);
  }, [height, uniforms, invalidate, texture, gl, scene, camera]);

  // Leaked GPU memory across panel open/close is the most common r3f bug, and a DataTexture.
  useEffect(() => () => texture.dispose(), [texture]);

  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[3.2, 2.1, Math.min(frames - 1, 511), Math.min(rows - 1, 255)]} />
      <shaderMaterial
        vertexShader={VERTEX}
        fragmentShader={FRAGMENT}
        uniforms={uniforms}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

function Markers({ surface, step }: { surface: LiquiditySurface; step: number }) {
  const first = surface.steps[0] ?? 0;
  const last = surface.steps[surface.steps.length - 1] ?? first;
  const span = Math.max(1, last - first);
  const x = (s: number) => ((s - first) / span - 0.5) * 3.2;

  return (
    <group>
      {surface.shock_steps.map((s) => (
        <mesh key={s} position={[x(s), 0.25, 0]}>
          <boxGeometry args={[0.008, 0.5, 2.1]} />
          <meshBasicMaterial color="#f5b544" transparent opacity={0.22} />
        </mesh>
      ))}
      <mesh position={[x(step), 0.25, 0]}>
        <boxGeometry args={[0.006, 0.5, 2.1]} />
        <meshBasicMaterial color="#38bdf8" transparent opacity={0.4} />
      </mesh>
    </group>
  );
}

/** Ask for a frame whenever something that affects the picture changes. */
function Repaint({ step, height }: { step: number; height: number }) {
  const invalidate = useThree((state) => state.invalidate);
  const advance = useThree((state) => state.advance);
  const width = useThree((state) => state.size.width);
  const canvasHeight = useThree((state) => state.size.height);

  useEffect(() => {
    invalidate();
    // advance() runs one full frame through r3f's own pipeline.
    let handle = 0;
    let frame = 0;
    const paint = () => {
      advance(performance.now());
      frame += 1;
      if (frame < 3) handle = requestAnimationFrame(paint);
    };
    paint();
    return () => cancelAnimationFrame(handle);
  }, [invalidate, advance, width, canvasHeight, step, height]);

  return null;
}

function supportsWebGL2(): boolean {
  try {
    return !!document.createElement("canvas").getContext("webgl2");
  } catch {
    return false;
  }
}

export default function LiquiditySurface3D({ data }: { data: ModuleData }) {
  const [height, setHeight] = useState(0.9);
  const [webgl] = useState(supportsWebGL2);
  const reducedMotion = useRef(
    typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  if (data.errors.surface) {
    return <ErrorState endpoint={data.endpoints.surface} message={data.errors.surface} />;
  }
  if (data.loading.surface && !data.surface) return <Skeleton lines={8} />;
  const surface = data.surface;
  if (!surface || surface.steps.length < 2) {
    return <EmptyState>Not enough book history to build a surface.</EmptyState>;
  }

  if (!webgl) {
    // A blank canvas is the worst possible fallback: it looks like a bug in the data rather.
    return (
      <div className="flex h-full flex-col gap-2">
        <p className="rounded-input border border-warn/40 bg-warn/10 p-2 text-2xs text-warn">
          WebGL2 is unavailable in this browser, so the surface is shown as the 2D
          heatmap of the same data.
        </p>
        <div className="min-h-0 flex-1">
          <BookHeatmap data={data} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-2xs text-muted">
        <span>Height</span>
        <input
          type="range"
          min={0.2}
          max={2}
          step={0.1}
          value={height}
          onChange={(e) => setHeight(Number(e.target.value))}
          className="h-1 w-28 accent-[color:var(--accent)]"
          aria-label="Surface height scale"
        />
        <Button variant="quiet" onClick={() => setHeight(0.9)}>
          reset
        </Button>
        <span className="ml-auto">drag to orbit · scroll to zoom</span>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-input border border-edge bg-base">
        <Canvas
          frameloop="demand"
          dpr={[1, 2]}
          camera={{ position: [2.4, 1.9, 2.4], fov: 45 }}
          gl={{ antialias: true }}
          onCreated={(state) => state.invalidate()}
        >
          <color attach="background" args={["#07090d"]} />
          <ambientLight intensity={0.6} />
          <directionalLight position={[3, 5, 2]} intensity={0.7} />
          <gridHelper args={[3.6, 12, "#1e2634", "#151b26"]} position={[0, -0.001, 0]} />
          <SurfaceMesh surface={surface} height={height} />
          <Repaint step={data.step} height={height} />
          <Markers surface={surface} step={data.step} />
          <OrbitControls
            makeDefault
            enableDamping={!reducedMotion.current}
            autoRotate={false}
            minDistance={1.4}
            maxDistance={7}
          />
        </Canvas>
      </div>

      <AxisNote>
        x: time, step {surface.steps[0] ?? 0}–
        {surface.steps[surface.steps.length - 1] ?? 0}. z: price
        relative to mid, ±{surface.levels} ticks. Height: resting quantity. Amber slab is
        the shock, cyan the scrubbed step.
      </AxisNote>
    </div>
  );
}
