/** Bounded, session-scoped GET cache. Mutations and refresh invalidate it.
 * No persisted traces. Responses are cloned because legacy components edit query data.
 * Generations prevent in-flight reads from repopulating the cache after a write.
 */
export function createAPI({fetcher=(...args)=>fetch(...args), now=()=>Date.now(), onUnauthorized=()=>location.replace('/team-login')}={}){
 const cache=new Map(),pending=new Map();let generation=0;
 const ttl=path=>['session','studio/catalog','studio/profile','studio/reports','inspection/files','studio/workspace','studio/reference','methods/experiment-overview','evaluation'].includes(path)?10000:0;
 function clear(){generation++;cache.clear();pending.clear();}
 async function api(path,params={},options={}){
  const q=new URLSearchParams();Object.entries(params).sort(([a],[b])=>a.localeCompare(b)).forEach(([k,v])=>{if(v!==undefined&&v!==null&&v!=='')q.set(k,v);});
  const url='/api/'+path+(q.size?'?'+q:''),isGet=!options.method||options.method==='GET';
  if(!isGet)clear();
  const cached=cache.get(url);if(isGet&&cached&&cached.until>now())return structuredClone(cached.data);
  if(isGet&&pending.has(url))return structuredClone(await pending.get(url));
  const gen=generation;
  const job=(async()=>{const r=await fetcher(url,options);let data;try{data=await r.json();}catch{throw Error('服务未返回有效数据，请确认后台已启动。');}
   if(r.status===401){clear();onUnauthorized();throw Error('登录已过期');}
   if(!r.ok)throw Error(data.error||'读取失败（'+r.status+'）');
   if(isGet&&ttl(path)&&gen===generation){cache.set(url,{data,until:now()+ttl(path)});if(cache.size>64)cache.delete(cache.keys().next().value);}
   return data;
  })();
  if(isGet)pending.set(url,job);
  try{return structuredClone(await job);}finally{if(pending.get(url)===job)pending.delete(url);if(!isGet)clear();}
 }
 return {api,clear};
}
