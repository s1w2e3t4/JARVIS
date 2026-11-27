
import { useEffect, useRef } from "react";
import * as THREE from "three";

const vertexShader = `
  varying vec2 vUv;

  void main() {
    vUv = uv;
    gl_Position = vec4(position, 1.0);
  }
`;

const fragmentShader = `
  precision highp float;

  uniform float uTime;
  uniform vec2 uResolution;
  uniform float uEnergy;
  uniform float uScale;

  varying vec2 vUv;

  float hash(vec2 p) {
    return fract(
      sin(dot(p, vec2(127.1, 311.7))) * 43758.5453
    );
  }

  float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);

    return mix(
      mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
      mix(
        hash(i + vec2(0.0, 1.0)),
        hash(i + vec2(1.0, 1.0)),
        u.x
      ),
      u.y
    );
  }

  float fbm(vec2 p) {
    float value = 0.0;
    float amplitude = 0.5;

    for (int i = 0; i < 5; i++) {
      value += amplitude * noise(p);
      p = p * 2.03 + 13.7;
      amplitude *= 0.5;
    }

    return value;
  }

  vec3 diskColor(vec3 p) {
    float r = length(p.xz);
    float a = atan(p.z, p.x);

    float inner = smoothstep(1.65, 2.1, r);
    float outer = 1.0 - smoothstep(4.0, 7.5, r);

    float angle = a - uTime * 0.12 / sqrt(r);

    vec2 textureUV = vec2(
      r * 9.0,
      angle * 3.0 + log(r) * 6.0
    );

    float turbulence = fbm(textureUV);
    float filaments = fbm(
      vec2(r * 24.0, angle * 2.0 + turbulence)
    );

    float density = inner * outer;
    density *= pow(turbulence, 1.7) * 2.6;
    density *= 0.5 + filaments;

    float heat = exp(-max(r - 2.0, 0.0) * 0.65);

    vec3 cold = vec3(0.025, 0.07, 0.23);
    vec3 blue = vec3(0.12, 0.38, 1.0);
    vec3 white = vec3(0.82, 0.92, 1.0);

    vec3 color = mix(cold, blue, heat);
    color = mix(color, white, pow(heat, 3.0));

    float doppler = 0.65 + 0.55 *
      pow(0.5 + 0.5 * sin(a), 2.0);

    return color * density * doppler * 2.4;
  }

  void main() {
    vec2 uv = (vUv - 0.5) * 2.0;

    uv.x *= uResolution.x / uResolution.y;
    uv /= uScale;

    float tilt = 0.40;

    uv = mat2(
      cos(tilt), -sin(tilt),
      sin(tilt), cos(tilt)
    ) * uv;

    float orbit = uTime * 0.35;

vec3 camera = vec3(
  sin(orbit) * 14.0,
  3.1,
  cos(orbit) * 14.0
);
    vec3 forward = normalize(-camera);
    vec3 right = normalize(
      cross(forward, vec3(0.0, 1.0, 0.0))
    );
    vec3 up = cross(right, forward);

    vec3 ray = normalize(
      forward +
      right * uv.x * 0.54 +
      up * uv.y * 0.54
    );

    vec3 p = camera;
    vec3 color = vec3(0.0);

    float transmission = 1.0;

    // Numerical approximation of curved light paths.
    for (int i = 0; i < 120; i++) {
      float r = length(p);

      if (r < 1.0) {
        break;
      }

      if (r > 24.0) {
        break;
      }

      float stepSize = clamp(r * 0.065, 0.025, 0.65);

      vec3 old = p;

      vec3 gravity = -p / pow(r, 3.0);

      ray = normalize(
        ray + gravity * stepSize * 1.55
      );

      p += ray * stepSize;

      // Light emitted by the accretion disk.
      if (old.y * p.y < 0.0) {
        float t = old.y / (old.y - p.y);
        vec3 hit = mix(old, p, t);

        float diskRadius = length(hit.xz);

        if (diskRadius > 1.65 &&
            diskRadius < 7.5) {

          vec3 emission = diskColor(hit);

          color += emission * transmission;
          transmission *= 0.48;
        }
      }

      // Thin luminous region around the black hole.
      float ring = exp(
        -pow((r - 1.48) * 7.0, 2.0)
      );

      color += vec3(0.06, 0.20, 0.65)
        * ring * stepSize * 0.12
        * transmission;

      // Diffuse surrounding cosmic matter.
      float diskDistance = abs(p.y);
      float diskRadius = length(p.xz);

      float atmosphere =
        exp(-diskDistance * 4.0) *
        exp(-pow((diskRadius - 3.8) * 0.4, 2.0));

      float dust = fbm(
        vec2(
          diskRadius * 4.0,
          atan(p.z, p.x) * 3.0 - uTime * 0.08
        )
      );

      color += vec3(0.015, 0.035, 0.12)
        * atmosphere * dust
        * stepSize * transmission;
    }

    color *= uEnergy;

    // Cinematic exposure and tone mapping.
    color = 1.0 - exp(-color * 1.7);
    color = pow(color, vec3(0.85));

    // Fade the outer edges into pure black.
    float vignette = 1.0 -
      smoothstep(0.65, 1.65, length(uv));

    color *= vignette;

    gl_FragColor = vec4(color, 1.0);
  }
`;

