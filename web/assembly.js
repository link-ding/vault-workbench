// Exterior 3D assembly of the configured drawer or case, built from the recipe's own STL files.
// Placement data (rotations and anchor points) comes from tools/build_assembly.py.
import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';
import { fetchGeometry } from '/viewer.js';

const PITCH = 48; // one F level inside a module; facades are 48·F − 1 mm tall
const CACHE_BYTES = 500 * 1024 ** 2;
const VIEW_DIRECTION = new THREE.Vector3(.75, 1.1, .85).normalize(); // front-left, like the manual's product shots
const TONES = { body: 0xdfe1db, part: 0xeceee9, dark: 0x3b3e3b, accent: 0xe86634 };

const matrix = values => new THREE.Matrix4().set(...values);
const at = (x, y, z) => new THREE.Matrix4().makeTranslation(x, y, z);
const away = (x, y, z) => new THREE.Vector3(x, y, z); // exploded-view offset in assembly millimetres

// Returns the measured parts of one configured drawer, or null when the files are not in the placement data.
export function drawerLayout(cfg, parts, data) {
  const measured = part => {
    const entry = part && data.files[part.relative];
    return entry && entry.size === part.size ? entry : null;
  };
  const named = name => parts.find(p => p.name === name);
  const module = parts.find(p => p.relative.includes('/Modules/'));
  const body = measured(module);
  if (!body) return null;
  const u = data.universal;
  const placed = [{ part: module, matrix: matrix(body.matrix), tone: 'body', explode: away(0, 0, 0) }];
  // exploded view: the box slides out of the module, the facade group stands in front of it
  const pull = body.depth * .6 + 20, ahead = pull + 45, side = body.width * .3 + 30;
  const key = named('Key (Turn On Supports).stl'), keyCap = named('Key Cap.stl'), hiddenKey = named('Hidden Key (Turn On Supports).stl');
  const label = named('Label.stl'), facadeCap = named('Facade Cap.stl'), stopper = named('Runner Stopper.stl');
  const skipped = [];
  const bar = named('Heawyweight Roof Handle.stl'), insert = named('Heawyweight Roof Insert.stl');
  let level = 0;
  for (const height of cfg.drawers) {
    const facade = parts.find(p => (p.relative.includes('/Facades/') && p.name.startsWith(`${cfg.family}${height}F `)) || p.name === 'XL Facade 1F.stl');
    const front = measured(facade);
    if (!front) return null;
    const base = at(0, 0, body.bodyBottom + PITCH * level);
    const fixed = (x, y, z, local) => base.clone().multiply(at(x, y, z)).multiply(matrix(local));
    placed.push({ part: facade, matrix: base.clone().multiply(matrix(front.matrix)), tone: 'part', explode: away(0, ahead, 0) });
    // The box's lowest front screw row meets the facade's keyhole row (M4 × 14 through both).
    // XL6/XL7 boxes come in a front and a rear half; XL3–XL5 boxes are open at the back and take a back panel.
    const boxFiles = parts.filter(p => p.relative.includes('/Boxes/'));
    const halves = boxFiles.filter(p => / Part [12]\.stl$/.test(p.name)).sort((a, b) => a.name.localeCompare(b.name));
    const boxPart = halves[0] || boxFiles.find(p => p.name.toLowerCase().endsWith(` box ${height}f.stl`));
    const box = measured(boxPart);
    if (box) {
      const boxZ = body.bodyBottom + PITCH * level + front.cap.z - box.holeZ;
      placed.push({ part: boxPart, matrix: at(0, 0, boxZ).multiply(matrix(box.matrix)), tone: 'dark', explode: away(0, pull, 0) });
      const rear = measured(halves[1]);
      // single XL boxes end 2.2 mm in front of the module's back; the rear half ends there too
      if (rear) placed.push({ part: halves[1], matrix: at(0, -(body.depth - 2.2 - rear.depth), boxZ).multiply(matrix(rear.matrix)), tone: 'dark', explode: away(0, pull - 45, 0) });
      const panelPart = boxFiles.find(p => p.name.endsWith(' Back Panel.stl')), panel = measured(panelPart);
      if (panel) placed.push({ part: panelPart, matrix: at(0, -box.depth + panel.thickness, boxZ).multiply(matrix(panel.matrix)), tone: 'dark', explode: away(0, pull - 70, 0) });
      let behind = 0;
      for (const plate of parts.filter(p => p.relative.includes('/Gridfinity Baseplate/')).sort((a, b) => a.name.localeCompare(b.name))) {
        const grid = measured(plate);
        if (!grid) continue;
        placed.push({ part: plate, matrix: at(0, -grid.frontGap - behind, boxZ + grid.floor).multiply(matrix(grid.matrix)), tone: 'accent', explode: away(0, pull, box.height + 25 + behind / 4) });
        behind += grid.depth || 0;
      }
    }
    if (key) placed.push({ part: key, matrix: fixed(front.keyhole.x, front.face, front.keyhole.z, u.key), tone: 'dark', explode: away(0, ahead + 35, 0) });
    if (key && keyCap) placed.push({ part: keyCap, matrix: fixed(front.keyhole.x, front.face, front.keyhole.z, u.keyCap), tone: 'part', explode: away(0, ahead + 60, 0) });
    // Hidden Key Part 2 is the removable turning key, so the closed drawer shows only the flush lock.
    if (hiddenKey) placed.push({ part: hiddenKey, matrix: fixed(front.keyhole.x, front.face, front.keyhole.z, u.hiddenKey), tone: 'dark', explode: away(0, ahead + 35, 0) });
    if (label) placed.push({ part: label, matrix: fixed(front.pocket.xMin, front.pocket.floor, front.pocket.z, u.label), tone: 'part', explode: away(side, ahead, 0) });
    if (facadeCap) placed.push({ part: facadeCap, matrix: fixed(front.cap.x, front.face, front.cap.z, u.facadeCap), tone: 'part', explode: away(0, ahead + 25, 0) });
    const slot = body.slots[level];
    if (stopper && slot) {
      const half = body.width / 2, local = matrix(u.stopper);
      placed.push({ part: stopper, matrix: at(half, slot.y, slot.z).multiply(local), tone: 'accent', explode: away(30, 0, 0) });
      placed.push({ part: stopper, matrix: at(-half, slot.y, slot.z).multiply(new THREE.Matrix4().makeRotationZ(Math.PI)).multiply(local), tone: 'accent', explode: away(-30, 0, 0) });
    }
    level += height;
  }
  const roofPart = parts.find(p => p.relative.includes('/Roofs/'));
  const roof = measured(roofPart);
  if (roofPart && !roof) skipped.push('顶盖');
  if (roof) {
    const lift = 70;
    placed.push({ part: roofPart, matrix: at(0, 0, body.bodyTop).multiply(matrix(roof.matrix)), tone: 'body', explode: away(0, 0, lift) });
    const handle = named('Top Handle (Turn On Supports).stl');
    if (handle && roof.handle) placed.push({ part: handle, matrix: at(0, roof.handle.y, body.bodyTop + roof.handle.z).multiply(matrix(u.topHandle)), tone: 'dark', explode: away(0, 0, lift + 60) });
    if (bar && roof.bar) {
      const axis = at(0, roof.bar.y, body.bodyTop + roof.bar.z);
      placed.push({ part: bar, matrix: axis.clone().multiply(matrix(u.heavyHandle)), tone: 'dark', explode: away(0, 0, lift + 60) });
      if (insert) {
        placed.push({ part: insert, matrix: axis.clone().multiply(matrix(u.heavyInsert)), tone: 'dark', explode: away(50, 0, lift + 60) });
        // the second insert sits at the other wall: half a turn about the front-back axis
        placed.push({ part: insert, matrix: axis.clone().multiply(new THREE.Matrix4().makeRotationY(Math.PI)).multiply(matrix(u.heavyInsert)), tone: 'dark', explode: away(-50, 0, lift + 60) });
      }
    }
  }
  return { placed, skipped };
}

