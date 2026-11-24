import { useEffect, useRef } from 'react';
const points = Array.from({length:230}, (_,i)=>{const y=1-2*(i+.5)/230,r=Math.sqrt(1-y*y),a=i*2.399963;return [r*Math.cos(a),y,r*Math.sin(a)];});
const edges=[];
for(let i=0;i<points.length;i++) for(let j=i+1;j<points.length;j++) if(Math.hypot(...points[i].map((v,k)=>v-points[j][k]))<.28) edges.push([i,j]);
export default function NeuralCore({mode}) {
 const canvas=useRef(null), state=useRef(mode); state.current=mode;
 useEffect(()=>{const c=canvas.current,ctx=c.getContext('2d');let frame,last=0,t=0,angle=0,scale=1,previous='';
 function draw(now){const dt=last?Math.min((now-last)/1000,.05):0;last=now;const m=state.current;if(m!==previous){t=0;previous=m;}t+=dt;
 const w=c.clientWidth,h=c.clientHeight,d=Math.min(window.devicePixelRatio||1,2);if(c.width!==Math.round(w*d)||c.height!==Math.round(h*d)){c.width=Math.round(w*d);c.height=Math.round(h*d);}ctx.setTransform(d,0,0,d,0,0);ctx.clearRect(0,0,w,h);
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 angle+=reduced?0:dt*(m==='thinking'?.85:m==='idle'?.08:0);
 const target=reduced?1:m==='listening'?.9+.1*Math.cos(t*2*Math.PI/5):m==='speaking'?1+.12*Math.sin(t*2*Math.PI/3.5):1;
 scale+=(target-scale)*(1-Math.exp(-dt*5));const r=Math.min(w,h)*.32*scale,x=w/2,y=h/2;
 const g=ctx.createRadialGradient(x,y,0,x,y,r*1.7);g.addColorStop(0,'#3347a54d');g.addColorStop(1,'#04060c00');ctx.fillStyle=g;ctx.fillRect(0,0,w,h);
 const p=points.map(([a,b,z])=>{const u=a*Math.cos(angle)-z*Math.sin(angle),v=a*Math.sin(angle)+z*Math.cos(angle);return [x+u*r,y+(b*.96+v*.15)*r,v];});
 for(const [i,j] of edges){const a=p[i],b=p[j];ctx.strokeStyle=`rgba(114,151,255,${.06+(a[2]+1)*.12})`;ctx.lineWidth=.7;ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();}
 for(const a of p){ctx.beginPath();ctx.arc(a[0],a[1],.8+(a[2]+1)*.75,0,Math.PI*2);ctx.fillStyle=`rgba(167,204,255,${.35+(a[2]+1)*.3})`;ctx.fill();}frame=requestAnimationFrame(draw);}
 frame=requestAnimationFrame(draw);return ()=>cancelAnimationFrame(frame);},[]);
 return <canvas ref={canvas} className="neural-canvas" aria-hidden="true"/>;
}
