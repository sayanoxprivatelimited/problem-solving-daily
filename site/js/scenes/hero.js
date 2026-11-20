/**
 * Scene 1 — "token stream".
 *
 * A tunnel of instanced cubes flying towards the camera, one colour per category.
 * It reads as "streams of problems flowing past", and it costs one draw call.
 */
import * as THREE from '../../assets/three.module.js';
import { Stage, addLights } from '../engine.js';
import { categoryColor, starField } from '../layout.js';

const COUNT = 260;
const TUNNEL_DEPTH = 34;

export function createHeroScene(canvas, problems) {
  const stage = new Stage(canvas, { fov: 54, position: [0, 1.1, 9] });
  addLights(stage.scene, { intensity: 0.85 });

  const palette = problems.map((problem) => new THREE.Color(categoryColor(problem.category)));
  const geometry = new THREE.BoxGeometry(0.12, 0.12, 0.12);
  const material = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.32,
    metalness: 0.45,
    emissive: 0x0b1020,
    emissiveIntensity: 0.8,
  });

  const mesh = new THREE.InstancedMesh(geometry, material, COUNT);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);

  // Deterministic pseudo-random so the scene looks identical on every reload.
  let seed = 20261120;
  const random = () => {
    seed ^= seed << 13;
    seed ^= seed >>> 17;
    seed ^= seed << 5;
    seed >>>= 0;
    return seed / 0xffffffff;
  };

  const particles = Array.from({ length: COUNT }, (_, index) => {
    const angle = random() * Math.PI * 2;
    const radius = 1.7 + random() * 3.9;
    const color = palette.length ? palette[index % palette.length] : new THREE.Color(0x94a3b8);
    mesh.setColorAt(index, color);
    return {
      angle,
      radius,
      z: -TUNNEL_DEPTH + random() * TUNNEL_DEPTH,
      speed: 1.4 + random() * 2.6,
      spin: random() * Math.PI * 2,
      spinRate: (random() - 0.5) * 1.4,
      scale: 0.7 + random() * 1.5,
    };
  });
  mesh.instanceColor.needsUpdate = true;
  stage.scene.add(mesh);

  // Background sparks, pushed far away so they read as depth rather than clutter.
  const starGeometry = new THREE.BufferGeometry();
  const stars = starField(420, { radius: 30, seed: 11 });
  starGeometry.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(stars.flatMap((star) => [star.x, star.y, star.z]), 3),
  );
  const starsMesh = new THREE.Points(
    starGeometry,
    new THREE.PointsMaterial({ color: 0x93b4ff, size: 0.06, transparent: true, opacity: 0.65 }),
  );
  stage.scene.add(starsMesh);

  // Two wireframe rings that hint at the "catalogue" buried inside the stream.
  const ringGroup = new THREE.Group();
  for (const [radius, opacity] of [
    [2.9, 0.18],
    [4.1, 0.1],
  ]) {
    const ring = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(
        Array.from({ length: 120 }, (_, index) => {
          const angle = (index / 120) * Math.PI * 2;
          return new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius, -6);
        }),
      ),
      new THREE.LineBasicMaterial({ color: 0x8b5cf6, transparent: true, opacity }),
    );
    ringGroup.add(ring);
  }
  stage.scene.add(ringGroup);

  const dummy = new THREE.Object3D();

  stage.onUpdate((delta, elapsed) => {
    for (let index = 0; index < particles.length; index += 1) {
      const particle = particles[index];
      particle.z += particle.speed * delta * 3.2;
      particle.spin += particle.spinRate * delta;
      if (particle.z > 7) {
        particle.z -= TUNNEL_DEPTH;
      }

      // Slight inward drift keeps the tunnel from feeling like a flat wall.
      const radius = particle.radius * (1 - 0.02 * Math.sin(elapsed * 0.6 + particle.angle));
      dummy.position.set(
        Math.cos(particle.angle + elapsed * 0.05) * radius,
        Math.sin(particle.angle + elapsed * 0.05) * radius * 0.68,
        particle.z,
      );
      dummy.rotation.set(particle.spin, particle.spin * 0.7, particle.spin * 0.4);
      dummy.scale.setScalar(particle.scale);
      dummy.updateMatrix();
      mesh.setMatrixAt(index, dummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;

    cameraFollow(stage, elapsed);
    starsMesh.rotation.y = elapsed * 0.012;
    ringGroup.rotation.z = elapsed * 0.06;
  });

  // One frame for reduced-motion visitors, so they still see the composition.
  if (stage.reducedMotion) {
    for (let index = 0; index < particles.length; index += 1) {
      const particle = particles[index];
      dummy.position.set(
        Math.cos(particle.angle) * particle.radius,
        Math.sin(particle.angle) * particle.radius * 0.68,
        particle.z,
      );
      dummy.rotation.set(particle.spin, particle.spin * 0.7, particle.spin * 0.4);
      dummy.scale.setScalar(particle.scale);
      dummy.updateMatrix();
      mesh.setMatrixAt(index, dummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
    stage.render();
  }

  return stage;
}

function cameraFollow(stage, elapsed) {
  const { camera, pointer } = stage;
  camera.position.x += (pointer.x * 1.1 - camera.position.x) * 0.05;
  camera.position.y += (1.1 + pointer.y * 0.5 - camera.position.y) * 0.05;
  camera.position.z = 9 + Math.sin(elapsed * 0.25) * 0.35;
  camera.lookAt(0, 0, -4);
}
