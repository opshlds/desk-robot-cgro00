// HAIL-E face engine (reference implementation).
// The firmware's src/face_engine.cpp is a line-for-line port of this file:
// same state, same easing, same primitives. Change one, change the other.
//
// step(dtMs, nowMs) advances the animation; render() returns a list of
// primitives in painter's order on a black 466x466 screen:
//   {k:'rrect', x,y,w,h,r, c,o}   filled rounded rect (c = [r,g,b], o = 0..1)
//   {k:'quad',  p:[x0,y0,..x3,y3], c,o}   filled convex quad
//   {k:'circle',x,y,r, c,o}
//   {k:'arc',   x,y,r,w,a0,a1, c,o}       degrees, 0 = 3 o'clock, clockwise
//   {k:'text',  s,x,y,size, c,o}          centred glyph
(function (root) {
  const NUM = ['eyeW','eyeH','radius','gap','dy','lid','slant','asym','lower',
               'mouthW','curve','open','mouthDx','gazeX','gazeY'];
  const BLACK = [0, 0, 0];

  function hexRGB(h) { const n = parseInt(h.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; }
  function ease(cur, tgt, dt, tau) { return cur + (tgt - cur) * (1 - Math.exp(-dt / tau)); }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  class Face {
    constructor(params, rand) {
      this.P = params; this.L = params.layout;
      this.rand = rand || Math.random;
      this.emotion = 'neutral';
      const e = this.P.emotions.neutral;
      this.cur = {}; this.tgt = {};
      for (const k of NUM) { this.cur[k] = e[k]; this.tgt[k] = e[k]; }
      this.col = hexRGB(e.color); this.colT = this.col.slice();
      this.ring = e.ring; this.ringT = 0; this.ringPhase = 0;
      this.asleep = false; this.sleepT = 0; this.breath = 0;
      this.blink = 0; this.blinkDir = 0; this.nextBlink = 1500;
      this.gx = 0; this.gy = 0; this.gtx = 0; this.gty = 0; this.nextSaccade = 800;
      this.talking = false; this.level = 0; this.mouth = 0;
      this.pop = 0; this.idle = true;
      this.zeds = [{a: false}, {a: false}, {a: false}]; this.nextZed = 0;
      this.bright = this.L.brightAwake;
    }
    setParams(p) { this.P = p; this.L = p.layout; this.setEmotion(this.emotion, true); }
    setEmotion(name, quiet) {
      const e = this.P.emotions[name]; if (!e) return false;
      if (name !== this.emotion && !quiet) { this.pop = 1; this.ringT = 1; }
      this.emotion = name;
      for (const k of NUM) this.tgt[k] = e[k];
      this.colT = hexRGB(e.color); this.ring = e.ring;
      this.gtx = e.gazeX; this.gty = e.gazeY;
      return true;
    }
    setAsleep(on) { this.asleep = on; if (!on) { this.pop = 1; this.nextBlink = 0; } }
    setTalking(on) { this.talking = on; if (!on) this.level = 0; }
    setLevel(v) { this.level = clamp(v, 0, 1); }
    doBlink() { if (this.blinkDir === 0) this.blinkDir = 1; }
    lookAt(x, y) { this.gtx = clamp(x, -1, 1) * this.L.gazeMaxX; this.gty = clamp(y, -1, 1) * this.L.gazeMaxY; this.nextSaccade = this.now + 2500; }

    step(dt, now) {
      this.now = now;
      const L = this.L, R = this.rand;
      for (const k of NUM) this.cur[k] = ease(this.cur[k], this.tgt[k], dt, L.morphMs);
      for (let i = 0; i < 3; i++) this.col[i] = ease(this.col[i], this.colT[i], dt, L.colorMs);
      this.sleepT = ease(this.sleepT, this.asleep ? 1 : 0, dt, 450);
      this.breath += dt / 5200 * Math.PI * 2;
      this.pop *= Math.exp(-dt / 160);
      this.ringT *= Math.exp(-dt / (this.ring === 'flash' ? 380 : 900));
      this.ringPhase = (this.ringPhase + dt * 0.3) % 360;   // 1 turn / 1.2 s

      // Blink: 70 ms down, 120 ms up, every 2.2-6 s; sometimes a double.
      if (!this.asleep && this.blinkDir === 0 && now >= this.nextBlink) this.blinkDir = 1;
      if (this.blinkDir === 1) { this.blink += dt / 70; if (this.blink >= 1) { this.blink = 1; this.blinkDir = -1; } }
      else if (this.blinkDir === -1) {
        this.blink -= dt / 120;
        if (this.blink <= 0) { this.blink = 0; this.blinkDir = 0;
          this.nextBlink = now + (R() < 0.15 ? 160 : 2200 + R() * 3800); }
      }

      // Gaze: quick saccades around the emotion's resting gaze.
      if (this.idle && !this.asleep && now >= this.nextSaccade) {
        const e = this.P.emotions[this.emotion];
        if (R() < 0.4) { this.gtx = e.gazeX; this.gty = e.gazeY; }
        else { this.gtx = e.gazeX + (R() * 2 - 1) * L.gazeMaxX * 0.7; this.gty = e.gazeY + (R() * 2 - 1) * L.gazeMaxY * 0.7; }
        this.nextSaccade = now + 1200 + R() * 2800;
      }
      this.gx = ease(this.gx, this.gtx * (1 - this.sleepT), dt, 55);
      this.gy = ease(this.gy, this.gty * (1 - this.sleepT), dt, 55);

      // Mouth follows the voice level: fast attack, slower release.
      const mt = this.talking ? this.level : 0;
      this.mouth = ease(this.mouth, mt, dt, mt > this.mouth ? 25 : 70);

      // Z's while asleep.
      if (this.sleepT > 0.8 && now >= this.nextZed) {
        const z = this.zeds.find(z => !z.a);
        if (z) { z.a = true; z.t = 0; z.x0 = L.cx + 50 + R() * 20; }
        this.nextZed = now + 1500;
      }
      for (const z of this.zeds) if (z.a) { z.t += dt / 3200; if (z.t >= 1) z.a = false; }

      this.bright = Math.round(L.brightAwake + (L.brightAsleep - L.brightAwake) * this.sleepT);
    }

    // A curved band (mouth, closed eye): quads + round end caps.
    band(out, mx, my, w, curve, thick, openPx, c, o) {
      const N = 18, pts = [];
      for (let i = 0; i <= N; i++) {
        const t = i / N, bow = 1 - (2 * t - 1) * (2 * t - 1);
        const x = mx - w / 2 + w * t;
        const open = openPx * Math.sqrt(bow);                // elliptical opening
        const yTop = my - curve / 2 + curve * bow - open * 0.3;
        pts.push([x, yTop, yTop + thick + open]);
      }
      for (let i = 0; i < N; i++) {
        const a = pts[i], b = pts[i + 1], x1 = b[0] + 1;   // 1 px overlap hides AA seams
        out.push({k: 'quad', p: [a[0], a[1], x1, b[1], x1, b[2], a[0], a[2]], c, o});
      }
      const r = thick / 2;
      out.push({k: 'circle', x: pts[0][0], y: pts[0][1] + r, r, c, o});
      out.push({k: 'circle', x: pts[N][0], y: pts[N][1] + r, r, c, o});
    }

    eye(out, ex, ey, side, e, c, o) {   // side: -1 left, +1 right (screen)
      const L = this.L, s = 1 + 0.08 * this.pop;
      const w = e.eyeW * s, h = e.eyeH * s * (1 + 0.04 * this.mouth);
      const x = ex - w / 2, y = ey - h / 2;
      const sl = this.sleepT;
      out.push({k: 'rrect', x, y, w, h, r: Math.min(e.radius * s, w / 2, h / 2), c, o});
      // Upper lid: black quad; its bottom edge passes the eye centre-line at
      // `lid` of the height, tilted by `slant` px (inner corner lower if > 0).
      const lidBase = clamp(e.lid - side * e.asym, 0, 1);   // asym: left eye lower
      if (sl < 0.5) {   // glint, kept below the lid line
        const gc = mix(c, [255, 255, 255], 0.55);
        const gy = Math.max(ey - h * 0.26, y + lidBase * h + Math.abs(e.slant) * 0.5 + w * 0.13);
        out.push({k: 'circle', x: ex - w * 0.2 + this.gx * 0.25, y: gy + this.gy * 0.25, r: w * 0.085, c: gc, o: o * (1 - sl * 2)});
      }
      const close = Math.max(this.blink, sl);
      const lid = lidBase + (1 - lidBase) * close;
      const slant = e.slant * (1 - close);
      if (lid > 0.001 || Math.abs(slant) > 0.5) {
        const yc = y + lid * h;
        const inner = side < 0 ? x + w : x;                   // inner = towards the nose
        const yIn = yc + slant / 2, yOut = yc - slant / 2;
        const yL = inner === x ? yIn : yOut, yR = inner === x ? yOut : yIn;
        const top = y - 40;
        out.push({k: 'quad', p: [x - 4, top, x + w + 4, top, x + w + 4, yR + (close > 0.98 ? 4 : 0), x - 4, yL + (close > 0.98 ? 4 : 0)], c: BLACK, o: 1});
      }
      // Lower lid (happy crescent): black circle rising from below.
      if (e.lower > 0.01) {
        const rr = w * 0.78;
        out.push({k: 'circle', x: ex, y: y + h + rr - e.lower * h, r: rr, c: BLACK, o: 1});
      }
      // Closed eye while asleep: a soft downward curve at the lower third.
      if (sl > 0.05) this.band(out, ex, y + h * 0.72, w * 0.78, 14, 9, 0, c, o * sl);
    }

    render() {
      const L = this.L, e = this.cur, out = [];
      const breathe = Math.sin(this.breath) * this.sleepT;
      const o = 1 - 0.35 * this.sleepT * (0.5 + 0.5 * breathe);
      const c = this.col.map(Math.round);
      const ey = L.eyeY + e.dy + this.gy - 3 * this.mouth + breathe * 3;
      this.eye(out, L.cx - e.gap + this.gx, ey, -1, e, c, o);
      this.eye(out, L.cx + e.gap + this.gx, ey, +1, e, c, o);

      const open = Math.max(e.open, this.mouth) * L.talkOpenH * (1 - this.sleepT);
      const mw = e.mouthW * (1 - 0.35 * this.sleepT) * (1 - 0.15 * this.mouth);
      this.band(out, L.cx + e.mouthDx + this.gx * 0.4, L.mouthY + e.dy * 0.5 + this.gy * 0.3, mw,
                e.curve * (1 - this.sleepT), L.mouthThick, open, c, o);

      // Ring around the rim.
      const awake = 1 - this.sleepT;
      if (this.ring === 'spin' && awake > 0.1)
        out.push({k: 'arc', x: L.cx, y: L.cx, r: L.ringR, w: L.ringW, a0: this.ringPhase, a1: this.ringPhase + 70, c, o: 0.9 * awake});
      else if ((this.ring === 'pulse' || this.ring === 'flash') && this.ringT > 0.02)
        out.push({k: 'arc', x: L.cx, y: L.cx, r: L.ringR, w: L.ringW, a0: 0, a1: 360, c, o: this.ringT * (this.ring === 'flash' ? 0.9 : 0.45) * awake});

      for (const z of this.zeds) if (z.a) {
        const t = z.t, size = 22 + 26 * t;
        out.push({k: 'text', s: 'z', x: z.x0 + 50 * t + Math.sin(t * 6) * 6, y: L.eyeY - 60 - 80 * t, size,
                  c, o: Math.sin(Math.PI * t) * 0.8});
      }
      return out;
    }
  }
  root.HaileFace = {Face, hexRGB};
})(typeof window !== 'undefined' ? window : globalThis);
