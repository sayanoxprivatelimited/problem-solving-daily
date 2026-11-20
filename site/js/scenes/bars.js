/**
 * Scene 3 — tests per problem, as a 3D bar chart you can spin.
 *
 * Height comes from `barLayout`, colour from the category. Every bar carries its
 * own value label, so the chart still communicates without hovering.
 */
import * as THREE from '../../assets/three.module.js';
import { Stage, addLights, makeLabelTexture } from '../engine.js';
import { barLayout } from '../layout.js';

export function createBarsScene(canvas, problems, { onHover } = {}) {
  const stage = new Stage(canvas, { fov: 40, position: [0, 4.8, 9.8], lookAt: [0, 1.4, 0] });
  addLights(stage.scene, { intensity: 0.9 });

  const world = new THREE.Group();
  stage.scene.add(world);

  const grid = new THREE.GridHelper(26, 26, 0x24304a, 0x151d2e);
  grid.position.y = 0;
  grid.material.transparent = true;
  grid.material.opacity = 0.55;
  world.add(grid);

  const bars = [];
  const pickables = [];

  for (const entry of barLayout(problems)) {
    const group = new THREE.Group();
    group.position.set(entry.x, 0, entry.z);

    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(entry.width, entry.height, entry.depth),
      new THREE.MeshStandardMaterial({
        color: entry.color,
        emissive: entry.color,
        emissiveIntensity: 0.22,
        roughness: 0.35,
        metalness: 0.4,
      }),
    );
    mesh.position.y = entry.height / 2;
    mesh.userData.entry = entry;
    group.add(mesh);
    pickables.push(mesh);

    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry),
      new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.22 }),
    );
    edges.position.copy(mesh.position);
    group.add(edges);

    const valueTexture = makeLabelTexture(String(entry.tests), {
      font: '700 46px ui-monospace, SFMono-Regular, Menlo, monospace',
      color: '#ffffff',
      background: 'rgba(8, 12, 24, 0.82)',
      border: `#${entry.color.toString(16).padStart(6, '0')}`,
    });
    const valueLabel = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: valueTexture, transparent: true, depthWrite: false }),
    );
    const labelHeight = 0.34;
    valueLabel.scale.set(labelHeight * (valueTexture.userData.aspect || 2), labelHeight, 1);
    valueLabel.position.set(0, entry.height + 0.42, 0);
    group.add(valueLabel);

    const indexTexture = makeLabelTexture(`${entry.index}`, {
      font: '600 34px ui-monospace, SFMono-Regular, Menlo, monospace',
      color: '#94a3b8',
      background: 'rgba(8, 12, 24, 0.55)',
    });
    const indexLabel = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: indexTexture, transparent: true, depthWrite: false }),
    );
    const indexHeight = 0.26;
    indexLabel.scale.set(indexHeight * (indexTexture.userData.aspect || 2), indexHeight, 1);
    indexLabel.position.set(0, -0.28, 0.55);
    group.add(indexLabel);

    world.add(group);
    bars.push({ entry, group, mesh });
  }

  // --- interaction ---------------------------------------------------------
  const raycaster = new THREE.Raycaster();
  let dragging = false;
  let lastX = 0;
  let hovered = null;

  canvas.addEventListener('pointerdown', (event) => {
    dragging = true;
    lastX = event.clientX;
    canvas.style.cursor = 'grabbing';
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    world.rotation.y += ((event.clientX - lastX) / Math.max(canvas.clientWidth, 1)) * 2.2;
    lastX = event.clientX;
  });
  const endDrag = () => {
    dragging = false;
    canvas.style.cursor = hovered ? 'pointer' : 'grab';
  };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('pointerleave', () => {
    endDrag();
    setHover(null);
  });

  function setHover(bar) {
    if (bar === hovered) return;
    hovered = bar;
    if (bar === null) {
      if (onHover) onHover(null);
      return;
    }
    if (!onHover) return;
    const rect = canvas.getBoundingClientRect();
    const point = new THREE.Vector3(bar.entry.x, bar.entry.height, 0)
      .applyMatrix4(world.matrixWorld)
      .project(stage.camera);
    onHover(bar.entry, {
      x: rect.left + ((point.x + 1) / 2) * rect.width,
      y: rect.top + ((1 - point.y) / 2) * rect.height,
    });
  }

  stage.onUpdate((delta, elapsed) => {
    if (!dragging) world.rotation.y += delta * 0.075;
    world.rotation.x = Math.sin(elapsed * 0.25) * 0.02;

    raycaster.setFromCamera(stage.pointer, stage.camera);
    const hits = raycaster.intersectObjects(pickables, false);
    const bar = hits.length ? bars.find((entry) => entry.mesh === hits[0].object) : null;
    setHover(bar ?? null);
    canvas.style.cursor = hovered ? 'pointer' : dragging ? 'grabbing' : 'grab';

    for (const item of bars) {
      const target = item === hovered ? 1.18 : 1;
      item.mesh.scale.y += (target - item.mesh.scale.y) * 0.15;
      item.mesh.position.y = (item.entry.height * item.mesh.scale.y) / 2;
      item.mesh.material.emissiveIntensity +=
        ((item === hovered ? 0.55 : 0.22) - item.mesh.material.emissiveIntensity) * 0.15;
    }
  });

  return {
    stage,
    dispose() {
      stage.dispose();
    },
  };
}