// Returns the visible parts of one closed flip-lid case. Hinges, rods and the lock holder sit inside; the exploded view lists them in the tray.
export function caseLayout(cfg, parts, data) {
  const measured = part => {
    const entry = part && data.files[part.relative];
    return entry && entry.size === part.size ? entry : null;
  };
  const named = name => parts.find(p => p.name === name);
  const module = parts.find(p => p.relative.includes('/Modules/'));
  const facadePart = parts.find(p => p.kind === 'sustainer' && / Facade\.stl$/.test(p.name));
  const body = measured(module), front = measured(facadePart);
  const lids = parts.filter(p => /\/Roofs?\//.test(p.relative) && !p.name.startsWith('Connector'));
  if (!body || !front || !lids.length || lids.some(p => !measured(p))) return null;
  const u = data.universal;
  const base = at(0, 0, body.rim - front.height); // the facade fills the front from the body floor to the rim
  const ahead = 60, side = body.width * .3 + 30;
  const placed = [
    { part: module, matrix: matrix(body.matrix), tone: 'body', explode: away(0, 0, 0) },
    { part: facadePart, matrix: base.clone().multiply(matrix(front.matrix)), tone: 'part', explode: away(0, ahead, 0) },
    ...lids.map(part => ({ part, matrix: at(0, front.thickness, body.rim).multiply(matrix(measured(part).matrix)), tone: 'body', explode: away(0, 0, 90) })),
  ];
  // Closed lock: the catch plate rests on the lock pocket face and its hook sits on the lid's front nose.
  const lid = measured(lids[0]);
  const latch = named('LOCK OUTER PART 1F.stl') || named('LOCK OUTER PART 2-4F.stl');
  if (latch) {
    const tall = latch.name.includes('2-4F');
    const top = body.rim + lid.nose - u[tall ? 'latch24FCeiling' : 'latch1FCeiling'];
    for (const x of front.locks) placed.push({ part: latch, matrix: at(x, front.lockFloor, top).multiply(matrix(u[tall ? 'latch24F' : 'latch1F'])), tone: 'dark', explode: away(0, ahead + 45, 0) });
  }
  const pocket = front.pocket, caseLabel = named('Label for Sustainers.stl'), label = named('Label.stl');
  // the 1F label slides in from the top edge, the 2F/4F label from the open side of its pocket
  if (caseLabel) placed.push({ part: caseLabel, matrix: base.clone().multiply(at((pocket.xMin + pocket.xMax) / 2, pocket.floor, pocket.zMax)).multiply(matrix(u.caseLabel)), tone: 'part', explode: away(0, ahead, 40) });
  if (label) placed.push({ part: label, matrix: base.clone().multiply(at(pocket.xMin, pocket.floor, (pocket.zMin + pocket.zMax) / 2)).multiply(matrix(u.label)), tone: 'part', explode: away(side, ahead, 0) });
  return { placed, skipped: [] };
}

const TRAY_TONES = [[/Springs|Latch|Rails|Stopper|Rods|Pins/i, 'accent'], [/Key|Handle|Hinge|LOCK OUTER|Runner Slides/i, 'dark']];
const EXPLODE_MS = 450;

export class AssemblyViewer {
  constructor(canvas, overlay) {
    this.canvas = canvas;
    this.overlay = overlay;
    this.cache = new Map();
    this.group = new THREE.Group();
    this.tray = new THREE.Group();
    this.amount = 0; // 0 assembled … 1 exploded
    this.exploded = false;
    this.trayParts = [];
    this.trayReady = false;
    this.badges = new Map();
    this.highlighted = null;
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    } catch {
      this.renderer = null;
      return;
    }
    this.scene = new THREE.Scene();
    this.scene.add(this.group, this.tray);
    this.camera = new THREE.PerspectiveCamera(26, 1, 1, 10000);
    this.camera.up.set(0, 0, 1);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8f9886, 1.7));
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(.5, .4, 1);
    this.camera.add(key);
    this.scene.add(this.camera);
    const material = (tone, extra = {}) => new THREE.MeshStandardMaterial({ color: TONES[tone], roughness: tone === 'dark' ? .55 : .7, metalness: 0, side: THREE.DoubleSide, ...extra });
    this.materials = Object.fromEntries(Object.keys(TONES).map(tone => [tone, material(tone)]));
    this.dimmed = Object.fromEntries(Object.keys(TONES).map(tone => [tone, material(tone, { transparent: true, opacity: .16, depthWrite: false })]));
    this.focus = new THREE.MeshStandardMaterial({ color: 0xf07a45, emissive: 0x8a2e0a, roughness: .5, side: THREE.DoubleSide });
    this.pickTarget = new THREE.WebGLRenderTarget(1, 1);
    this.pickMaterials = [];
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.addEventListener('change', () => this.draw());
    new ResizeObserver(() => this.draw()).observe(canvas);
  }
  get available() { return Boolean(this.renderer); }
  // Downloads are not cancelled: a file the user moved away from is still worth caching.
  geometry(part, onBytes) {
    let item = this.cache.get(part.id);
    if (!item) {
      const listeners = new Set();
      item = { size: part.size, listeners, promise: fetchGeometry('/api/stl/' + part.id, { onProgress: done => listeners.forEach(fn => fn(done)) }) };
      this.cache.set(part.id, item);
      item.promise.catch(() => this.cache.delete(part.id)).finally(() => listeners.clear());
    }
    const listen = done => onBytes(part.id, done);
    item.listeners.add(listen);
    item.used = performance.now();
    return item.promise.finally(() => item.listeners.delete(listen));
  }
  evict(keep) {
    let total = [...this.cache.values()].reduce((sum, item) => sum + item.size, 0);
    for (const [id, item] of [...this.cache].sort((a, b) => a[1].used - b[1].used)) {
      if (total <= CACHE_BYTES) break;
      if (keep.has(id)) continue;
      item.promise.then(g => g.dispose(), () => {});
      this.cache.delete(id);
      total -= item.size;
    }
  }
  async load(parts, { signal, onProgress } = {}) {
    const unique = new Map(parts.map(p => [p.id, p]));
    const total = [...unique.values()].reduce((sum, p) => sum + p.size, 0);
    const received = new Map([...unique.keys()].map(id => [id, 0]));
    const report = (id, done) => {
      received.set(id, done);
      onProgress?.([...received.values()].reduce((a, b) => a + b, 0), total);
    };
    const geometries = new Map();
    await Promise.all([...unique.values()].map(async part => {
      geometries.set(part.id, await this.geometry(part, report));
      report(part.id, part.size);
    }));
    return signal?.aborted ? null : geometries;
  }
  mesh(geometry, part, tone, base, explode) {
    const mesh = new THREE.Mesh(geometry, this.materials[tone]);
    mesh.matrixAutoUpdate = false;
    mesh.userData = { id: part.id, tone, base, explode };
    return mesh;
  }
  // Loads the placed files (cached across configurations), then swaps the scene in one step.
  // Parts without measured placement wait in `tray` until the exploded view asks for them.
  async show(placed, tray, options = {}) {
    if (!this.renderer) throw new Error('此浏览器无法使用 WebGL，暂时不能显示 3D 组装。');
    const geometries = await this.load(placed.map(p => p.part), options);
    if (!geometries) return false;
    this.group.clear();
    this.tray.clear();
    for (const { part, matrix: m, tone, explode } of placed) this.group.add(this.mesh(geometries.get(part.id), part, tone, m.clone(), explode));
    this.trayParts = tray;
    this.trayReady = !tray.length;
    if (this.exploded && !this.trayReady && !await this.loadTray(options)) return false;
    this.evict(new Set([...placed.map(p => p.part.id), ...tray.map(p => p.id)]));
    this.highlight(this.highlighted);
    this.pose();
    this.frame();
    return true;
  }
  // Unplaced files stand in rows beside the exploded assembly, in their print orientation.
  async loadTray(options) {
    const geometries = await this.load(this.trayParts, options);
    if (!geometries) return false;
    this.tray.clear();
    const bounds = this.bounds(1, false);
    const rowLimit = Math.max(bounds.max.x - bounds.min.x, 260);
    let x = bounds.min.x - 40, y = bounds.max.y, rowDepth = 0;
    for (const part of this.trayParts) {
      const g = geometries.get(part.id), size = g.boundingBox.getSize(new THREE.Vector3()), centre = g.boundingBox.getCenter(new THREE.Vector3());
      if (bounds.min.x - 40 - (x - size.x) > rowLimit && rowDepth) { x = bounds.min.x - 40; y -= rowDepth + 30; rowDepth = 0; }
      const base = at(x - size.x / 2 - centre.x, y - size.y / 2 - centre.y, -g.boundingBox.min.z);
      const tone = TRAY_TONES.find(([test]) => test.test(part.name))?.[1] || 'part';
      this.tray.add(this.mesh(g, part, tone, base, away(0, 0, 0)));
      x -= size.x + 30;
      rowDepth = Math.max(rowDepth, size.y);
    }
    this.trayReady = true;
    return true;
  }
  async setExploded(on, options) {
    this.exploded = on;
    if (on && !this.trayReady && !await this.loadTray(options)) return;
    this.highlight(this.highlighted);
    this.frame(on ? 1 : 0);
    const from = this.amount, to = on ? 1 : 0, start = performance.now();
    await new Promise(resolve => {
      // A hidden or background page gets no animation frames, so a timer stands in when one is late.
      const next = () => {
        let fired = false;
        const go = () => { if (!fired) { fired = true; step(performance.now()); } };
        requestAnimationFrame(go);
        setTimeout(go, 50);
      };
      const step = now => {
        const k = Math.min(1, (now - start) / EXPLODE_MS);
        this.amount = from + (to - from) * (1 - (1 - k) ** 3);
        this.pose();
        this.draw();
        if (k < 1) next(); else resolve();
      };
      next();
    });
  }
  pose() {
    for (const mesh of this.group.children) {
      const { base, explode } = mesh.userData;
      mesh.matrix.copy(at(explode.x * this.amount, explode.y * this.amount, explode.z * this.amount).multiply(base));
    }
    for (const mesh of this.tray.children) mesh.matrix.copy(mesh.userData.base);
    this.tray.visible = this.amount > .98;
    this.scene.updateMatrixWorld(true);
  }
  bounds(amount, withTray = true) {
    const box = new THREE.Box3(), part = new THREE.Box3();
    for (const mesh of this.group.children) {
      const { base, explode } = mesh.userData;
      box.union(part.copy(mesh.geometry.boundingBox).applyMatrix4(at(explode.x * amount, explode.y * amount, explode.z * amount).multiply(base)));
    }
    if (withTray && amount > .98) for (const mesh of this.tray.children) box.union(part.copy(mesh.geometry.boundingBox).applyMatrix4(mesh.userData.base));
    return box;
  }
  // Highlights every instance of the given file ids and fades the rest; null restores normal shading.
  highlight(ids) {
    this.highlighted = ids?.size ? ids : null;
    for (const mesh of [...this.group.children, ...this.tray.children]) {
      const { id, tone } = mesh.userData;
      mesh.material = !this.highlighted ? this.materials[tone] : this.highlighted.has(id) ? this.focus : this.dimmed[tone];
    }
    for (const [id, badge] of this.badges) badge.classList.toggle('active', Boolean(this.highlighted?.has(id)));
    this.draw();
  }
  // GPU picking: draw each mesh in a unique flat colour into a 1×1 target under the pointer.
  pick(clientX, clientY) {
    const meshes = [...this.group.children, ...(this.tray.visible ? this.tray.children : [])];
    if (!this.renderer || !meshes.length) return null;
    const rect = this.canvas.getBoundingClientRect(), dpr = this.renderer.getPixelRatio();
    const width = Math.floor(rect.width * dpr), height = Math.floor(rect.height * dpr);
    const x = Math.floor((clientX - rect.left) * dpr), y = Math.floor((clientY - rect.top) * dpr);
    if (x < 0 || y < 0 || x >= width || y >= height) return null;
    const saved = meshes.map(mesh => mesh.material);
    meshes.forEach((mesh, i) => {
      this.pickMaterials[i] ??= new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, color: new THREE.Color().setRGB(((i + 1) & 255) / 255, (((i + 1) >> 8) & 255) / 255, 0, THREE.LinearSRGBColorSpace) });
      mesh.material = this.pickMaterials[i];
    });
    this.camera.setViewOffset(width, height, x, y, 1, 1);
    this.renderer.setRenderTarget(this.pickTarget);
    this.renderer.clear();
    this.renderer.render(this.scene, this.camera);
    this.renderer.setRenderTarget(null);
    this.camera.clearViewOffset();
    meshes.forEach((mesh, i) => { mesh.material = saved[i]; });
    const pixel = new Uint8Array(4);
    this.renderer.readRenderTargetPixels(this.pickTarget, 0, 0, 1, 1, pixel);
    const index = pixel[0] + pixel[1] * 256;
    return index ? meshes[index - 1]?.userData.id ?? null : null;
  }
  // Numbered badges follow each file's first instance in the exploded view.
  setBadges(numbers, titles, onSelect) {
    for (const badge of this.badges.values()) badge.remove();
    this.badges = new Map([...numbers].map(([id, n]) => {
      const badge = document.createElement('button');
      badge.type = 'button';
      badge.className = 'part-badge';
      badge.textContent = n;
      badge.title = titles.get(id) || '';
      badge.addEventListener('click', () => onSelect(id));
      badge.addEventListener('pointerenter', () => this.highlight(new Set([id])));
      badge.addEventListener('pointerleave', () => this.highlight(null));
      this.overlay.append(badge);
      return [id, badge];
    }));
    this.draw();
  }
  placeBadges() {
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight, show = this.amount > .98;
    const first = new Map();
    for (const mesh of [...this.group.children, ...this.tray.children]) if (!first.has(mesh.userData.id)) first.set(mesh.userData.id, mesh);
    for (const [id, badge] of this.badges) {
      const mesh = first.get(id);
      if (!show || !mesh) { badge.hidden = true; continue; }
      const p = mesh.geometry.boundingBox.getCenter(new THREE.Vector3()).applyMatrix4(mesh.matrix).project(this.camera);
      badge.hidden = p.z > 1 || Math.abs(p.x) > 1 || Math.abs(p.y) > 1;
      badge.style.left = `${this.canvas.offsetLeft + (p.x + 1) / 2 * width}px`;
      badge.style.top = `${this.canvas.offsetTop + (1 - p.y) / 2 * height}px`;
    }
  }
  clear() { this.group.clear(); this.tray.clear(); this.draw(); }
  frame(amount = this.amount) {
    const box = this.bounds(amount);
    if (box.isEmpty()) return this.draw();
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const distance = sphere.radius / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2)) * .82;
    this.camera.near = distance / 50; this.camera.far = distance * 20;
    this.camera.position.copy(sphere.center).addScaledVector(VIEW_DIRECTION, distance);
    this.controls.target.copy(sphere.center);
    this.controls.minDistance = sphere.radius * .3; this.controls.maxDistance = distance * 4;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.draw();
  }
  draw() {
    if (!this.renderer) return;
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    if (!width || !height) return;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
    this.placeBadges();
  }
}
