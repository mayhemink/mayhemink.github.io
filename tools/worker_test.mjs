// Offline test for the mayhem-ops-state worker (no Cloudflare, no real keys).
// Save the worker source next to this file as worker.mjs, then: node worker_test.mjs
import W from './worker.mjs';
const store=new Map();
const env={PAGE_KEY:'kkkk',ADMIN_SECRET:'aaaa',OPS_STATE:{put:async(k,v)=>store.set(k,v),get:async(k,o)=>{const v=store.get(k);if(v==null)return null;if(o&&o.type==='arrayBuffer')return v instanceof ArrayBuffer?v:v;return typeof v==='string'?v:null;}}};
const go=async(u,o)=>{const r=await W.fetch(new Request('https://w'+u,o),env);return [r.status,r.headers.get('content-type'),await r.clone().text().then(t=>t.slice(0,160))];};
console.log(await go('/health'));
console.log(await go('/check',{method:'POST',headers:{'X-Ops-Key':'kkkk'},body:JSON.stringify({taskId:'a1',done:true,who:'k'})}));
console.log(await go('/state?k=kkkk'));
console.log(await go('/job?id=6569',{method:'POST',headers:{'X-Ops-Key':'kkkk'},body:'{"a":1}'}));
console.log(await go('/job?id=6569',{headers:{'X-Ops-Key':'kkkk'}}));
console.log(await go('/suggest',{method:'POST',body:JSON.stringify({text:'hi'})}));
const enc=new TextEncoder();
function zip(es){const parts=[];for(const e of es){const n=enc.encode(e.name);const h=new Uint8Array(30+n.length);const dv=new DataView(h.buffer);dv.setUint32(0,0x04034b50,true);dv.setUint32(18,e.d.length,true);dv.setUint32(22,e.d.length,true);dv.setUint16(26,n.length,true);h.set(n,30);parts.push(h,e.d);}const tot=parts.reduce((a,p)=>a+p.length,0);const u=new Uint8Array(tot);let o=0;for(const p of parts){u.set(p,o);o+=p.length;}return u;}
const z=zip([{name:'printavo-6569-a.jpg',d:new Uint8Array([9,9,9])},{name:'printavo-6569-manifest.json',d:enc.encode('{"v":3}')}]);
console.log(await go('/handoff?id=6569',{method:'POST',body:z}));
const r=await W.fetch(new Request('https://w/handoff?id=6569',{method:'POST',headers:{'X-Ops-Key':'kkkk'},body:z}),env);const j=await r.json();console.log(j.ok,j.list);
console.log(await go(new URL(j.list).pathname+new URL(j.list).search));
console.log(await go('/handoff/file?t='+j.token+'&f=printavo-6569-a.jpg'));
console.log(await go('/handoff/get?t='+j.token));
console.log(await go('/handoff/latest?id=6569',{headers:{'X-Ops-Key':'kkkk'}}));
console.log(await go('/handoff/get?t=00'));
console.log(await go('/nope'));
