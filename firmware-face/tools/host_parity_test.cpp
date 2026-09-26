#include "../src/face_engine.h"
#include <stdio.h>
int main(){ static FaceEngine f; static Prim P[FaceEngine::kMaxPrims]; uint32_t t=0;
 const char* seq[]={"surprised","thinking","happy","sad","angry","sleepy","neutral"};
 for(int s=0;s<7;s++){ f.setEmotion(seq[s]); if(s==2){f.setTalking(true);} 
  for(int i=0;i<40;i++){ t+=16; if(f.talking()) f.setLevel((i%7)/6.0f); f.step(16,t);} 
  if(s==3) f.setTalking(false);
  if(s==5) f.setAsleep(true);
  int n=f.render(P); printf("F %s %d %d\n",seq[s],n,f.brightness());
  for(int i=0;i<n;i++){ Prim&p=P[i]; printf("%d %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f %.2f %d %d %d %.3f\n",(int)p.k,p.x,p.y,p.w,p.h,p.r,p.p[0],p.p[5],p.a0,p.a1,p.size,p.c[0],p.c[1],p.c[2],p.o);} }
}
