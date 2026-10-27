/* A cache owns both the request and its result. Invalidated requests may finish,
   but cannot repopulate the cache or update a newer editor. Errors can retry. */
function rxPreviewCache(values, params, request=post) {
 const entries = new Map();
 return {
  entries,
  async load(key, body) {
   if(values[key])return {url:values[key],par:params[key]};
   const prior=entries.get(key);
   if(prior&&prior.status==='pending')return prior.promise;
   const entry={status:'pending',promise:null};
   entries.set(key,entry);
   entry.promise=(async()=>{
    try {
     const r=await request('/api/particles/emoji-preview',body);
     if(entries.get(key)!==entry)return null;
     if(!r.ok){entry.status='error';entry.error=r.j.error||'The preview could not be made.';return null;}
     entry.status='success';values[key]=r.j.url;params[key]=r.j.params;
     return {url:r.j.url,par:r.j.params};
    } catch(e) {
     if(entries.get(key)===entry){entry.status='error';entry.error=e.message;}
     return null;
    }
   })();
   return entry.promise;
  },
  invalidate(match=()=>true) {
   for(const key of new Set([...entries.keys(),...Object.keys(values)])){
    if(match(key)){entries.delete(key);delete values[key];delete params[key];}
   }
  }
 };
}
