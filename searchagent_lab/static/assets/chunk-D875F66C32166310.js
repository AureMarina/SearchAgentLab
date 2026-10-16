/** One experiment-local execution selection. Legacy trace_* links remain valid. */
const pairs = [['panel','trace_panel'],['result_b','trace_b'],['result_a','trace_a'],
 ['result_axis','trace_axis'],['result_filter','trace_filter'],['result_q','trace_q']];
export const executionKeys = ['panel','result_b','result_a','result_axis','result_filter','result_q','result_dataset','result_page',
 'trace_panel','trace_b','trace_a','trace_axis','trace_filter','trace_q','trace_sample','trace_dataset','trace_side','trace_step','trace_field','trace_diff',
 'trace_scope','trace_key','trace_page','execution_view','exec_state','baseline_run'];
const put=(q,k,v)=>{if(v===undefined||v===null||v==='')q.delete(k);else q.set(k,String(v));};
const norm=(k,v)=>k.includes('filter')&&v==='missing'?'unscored':v;
export function syncExecutionState(q, patch = {}, {initial=false}={}) {
 const values={...patch};
 if(initial){
  const legacy=!q.has('exec_state')&&['trace','trace-compare'].includes(q.get('view'));
  for(const [key,alias] of pairs){const v=legacy?(q.get(alias)??q.get(key)):(q.get(key)??q.get(alias));if(v!==null)values[key]=norm(key,v);}
 }
 const oldPanel=q.get('panel')||q.get('trace_panel');
 const newPanel=values.panel??values.trace_panel;
 if(newPanel!==undefined&&oldPanel&&newPanel!==oldPanel){
  for(const k of executionKeys.filter(k=>!['execution_view','exec_state','baseline_run'].includes(k)))q.delete(k);
 }
 const oldB=q.get('result_b')||q.get('trace_b'),newB=values.result_b??values.trace_b;
 if(oldB&&newB!==undefined&&oldB!==newB){for(const k of ['trace_step','trace_field','trace_side'])q.delete(k);}
 for(const [key,alias] of pairs){
  if(Object.hasOwn(values,key)||Object.hasOwn(values,alias)){
   const v=norm(key,Object.hasOwn(values,key)?values[key]:values[alias]);values[key]=v;values[alias]=v;
  }
 }
 for(const [key,value] of Object.entries(values))put(q,key,value);
 q.set('exec_state','1');return q;
}
export function experimentParams(ctx,patch={}) {
 const q=syncExecutionState(new URLSearchParams(ctx.q),{}, {initial:true});syncExecutionState(q,patch);q.delete('family');q.delete('id');return Object.fromEntries(q);
}
export function executionReturnParams(q){return Object.fromEntries(executionKeys.filter(k=>q.has(k)).map(k=>['return_'+k,q.get(k)]));}
export function restoreExecutionReturn(q){return Object.fromEntries(executionKeys.filter(k=>q.has('return_'+k)).map(k=>[k,q.get('return_'+k)]));}
