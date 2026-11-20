/**
 * Scene 2 — the catalogue as a galaxy.
 *
 * One node per problem, placed by `galaxyPositions`. Categories own angular
 * sectors, so the legend reads clockwise and a filter dims a whole sector at once.
 * Hovering raises a tooltip; clicking selects the node and the matching card.
 */
import * as THREE from 'three';
import { Stage, addLights, makeLabelTexture } from '../engine.js';
import { categoryColor, galaxyPositions, prettyCategory, starField } from '../layout.js';

export function createGalaxyScene(canvas, problems, { onHover, onSelect, initialFilter } = {}) {
  const stage = new Stage(canvas, { fov: 42, position: [0, 6.4, 9.6], lookAt: [0, 0, 0] });
  addLights(stage.scene, { intensity: 0.8 });

  const world = new THREE.Group();
  stage.scene.add(world);

  // Sparks for depth.
  const starGeometry = new THREE.BufferGeometry();
  const stars = starField(520, { radius: 24, seed: 23 });
  starGeometry.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(stars.flatMap((star) => [star.x, star.y, star.z]), 3),
  );
  stage.scene.add(
    new THREE.Points(
      starGeometry,
      new THREE.PointsMaterial({ color: 0x8fa9ff, size: 0.05, transparent: true, opacity: 0.55 }),
    ),
  );

  // Faint rings to read the layout as a "sky" rather than floating dots.
  for (const [radius, opacity] of [
    [2.7, 0.18],
    [4.2, 0.12],
    [5.6, 0.08],
  ]) {
    const ring = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(
        Array.from({ length: 160 }, (_, index) => {
          const angle = (index / 160) * Math.PI * 2;
          return new THREE.Vector3(Math.cos(angle) * radius, -1.9, Math.sin(angle) * radius);
        }),
      ),
      new THREE.LineBasicMaterial({ color: 0x64748b, transparent: true, opacity }),
    );
    world.add(ring);
  }

  // One scratch vector for every per-frame scale write: allocating inside the
  // animation loop is what turns a smooth scene into a stuttery one after a minute.
  const scratchScale = new THREE.Vector3();

  const positions = galaxyPositions(problems);
  const nodeGeometry = new THREE.SphereGeometry(0.34, 28, 22);
  const nodes = [];
  const pickables = [];

  for (const problem of problems) {
    const placement = positions.find((entry) => entry.index === problem.index) ?? { x: 0, y: 0, z: 0 };
    const color = categoryColor(problem.category);
    const group = new THREE.Group();
    group.position.set(placement.x, placement.y, placement.z);

    const sphere = new THREE.Mesh(
      nodeGeometry,
      new THREE.MeshStandardMaterial({
        color,
        emissive: color,
        emissiveIntensity: 0.55,
        roughness: 0.25,
        metalness: 0.35,
        transparent: true,
        opacity: 1,
      }),
    );
    sphere.userData.problem = problem;
    group.add(sphere);
    pickables.push(sphere);

    const halo = new THREE.Mesh(
      new THREE.TorusGeometry(0.52, 0.012, 8, 64),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.45 }),
    );
    halo.rotation.x = Math.PI / 2.6;
    group.add(halo);

    const labelTexture = makeLabelTexture(prettyCategory(problem.category), {
      font: '600 34px ui-monospace, SFMono-Regular, Menlo, monospace',
      color: '#e8f0ff',
      background: 'rgba(8, 12, 24, 0.78)',
      border: `#${color.toString(16).padStart(6, '0')}`,
    });
    const label = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: labelTexture, transparent: true, depthWrite: false }),
    );
    const labelHeight = 0.3;
    label.scale.set(labelHeight * (labelTexture.userData.aspect || 3), labelHeight, 1);
    label.position.set(0, 0.78, 0);
    group.add(label);

    world.add(group);
    nodes.push({
      problem,
      group,
      sphere,
      halo,
      label,
      basePosition: group.position.clone(),
      baseScale: 1,
    });
  }

  // --- interaction ---------------------------------------------------------
  const raycaster = new THREE.Raycaster();
  let hovered = null;
  let dragging = false;
  let lastX = 0;
  let idleSpin = 1;
  // The scene can finish loading after the visitor already picked a filter.
  let filter = initialFilter ?? 'all';
  let focusedIndex = null;

  const setHover = (node) => {
    hovered = node;
    if (onHover) {
      if (!node) {
        onHover(null);
      } else {
        const rect = canvas.getBoundingClientRect();
        const point = node.group.position.clone().project(stage.camera);
        onHover(node.problem, {
          x: rect.left + ((point.x + 1) / 2) * rect.width,
          y: rect.top + ((1 - point.y) / 2) * rect.height,
        });
      }
    }
    setCursor(node ? 'pointer' : dragging ? 'grabbing' : 'grab');
  };

  canvas.addEventListener('pointerdown', (event) => {
    dragging = true;
    idleSpin = 0;
    lastX = event.clientX;
    setCursor('grabbing');
    canvas.setPointerCapture?.(event.pointerId);
  });

  canvas.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const delta = (event.clientX - lastX) / Math.max(canvas.clientWidth, 1);
    world.rotation.y += delta * 2.4;
    lastX = event.clientX;
  });

  // Writing `style.cursor` every frame invalidates style for no reason; only write
  // it when the value actually changes.
  let cursor = 'grab';
  const setCursor = (next) => {
    if (next === cursor) return;
    cursor = next;
    canvas.style.cursor = next;
  };
  canvas.style.cursor = cursor;

  const endDrag = () => {
    dragging = false;
    setCursor(hovered ? 'pointer' : 'grab');
  };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('pointerleave', () => {
    endDrag();
    if (hovered) setHover(null);
  });

  canvas.addEventListener('click', () => {
    if (hovered && onSelect) onSelect(hovered.problem);
  });

  // Touch users have no hover, so a tap selects the node under the finger.
  canvas.addEventListener('touchstart', () => {
    idleSpin = 0;
  }, { passive: true });

  stage.onUpdate((delta, elapsed) => {
    if (!dragging) {
      idleSpin = Math.min(idleSpin + delta * 0.25, 1);
      world.rotation.y += delta * 0.075 * idleSpin;
    }

    // Raycast against the spheres using the shared pointer position.
    raycaster.setFromCamera(stage.pointer, stage.camera);
    const hits = raycaster.intersectObjects(pickables, false);
    const node = hits.length ? nodes.find((entry) => entry.sphere === hits[0].object) : null;
    if (node !== hovered) setHover(node ?? null);

    for (const entry of nodes) {
      const dimmed = filter !== 'all' && entry.problem.category !== filter;
      const focused = entry.problem.index === focusedIndex;
      const targetScale = dimmed ? 0.6 : entry === hovered ? 1.45 : focused ? 1.32 : 1;
      const targetOpacity = dimmed ? 0.22 : 1;

      entry.group.scale.lerp(scratchScale.setScalar(targetScale), 0.12);
      entry.sphere.material.opacity += (targetOpacity - entry.sphere.material.opacity) * 0.12;
      entry.halo.rotation.z = elapsed * 0.4 + entry.problem.index;
      entry.label.material.opacity = dimmed ? 0.25 : 0.95;

      const float = Math.sin(elapsed * 0.9 + entry.problem.index) * 0.045;
      entry.group.position.y = entry.basePosition.y + float;
    }
  });

  return {
    stage,
    setFilter(nextFilter) {
      filter = nextFilter ?? 'all';
    },
    /** Highlight one node from the card list (hover or selection). */
    focus(index) {
      focusedIndex = index ?? null;
      if (stage.reducedMotion) stage.render();
    },
    dispose() {
      stage.dispose();
    },
    reducedMotion: stage.reducedMotion,
  };
}