export default function CosmicHorizon({
  mode = "idle",
}) {
  const containerRef = useRef(null);
  const modeRef = useRef(mode);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  useEffect(() => {
    const container = containerRef.current;

    if (!container) return;

    let renderer;

    try {
      renderer = new THREE.WebGLRenderer({
        antialias: false,
        alpha: false,
        powerPreference: "high-performance",
      });
    } catch {
      container.textContent =
        "WebGL rendering is unavailable.";
      return;
    }

    renderer.setPixelRatio(
      Math.min(window.devicePixelRatio, 1.25)
    );

    renderer.setClearColor(0x000000);

    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.style.display = "block";

    container.appendChild(renderer.domElement);

    const scene = new THREE.Scene();

    const camera = new THREE.OrthographicCamera(
      -1, 1, 1, -1, 0, 1
    );

    const uniforms = {
      uTime: { value: 0 },
      uResolution: {
        value: new THREE.Vector2(1, 1),
      },
      uEnergy: { value: 1 },
      uScale: { value: 1 },
    };

    const material = new THREE.ShaderMaterial({
      uniforms,
      vertexShader,
      fragmentShader,
      depthTest: false,
      depthWrite: false,
    });

    const geometry = new THREE.PlaneGeometry(2, 2);

    const mesh = new THREE.Mesh(geometry, material);

    mesh.frustumCulled = false;

    scene.add(mesh);

    function resize() {
      const width = Math.max(container.clientWidth, 1);
      const height = Math.max(container.clientHeight, 1);

      renderer.setSize(width, height, false);

      renderer.getDrawingBufferSize(
        uniforms.uResolution.value
      );
    }

    const observer = new ResizeObserver(resize);

    observer.observe(container);

    resize();

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    );

    

    let frame;
    let previous = performance.now();
    let simulationTime = 0;
    let stateTime = 0;

    let currentSpeed = 0.45;
    let currentScale = 1;
    let currentEnergy = 1;
    let lastMode = "idle";

    function animate(now) {
      const dt = Math.min(
        (now - previous) / 1000,
        0.05
      );

      previous = now;

      const currentMode = modeRef.current;

      if (currentMode !== lastMode) {
        stateTime = 0;
        lastMode = currentMode;
      }

      stateTime += dt;

      const idleSpeed = 0.45;

      let targetSpeed = idleSpeed;
      let targetScale = 1;
      let targetEnergy = 1;

      switch (currentMode) {

        case "listening": {
          targetSpeed = idleSpeed * 1.30;

          const phase =
            (1 - Math.cos(
              stateTime * Math.PI * 2 / 5
            )) / 2;

          const eased =
            phase * phase * (3 - 2 * phase);

          targetScale = 1 - 0.20 * eased;

          targetEnergy = 1 + 0.10 * eased;

          break;
        }

        case "thinking":
          targetSpeed = idleSpeed * 1.60;
          targetScale = 1;
          targetEnergy = 1.20;
          break;

        case "speaking":
          targetSpeed = -idleSpeed * 1.30;
          targetScale = 1;
          targetEnergy = 1.10;
          break;

        case "executing":
          targetSpeed = idleSpeed * 1.80;
          targetScale = 1;
          targetEnergy = 1.25;
          break;

        default:
          targetSpeed = idleSpeed;
          targetScale = 1;
          targetEnergy = 1;
      }

      if (reducedMotion.matches) {
        targetSpeed = 0;
        targetScale = 1;
        targetEnergy = 1;
      }

      const speedEase =
        1 - Math.exp(-4.0 * dt);

      const scaleEase =
        1 - Math.exp(-7.0 * dt);

      const energyEase =
        1 - Math.exp(-3.0 * dt);

      currentSpeed +=
        (targetSpeed - currentSpeed) * speedEase;

      currentScale +=
        (targetScale - currentScale) * scaleEase;

      currentEnergy +=
        (targetEnergy - currentEnergy) * energyEase;

      simulationTime += dt * currentSpeed;

      uniforms.uTime.value = simulationTime;
      uniforms.uScale.value = currentScale;
      uniforms.uEnergy.value = currentEnergy;

      renderer.render(scene, camera);

      frame = requestAnimationFrame(animate);
    }

    frame = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();

      geometry.dispose();
      material.dispose();
      renderer.dispose();

      renderer.domElement.remove();
    };
  }, []);

  return (
    <div
      ref={containerRef}
      aria-hidden="true"
      style={{
        width: "100%",
        height: "100%",
        minHeight: "320px",
        background: "#000000",
        overflow: "hidden",
        pointerEvents: "none",
      }}
    />
  );
}
