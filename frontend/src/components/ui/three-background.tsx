"use client";

/**
 * A slow, soft two-tone nebula drawn with WebGL, used as the ambient backdrop
 * behind the marketing hero.
 *
 * Everything is procedural: a few octaves of drifting value noise tinted from
 * the inner colour to the outer one and vignetted so the headline still reads.
 * There are no textures and no external assets, so the effect costs one canvas
 * and one draw call.
 *
 * The backdrop is purely decorative, so it is written to fail quietly rather
 * than take the page down with it:
 *
 * - WebGL unavailable -> nothing is rendered and a single warning is logged;
 * - the tab is hidden -> the animation loop is paused;
 * - `prefers-reduced-motion: reduce` -> one static frame is drawn, no loop;
 * - the drawing buffer is capped so 4K screens do not allocate huge buffers.
 */

import React, { useEffect, useRef } from "react";
import * as THREE from "three";

import { cn } from "@/lib/utils";

/** Inner (violet) and outer (cyan) glow, in that order. */
const DEFAULT_COLORS: [string, string] = ["#7c3aed", "#22d3ee"];
const DEFAULT_INTENSITY = 0.55;
/** Slow on purpose: the nebula should feel like it is barely moving. */
const DEFAULT_SPEED = 0.35;
/** Longest edge of the drawing buffer. The nebula is soft and low-frequency,
 *  so a smaller buffer scaled up is invisible and roughly halves the GPU cost. */
const MAX_CANVAS_EDGE = 256;

/** The backdrop is ambient; 20 fps is indistinguishable from 60 for it. */
const FRAME_INTERVAL_MS = 1000 / 8;

/** The slice of a 2D vector the uniforms need. */
type Vector2Like = { set(x: number, y: number): void };
/** The slice of a 3D vector the uniforms need. */
type Vector3Like = { set(x: number, y: number, z: number): void };

/** Uniforms driven by this component. */
type NebulaUniforms = {
  uTime: { value: number };
  uResolution: { value: Vector2Like };
  uColorA: { value: Vector3Like };
  uColorB: { value: Vector3Like };
  uIntensity: { value: number };
};

/** The material handle kept around so prop changes can update the uniforms. */
type NebulaMaterial = { uniforms: NebulaUniforms; dispose(): void };

export type ThreeBackgroundProps = {
  /** Extra classes on the container. */
  className?: string;
  /** 0..1 intensity of the nebula. Default 0.55. */
  intensity?: number;
  /** [inner, outer] glow colours as hex strings. Default ["#7c3aed", "#22d3ee"] (violet → cyan). */
  colors?: [string, string];
  /** Animation speed multiplier. Default 0.35 (slow, premium). */
  speed?: number;
};

const VERTEX_SHADER = /* glsl */ `
varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = vec4(position, 1.0);
}
`;

const FRAGMENT_SHADER = /* glsl */ `
precision highp float;

uniform float uTime;
uniform vec2 uResolution;
uniform vec3 uColorA;
uniform vec3 uColorB;
uniform float uIntensity;

varying vec2 vUv;

// Cheap 2D hash -> value noise -> fbm. No textures, no dependencies.
float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float value = 0.0;
  float amplitude = 0.5;
  for (int i = 0; i < 2; i++) {
    value += amplitude * noise(p);
    p *= 2.0;
    amplitude *= 0.5;
  }
  return value;
}

void main() {
  vec2 uv = vUv;
  float aspect = uResolution.x / max(uResolution.y, 1.0);
  vec2 p = (uv - 0.5) * vec2(aspect, 1.0);

  // Slow drift; the fields are advected rather than scrolled.
  float t = uTime * 0.18;

  float base = fbm(p * 1.6 + vec2(t, -t * 0.7));
  float mid = fbm(p * 3.2 + vec2(-t * 0.6, t * 0.5) + base * 0.7);

  float field = base * 0.68 + mid * 0.32;
  float clouds = smoothstep(0.3, 0.86, field);

  // Inner colour (uColorA) in the dense core, outer colour (uColorB) in the
  // thin haze that surrounds it.
  vec3 color = mix(uColorB, uColorA, smoothstep(0.25, 0.85, field));

  // Vignette: dim the corners so the hero copy always stays legible.
  float vignette = 1.0 - smoothstep(0.3, 1.25, length(p * vec2(0.72, 1.0)));

  gl_FragColor = vec4(color, clouds * vignette * uIntensity);
}
`;

