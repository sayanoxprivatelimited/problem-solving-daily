/**
 * A tiny wrapper around a Three.js scene.
 *
 * It exists so that four different 3D scenes do not each reinvent resizing,
 * pixel-ratio clamping, visibility pausing and reduced-motion handling.
 *
 * Behaviour worth knowing:
 * - rendering stops when the canvas is off screen (IntersectionObserver)
 * - device pixel ratio is capped at 2 (a 3x retina canvas is 9x the fragments)
 * - `prefers-reduced-motion` renders a single frame instead of an animation loop
 */
import * as THREE from 'three';

export function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function disposeObject(object) {
  object.traverse((child) => {
    if (child.geometry) child.geometry.dispose();
    const materials = Array.isArray(child.material) ? child.material : child.material ? [child.material] : [];
    for (const material of materials) {
      if (material.map) material.map.dispose();
      material.dispose();
    }
  });
}

/** Draw text onto a canvas texture; used for node labels and bar values. */
export function makeLabelTexture(text, { font = '600 44px ui-monospace, SFMono-Regular, Menlo, monospace', color = '#e8f0ff', padding = 18, background = 'rgba(6, 10, 22, 0.72)', border = null } = {}) {
  const measureCanvas = document.createElement('canvas');
  const measureContext = measureCanvas.getContext('2d');
  measureContext.font = font;
  const width = Math.ceil(measureContext.measureText(text).width) + padding * 2;
  const height = 44 + padding * 2;

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  context.font = font;
  context.textAlign = 'center';
  context.textBaseline = 'middle';

  if (background) {
    context.fillStyle = background;
    const radius = 14;
    context.beginPath();
    context.moveTo(radius, 0);
    context.arcTo(width, 0, width, height, radius);
    context.arcTo(width, height, 0, height, radius);
    context.arcTo(0, height, 0, 0, radius);
    context.arcTo(0, 0, width, 0, radius);
    context.closePath();
    context.fill();
  }
  if (border) {
    context.strokeStyle = border;
    context.lineWidth = 3;
    context.stroke();
  }

  context.fillStyle = color;
  context.fillText(text, width / 2, height / 2 + 2);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  texture.userData = { aspect: width / height };
  return texture;
}

export class Stage {
  constructor(canvas, { fov = 45, position = [0, 2.2, 8], lookAt = [0, 0, 0], antialias = true } = {}) {
    this.canvas = canvas;
    this.reducedMotion = prefersReducedMotion();
    this.clock = new THREE.Clock();
    this.updaters = [];
    this.visible = true;
    this.elapsed = 0;
    this.pointer = new THREE.Vector2(0, 0);
    this.pointerTarget = new THREE.Vector2(0, 0);

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias,
      alpha: true,
      powerPreference: 'high-performance',
    });
    // 2x retina triples the fragment work for a barely visible gain on these scenes.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.renderer.setClearAlpha(0);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(fov, 1, 0.1, 120);
    this.camera.position.set(...position);
    this.camera.lookAt(new THREE.Vector3(...lookAt));

    this._onPointerMove = (event) => {
      const rect = this.canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      this.pointerTarget.set(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -(((event.clientY - rect.top) / rect.height) * 2 - 1),
      );
    };
    window.addEventListener('pointermove', this._onPointerMove, { passive: true });

    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(canvas.parentElement || canvas);

    this._intersection = new IntersectionObserver(
      (entries) => {
        this.visible = entries.some((entry) => entry.isIntersecting);
        if (this.visible && this.reducedMotion) this.render();
      },
      { threshold: 0.01 },
    );
    this._intersection.observe(canvas);

    // A hidden tab should cost nothing: the animation loop is cancelled, not paused.
    this._onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(this._animation);
        this._animation = null;
      } else if (!this.reducedMotion && !this._animation) {
        this._frame();
      }
    };
    document.addEventListener('visibilitychange', this._onVisibility);

    this.resize();
    this._frame = this._frame.bind(this);
    if (!this.reducedMotion) this._frame();
  }

  resize() {
    const parent = this.canvas.parentElement || this.canvas;
    const width = Math.max(parent.clientWidth, 1);
    const height = Math.max(parent.clientHeight, 1);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    if (this.reducedMotion) this.render();
  }

  onUpdate(callback) {
    this.updaters.push(callback);
    return callback;
  }

  render() {
    this.renderer.render(this.scene, this.camera);
  }

  _frame() {
    this._animation = requestAnimationFrame(this._frame);
    const delta = Math.min(this.clock.getDelta(), 0.05);
    this.elapsed += delta;
    this.pointer.lerp(this.pointerTarget, 0.06);

    if (this.visible) {
      for (const update of this.updaters) update(delta, this.elapsed, this);
      this.render();
    }
  }

  dispose() {
    cancelAnimationFrame(this._animation);
    this._animation = null;
    document.removeEventListener('visibilitychange', this._onVisibility);
    this._resizeObserver.disconnect();
    this._intersection.disconnect();
    window.removeEventListener('pointermove', this._onPointerMove);
    disposeObject(this.scene);
    this.renderer.dispose();
  }
}

export function addLights(scene, { intensity = 1 } = {}) {
  scene.add(new THREE.AmbientLight(0x8899ff, 0.55 * intensity));

  const key = new THREE.PointLight(0x8b5cf6, 55 * intensity, 40);
  key.position.set(6, 8, 6);
  scene.add(key);

  const rim = new THREE.PointLight(0x22d3ee, 40 * intensity, 40);
  rim.position.set(-7, -4, -6);
  scene.add(rim);

  const fill = new THREE.DirectionalLight(0xffffff, 0.5 * intensity);
  fill.position.set(0, 10, 4);
  scene.add(fill);
}
