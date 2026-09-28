// Host parity test: drives src/face_engine.cpp through a fixed sequence and
// prints every primitive. tools/parity_test.js drives design/face_engine.js
// through the same sequence with the same RNG; the outputs must match.
//   g++ -O1 -I include -o /tmp/parity tools/host_parity_test.cpp src/face_engine.cpp && /tmp/parity > /tmp/c.txt
//   node tools/parity_test.js > /tmp/j.txt && diff /tmp/c.txt /tmp/j.txt
// The F lines (state, brightness, drift, idle) must match exactly; primitive
// values may differ by 0.01 (float vs double rounding).
#include "../src/face_engine.h"
#include <stdio.h>
static FaceEngine f; static Prim P[FaceEngine::kMaxPrims]; static uint32_t t = 0;
static void run(int frames) { for (int i = 0; i < frames; i++) { t += 16; if (f.talking()) f.setLevel((i % 7) / 6.0f); f.step(16, t); } }
static void dump(const char* tag) {
  int n = f.render(P);
  printf("F %s %d %d drift %d %d off %d idle %u\n", tag, n, f.brightness(), f.driftX(), f.driftY(), f.screenOff() ? 1 : 0, f.idleMs() / 1000);
  for (int i = 0; i < n; i++) { Prim& p = P[i]; printf("%d %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f %d %d %d %.3f\n", (int)p.k, p.x, p.y, p.w, p.h, p.r, p.p[0], p.p[5], p.a0, p.a1, p.size, p.c[0], p.c[1], p.c[2], p.o); }
}
int main() {
  const char* seq[] = {"surprised", "thinking", "happy", "sad", "angry", "sleepy", "neutral"};
  for (int s = 0; s < 7; s++) {
    f.setEmotion(seq[s]); if (s == 2) f.setTalking(true);
    run(40);
    if (s == 3) f.setTalking(false);
    if (s == 5) f.setAsleep(true);
    dump(seq[s]);
  }
  // M4: gaze, drift over minutes, dim, screen off, wake.
  f.setAsleep(false); run(60); f.lookAt(0.8f, -0.5f); run(20); dump("gaze");
  run(3750); dump("drift-1min");           // 60 s
  run(8000); dump("dimmed");               // ~3.2 min idle
  f.setAsleep(true); run(8000); dump("off"); // ~5.4 min, asleep
  f.poke(); f.setAsleep(false); run(40); dump("woken");
}
