/**
 * Scene 4 — a slow wireframe icosahedron for the "how it works" section.
 *
 * Deliberately quiet: it is a section divider, not a feature. The point cloud on
 * the vertices makes the geometry readable without any lighting.
 */
import * as THREE from '../../assets/three.module.js';
import { Stage, makeLabelTexture, prefersReducedMotion } from '../engine.js';

export function createStructureScene(canvas, { labels = [] } = {}) {
  const stage = new Stage(canvas, { fov: 42, position: [0, 0.6, 7.6], lookAt: [0, 0, 0] });
  const reduced = prefersReducedMotion();

  const geometry = new THREE.IcosahedronGeometry(2.05, 1);
  const shell = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      color: 0x1f2a3d,
      wireframe: true,
      transparent: true,
      opacity: 0.85,
    }),
  );
  stage.scene.add(shell);

  const inner = new THREE.Mesh(
    new THREE.IcosahedronGeometry(1.28, 0),
    new THREE.MeshBasicMaterial({ color: 0x60a5fa, wireframe: true, transparent: true, opacity: 0.5 }),
  );
  stage.scene.add(inner);

  const vertices = new THREE.Points(
    geometry,
    new THREE.PointsMaterial({ color: 0xa5b4fc, size: 0.075, transparent: true, opacity: 0.9 }),
  );
  stage.scene.add(vertices);

  // The three steps of the workflow, pinned around the shell.
  const nodes = [];
  labels.slice(0, 3).forEach((text, index) => {
    const texture = makeLabelTexture(text, {
      font: '600 32px ui-monospace, SFMono-Regular, Menlo, monospace',
      color: '#e2e8f5',
      background: 'rgba(8, 12, 24, 0.8)',
      border: ['#8b5cf6', '#22d3ee', '#34d399'][index] ?? '#64748b',
    });
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }),
    );
    const height = 0.3;
    sprite.scale.set(height * (texture.userData.aspect || 4), height, 1);
    const angle = (index / Math.max(labels.length, 1)) * Math.PI * 2 - Math.PI / 2;
    sprite.position.set(Math.cos(angle) * 2.55, Math.sin(angle) * 1.25, 0.6);
    stage.scene.add(sprite);
    nodes.push({ sprite, angle, baseY: sprite.position.y });
  });

  if (!reduced) {
    stage.onUpdate((delta, elapsed) => {
      shell.rotation.y += delta * 0.16;
      shell.rotation.x = Math.sin(elapsed * 0.3) * 0.16;
      inner.rotation.y -= delta * 0.28;
      inner.rotation.z += delta * 0.12;
      vertices.rotation.copy(shell.rotation);

      stage.camera.position.x += (stage.pointer.x * 0.9 - stage.camera.position.x) * 0.04;
      stage.camera.position.y += (0.6 + stage.pointer.y * 0.4 - stage.camera.position.y) * 0.04;
      stage.camera.lookAt(0, 0, 0);

      for (const node of nodes) {
        node.sprite.position.y = node.baseY + Math.sin(elapsed * 0.8 + node.angle) * 0.12;
      }
    });
  } else {
    shell.rotation.set(0.3, 0.6, 0);
    stage.render();
  }

  return {
    stage,
    dispose() {
      stage.dispose();
    },
  };
}
