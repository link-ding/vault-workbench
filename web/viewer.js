// A small offline STL inspector. Full-resolution source files remain untouched.
export class MeshViewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.yaw = -.65; this.pitch = .7; this.zoom = 1;
    this.data = null; this.drag = null;
    canvas.addEventListener('pointerdown', e => {
      this.drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', e => {
      if (!this.drag) return;
      this.yaw += (e.clientX - this.drag[0]) * .009;
      this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch + (e.clientY - this.drag[1]) * .009));
      this.drag = [e.clientX, e.clientY]; this.draw();
    });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(name, () => { this.drag = null; });
    canvas.addEventListener('wheel', e => { e.preventDefault(); this.zoom = Math.max(.3, Math.min(4, this.zoom * Math.exp(-e.deltaY * .001))); this.draw(); }, { passive: false });
    this.resizeObserver = new ResizeObserver(() => this.draw());
    this.resizeObserver.observe(canvas);
  }
  setData(data) {
    this.data = data; this.yaw = -.65; this.pitch = .7; this.zoom = 1;
    this.draw();
  }
  clear() { this.data = null; this.draw(); }
  draw() {
    const { canvas, ctx, data } = this;
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width || !height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = width * dpr; canvas.height = height * dpr;
    ctx.scale(dpr, dpr); ctx.clearRect(0, 0, width, height);
    if (!data) return;
    const center = data.min.map((n, i) => (n + data.max[i]) / 2);
    const span = Math.max(...data.dimensions, 1);
    const scale = Math.min(width, height) * .73 / span * this.zoom;
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const rotate = (x, y, z) => {
      const a = x * cy - y * sy, b = x * sy + y * cy;
      return [a, b * cp - z * sp, b * sp + z * cp];
    };
    const polygons = data.triangles.map(t => {
      const points = [];
      for (let i = 0; i < 9; i += 3) points.push(rotate(t[i] - center[0], t[i + 1] - center[1], t[i + 2] - center[2]));
      return { points, depth: points.reduce((s, p) => s + p[2], 0) / 3 };
    }).sort((a, b) => a.depth - b.depth);
    ctx.lineJoin = 'round';
    for (const { points: p } of polygons) {
      const u = p[1].map((x, i) => x - p[0][i]), v = p[2].map((x, i) => x - p[0][i]);
      const nx = u[1] * v[2] - u[2] * v[1], ny = u[2] * v[0] - u[0] * v[2], nz = u[0] * v[1] - u[1] * v[0];
      const len = Math.hypot(nx, ny, nz) || 1;
      const light = Math.abs((nx * -.3 + ny * -.5 + nz * .8) / len);
      const gray = Math.round(145 + light * 80);
      ctx.fillStyle = `rgb(${gray},${gray + 3},${gray - 3})`;
      ctx.strokeStyle = `rgba(70,85,60,0.14)`; ctx.lineWidth = .45;
      ctx.beginPath(); p.forEach((q, i) => ctx[i ? 'lineTo' : 'moveTo'](width / 2 + q[0] * scale, height / 2 - q[1] * scale));
      ctx.closePath(); ctx.fill(); ctx.stroke();
    }
    // Orientation triad uses the same transform as the sampled mesh.
    for (const [axis, color, dir] of [['X', '#cb785a', [1, 0, 0]], ['Y', '#779669', [0, 1, 0]], ['Z', '#718f9d', [0, 0, 1]]]) {
      const p = rotate(...dir); ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(width - 65, height - 58); ctx.lineTo(width - 65 + p[0] * 28, height - 58 - p[1] * 28); ctx.stroke();
      ctx.font = '10px Consolas, monospace'; ctx.fillText(axis, width - 65 + p[0] * 38, height - 58 - p[1] * 38);
    }
  }
}
