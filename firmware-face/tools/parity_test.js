// Same sequence as tools/host_parity_test.cpp, same xorshift RNG, same print
// format (C printf rounding via toFixed). Run from firmware-face/.
require('../design/face_engine.js');
const P = require('../design/face_params.json');
let st = 0x9E3779B9 >>> 0;
const rnd = () => { st ^= (st << 13) >>> 0; st >>>= 0; st ^= st >>> 17; st ^= (st << 5) >>> 0; st >>>= 0; return (st >>> 8) * (1 / 16777216); };
const f = new HaileFace.Face(P, rnd);
const K = {rrect: 0, quad: 1, circle: 2, arc: 3, text: 4};
let t = 0;
const run = n => { for (let i = 0; i < n; i++) { t += 16; if (f.talking) f.setLevel((i % 7) / 6); f.step(16, t); } };
const r2 = v => (Math.abs(v) < 0.005 ? 0 : v).toFixed(2), r3 = v => (Math.abs(v) < 0.0005 ? 0 : v).toFixed(3);
function dump(tag) {
  const ps = f.render();
  console.log(`F ${tag} ${ps.length} ${f.bright} drift ${f.ox} ${f.oy} off ${f.screenOff() ? 1 : 0} idle ${Math.floor(f.idleMs / 1000)}`);
  for (const p of ps) {
    let x = 0, y = 0, w = 0, h = 0, r = 0, p0 = 0, p5 = 0, a0 = 0, a1 = 0, size = 0;
    if (p.k === 'rrect') ({x, y, w, h, r} = p); else if (p.k === 'circle') ({x, y, r} = p);
    else if (p.k === 'arc') ({x, y, r, w, a0, a1} = p); else if (p.k === 'text') ({x, y, size} = p);
    else if (p.k === 'quad') { p0 = p.p[0]; p5 = p.p[5]; }
    const c = p.c.map(v => Math.max(0, Math.min(255, Math.trunc(v))));
    console.log([K[p.k], r2(x), r2(y), r2(w), r2(h), r2(r), r2(p0), r2(p5), r2(a0), r2(a1), r2(size), c[0], c[1], c[2], r3(Math.max(0, Math.min(1, p.o)))].join(' '));
  }
}
const seq = ['surprised', 'thinking', 'happy', 'sad', 'angry', 'sleepy', 'neutral'];
for (let s = 0; s < 7; s++) {
  f.setEmotion(seq[s]); if (s === 2) f.setTalking(true);
  run(40);
  if (s === 3) f.setTalking(false);
  if (s === 5) f.setAsleep(true);
  dump(seq[s]);
}
f.setAsleep(false); run(60); f.lookAt(0.8, -0.5); run(20); dump('gaze');
run(3750); dump('drift-1min');
run(8000); dump('dimmed');
f.setAsleep(true); run(8000); dump('off');
f.poke(); f.setAsleep(false); run(40); dump('woken');
