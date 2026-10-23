// Offline WebGL STL inspector. Renders every triangle of the original file; source STL stays untouched.
import * as THREE from 'three';
import { STLLoader } from '/vendor/STLLoader.js';
import { OrbitControls } from '/vendor/OrbitControls.js';

const VIEW_DIRECTION = new THREE.Vector3(.75, -1.1, .9).normalize();

// Streams one STL from the local server and parses every triangle; reports byte progress.
export async function fetchGeometry(url, { signal, onProgress } = {}) {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || '读取模型失败。');
  }
  const total = +response.headers.get('Content-Length') || 0;
  let buffer;
  if (total && response.body) {
    const reader = response.body.getReader();
    buffer = new Uint8Array(total);
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (received + value.length > total) throw new Error('模型文件在读取过程中发生了变化，请重试。');
      buffer.set(value, received); received += value.length;
      onProgress?.(received, total);
    }
  } else {
    buffer = new Uint8Array(await response.arrayBuffer());
  }
  const geometry = new STLLoader().parse(buffer.buffer);
  if (!geometry.attributes.position?.count) throw new Error('STL 中没有可显示的三角面。');
  geometry.computeBoundingBox();
  const { min, max } = geometry.boundingBox;
  if (![min.x, min.y, min.z, max.x, max.y, max.z].every(Number.isFinite)) throw new Error('STL 包含无效坐标。');
  return geometry;
}

export class MeshViewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.mesh = null;
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    } catch {
      this.renderer = null;
      return;
    }
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, .1, 1000);
    this.camera.up.set(0, 0, 1); // STL files are Z-up.
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a9480, 1.6));
    // Headlight keeps the side facing the viewer lit while orbiting.
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(-.4, .6, 1);
    this.camera.add(key);
    this.scene.add(this.camera);
    this.material = new THREE.MeshStandardMaterial({ color: 0xd2d7c8, roughness: .62, metalness: 0, side: THREE.DoubleSide });
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.addEventListener('change', () => this.draw());
    this.triad = document.createElement('canvas');
    this.triad.className = 'mesh-triad';
    this.triad.setAttribute('aria-hidden', 'true');
    canvas.after(this.triad);
    this.resizeObserver = new ResizeObserver(() => this.draw());
    this.resizeObserver.observe(canvas);
  }
  async load(url, { signal, onProgress } = {}) {
    if (!this.renderer) throw new Error('此浏览器无法使用 WebGL，暂时不能预览模型。');
    const geometry = await fetchGeometry(url, { signal, onProgress });
    const { min, max } = geometry.boundingBox;
    this.clear();
    this.mesh = new THREE.Mesh(geometry, this.material);
    this.scene.add(this.mesh);
    this.frame();
    const size = max.clone().sub(min);
    return { faces: geometry.attributes.position.count / 3, dimensions: [size.x, size.y, size.z].map(n => Math.round(n * 100) / 100) };
  }
  frame() {
    const geometry = this.mesh.geometry;
    geometry.computeBoundingSphere();
    const { center, radius } = geometry.boundingSphere;
    const distance = Math.max(radius, 1) / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 1.08;
    this.camera.near = distance / 100; this.camera.far = distance * 20;
    this.camera.position.copy(center).addScaledVector(VIEW_DIRECTION, distance);
    this.controls.target.copy(center);
    this.controls.minDistance = radius * .05; this.controls.maxDistance = distance * 6;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.draw();
  }
  clear() {
    if (!this.mesh) return this.draw();
    this.scene.remove(this.mesh);
    this.mesh.geometry.dispose();
    this.mesh = null;
    this.draw();
  }
  draw() {
    if (!this.renderer) return;
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    if (!width || !height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
    this.drawTriad(dpr);
  }
  // Orientation triad follows the camera so axes read the same as in a slicer.
  drawTriad(dpr) {
    const size = 90, ctx = this.triad.getContext('2d');
    this.triad.width = this.triad.height = size * dpr;
    ctx.scale(dpr, dpr); ctx.clearRect(0, 0, size, size);
    if (!this.mesh) return;
    const view = this.camera.matrixWorldInverse;
    for (const [axis, color, dir] of [['X', '#cb785a', [1, 0, 0]], ['Y', '#779669', [0, 1, 0]], ['Z', '#718f9d', [0, 0, 1]]]) {
      const p = new THREE.Vector3(...dir).transformDirection(view);
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(45, 45); ctx.lineTo(45 + p.x * 28, 45 - p.y * 28); ctx.stroke();
      ctx.font = '10px Consolas, monospace'; ctx.fillText(axis, 42 + p.x * 38, 48 - p.y * 38);
    }
  }
}