/** Parse `#rgb` / `#rrggbb` into normalised sRGB components. */
function hexToRgb(hex: string): [number, number, number] {
  const raw = hex.trim().replace(/^#/, "");
  const full =
    raw.length === 3
      ? raw
          .split("")
          .map((c) => c + c)
          .join("")
      : raw.slice(0, 6);
  const value = Number.parseInt(full, 16);
  if (!Number.isFinite(value)) return [1, 1, 1];
  return [
    ((value >> 16) & 255) / 255,
    ((value >> 8) & 255) / 255,
    (value & 255) / 255,
  ];
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(Math.max(value, 0), 1);
}

export function ThreeBackground({
  className,
  intensity = DEFAULT_INTENSITY,
  colors = DEFAULT_COLORS,
  speed = DEFAULT_SPEED,
}: ThreeBackgroundProps): React.ReactElement {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const materialRef = useRef<NebulaMaterial | null>(null);
  /** Latest props, so the render loop and the uniforms stay in sync. */
  const propsRef = useRef({
    intensity,
    colorInner: colors[0],
    colorOuter: colors[1],
    speed,
  });

  // Create the renderer once. The dependency list stays empty on purpose:
  // prop changes are pushed into the existing uniforms instead of rebuilding
  // the whole WebGL context.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: false,
        powerPreference: "low-power",
      });
    } catch (error) {
      console.warn(
        "[three-background] WebGL is unavailable, skipping the nebula backdrop.",
        error,
      );
      return;
    }

    renderer.setPixelRatio(1);
    renderer.setClearColor(0x000000, 0);

    const canvas = renderer.domElement;
    canvas.className = "h-full w-full";
    container.appendChild(canvas);

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    const geometry = new THREE.PlaneGeometry(2, 2);

    const initial = propsRef.current;
    const uniforms: NebulaUniforms = {
      uTime: { value: 0 },
      uResolution: { value: new THREE.Vector2(1, 1) },
      uColorA: { value: new THREE.Vector3(...hexToRgb(initial.colorInner)) },
      uColorB: { value: new THREE.Vector3(...hexToRgb(initial.colorOuter)) },
      uIntensity: { value: clamp01(initial.intensity) },
    };

    const material: NebulaMaterial = new THREE.ShaderMaterial({
      uniforms,
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      depthTest: false,
    });

    // The quad is drawn straight to clip space, so it must never be culled.
    const mesh = new THREE.Mesh(geometry, material);
    mesh.frustumCulled = false;
    scene.add(mesh);
    materialRef.current = material;

    // Fit the drawing buffer to the container, capped so 4K stays sane.
    const resize = () => {
      const width = Math.max(1, container.clientWidth);
      const height = Math.max(1, container.clientHeight);

      let targetWidth = width;
      let targetHeight = height;
      const longest = Math.max(targetWidth, targetHeight);
      if (longest > MAX_CANVAS_EDGE) {
        const scale = MAX_CANVAS_EDGE / longest;
        targetWidth = Math.floor(targetWidth * scale);
        targetHeight = Math.floor(targetHeight * scale);
      }

      renderer.setSize(targetWidth, targetHeight, false);
      uniforms.uResolution.value.set(
        renderer.domElement.width,
        renderer.domElement.height,
      );
    };

    resize();
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);

    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    let prefersReducedMotion = motionQuery.matches;

    let rafId = 0;
    let running = false;
    let elapsed = 0;
    let lastTime = 0;
    let lastRender = 0;

    const tick = (now: number) => {
      // Bail if the loop was stopped between frames.
      if (!running) return;
      rafId = requestAnimationFrame(tick);

      // Throttle: the nebula drifts so slowly that rendering every frame is
      // wasted work, and on a laptop that work competes with the page.
      if (now - lastRender < FRAME_INTERVAL_MS) return;
      lastRender = now;

      const delta = Math.min((now - lastTime) / 1000, 0.1);
      lastTime = now;
      elapsed += delta * propsRef.current.speed;
      uniforms.uTime.value = elapsed;
      renderer.render(scene, camera);
    };

    const start = () => {
      if (running || prefersReducedMotion || document.hidden) return;
      running = true;
      lastTime = performance.now();
      lastRender = 0;
      rafId = requestAnimationFrame(tick);
    };

    const stop = () => {
      running = false;
      if (rafId) {
        cancelAnimationFrame(rafId);
        rafId = 0;
      }
    };

    const onMotionChange = (event: MediaQueryListEvent) => {
      prefersReducedMotion = event.matches;
      if (prefersReducedMotion) {
        stop();
        renderer.render(scene, camera);
      } else {
        start();
      }
    };

    const onVisibilityChange = () => {
      if (document.hidden) {
        stop();
      } else {
        start();
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    motionQuery.addEventListener("change", onMotionChange);

    if (prefersReducedMotion) {
      // One static frame, no animation.
      renderer.render(scene, camera);
    } else {
      start();
    }

    return () => {
      stop();
      resizeObserver.disconnect();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      motionQuery.removeEventListener("change", onMotionChange);
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
      materialRef.current = null;
    };
  }, []);

  // Push prop changes into the live uniforms instead of rebuilding the scene.
  // Depend on the individual colour values so an inline `colors={[...]}` array
  // does not re-run this effect on every parent render.
  const colorInner = colors[0];
  const colorOuter = colors[1];

  useEffect(() => {
    propsRef.current = { intensity, colorInner, colorOuter, speed };

    const material = materialRef.current;
    if (!material) return;
    material.uniforms.uIntensity.value = clamp01(intensity);
    material.uniforms.uColorA.value.set(...hexToRgb(colorInner));
    material.uniforms.uColorB.value.set(...hexToRgb(colorOuter));
  }, [intensity, colorInner, colorOuter, speed]);

  return (
    <div
      ref={containerRef}
      data-testid="three-background"
      aria-hidden="true"
      className={cn(
        "pointer-events-none absolute inset-0 -z-10 overflow-hidden",
        className,
      )}
    />
  );
}
