/** Pure display adapters over inspection/record. Never rescore or infer evidence.
 * Keep original pointers. Tool returns do NOT stand in for model_visible/input.
 * Related model/tool records are grouped only with a saved ID/turn or an exact
 * recorded call match. Different trajectories remain separately selectable.
 */
export const valueText = value => value == null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
export const hasValue = value => value !== null && value !== undefined;
const obj = value => value && typeof value === 'object' && !Array.isArray(value);
const canonical = value => {
 if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
 if (obj(value)) return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
 return JSON.stringify(value);
};
const normalizeText = value => valueText(value).normalize('NFC').trim().replace(/\s+/g, ' ');
const field = (event, ...names) => event.fields?.find(f => names.includes(f.name) && hasValue(f.value));
const fieldValue = (event, ...names) => field(event, ...names)?.value;
const isModel = event => /^(model|assistant|model_generate|generation)$/.test(event.kind) || !!field(event, 'output', 'model_response');
const isTool = event => /^(tool|tool_result|tool_response)$/.test(event.kind) || (!isModel(event) && !!field(event, 'tool_call', 'tool_response'));
const isSystem = event => /^(system|developer|system_reminder|reminder)$/.test(event.kind);

function parseArguments(value) {
 if (typeof value !== 'string') return value;
 try { return JSON.parse(value); } catch { return value; }
}
function call(value, pointer, requested = false) {
 if (!obj(value)) return {name: 'tool', arguments: value, query: valueText(value), pointer, requested};
 const f = value.function || value;
 const args = parseArguments(f.arguments ?? value.args ?? {});
 return {id: value.id || value.tool_call_id || '', name: String(f.name || value.name || 'tool'), arguments: args,
  query: typeof args === 'string' ? args : valueText(args?.query ?? args?.q ?? args?.search_query ?? args?.url ?? args?.command ?? args), pointer, requested};
}
const actionKey = c => canonical([c.name, c.arguments]);
function callsFor(event) {
 const single = field(event, 'tool_call', 'function_call');
 if (single) return [call(single.value, single.pointer, isModel(event))];
 const many = field(event, 'tool_calls');
 if (many && Array.isArray(many.value)) return many.value.map((c,i) => call(c, many.pointer + '/' + i, isModel(event)));
 if (isModel(event)) {
  const output = field(event, 'output', 'model_response', 'content');
  // A logged search tag is a request, not proof of actual tool execution.
  const matches = typeof output?.value === 'string' ? [...output.value.matchAll(/<search>([\s\S]*?)<\/search>/g)] : [];
  return matches.map(m => call({name: 'search', arguments: {query: m[1].trim()}}, output.pointer, true));
 }
 return [];
}
function related(model, tool) {
 if (!isModel(model) || !isTool(tool)) return false;
 const a = callsFor(model), b = callsFor(tool), id = fieldValue(tool, 'tool_call_id');
 if (id) return a.some(c => c.id && c.id === id);
 const mt = fieldValue(model, 'turn'), tt = fieldValue(tool, 'turn');
 if (hasValue(mt) && hasValue(tt)) return mt === tt;
 return a.length > 0 && b.length > 0 && b.every(c => a.some(x => actionKey(c) === actionKey(x)));
}

/** Remove transport wrappers for presentation, never modify the stored field. */
export function readable(value) {
 let current = value;
 for (let i = 0; i < 5; i++) {
  if (typeof current === 'string') {
   const stripped = current.trim().replace(/^<information>\s*/, '').replace(/\s*<\/information>$/, '').trim();
   if (/^[\[{]/.test(stripped)) {
    try { current = JSON.parse(stripped); continue; } catch { /* ordinary document text */ }
   }
   return stripped;
  }
  if (obj(current)) {
   const key = ['raw_text','result','results','documents','output','text','content','contents'].find(k => hasValue(current[k]));
   if (key) { current = current[key]; continue; }
  }
  return current;
 }
 return current;
}
function textDocuments(text) {
 const regex = /(?:^|\n)\s*Doc\s+(\d+)(\(Title:\s*|:\s*)/g;
 const matches = [...text.matchAll(regex)];
 if (!matches.length) return text.trim() ? [{title: '可见文本', text: text.trim(), id: '', url: ''}] : [];
 return matches.map((m,i) => {
  let begin=m.index+m[0].length, end=matches[i+1]?.index??text.length, title='';
  if(m[2].startsWith('(')){
   let depth=1, cursor=begin;
   for(;cursor<end&&text[cursor]!=='\n';cursor++){
    if(text[cursor]==='(')depth++;
    if(text[cursor]===')'&&--depth===0)break;
   }
   if(depth===0){title=text.slice(begin,cursor).trim();begin=cursor+1;}
  }
  let body=text.slice(begin,end).trim();
  if(!title && /^"[^\n]+"\s*\n/.test(body)){
   const cut=body.indexOf('\n');title=body.slice(0,cut).trim().replace(/^"|"$/g,'');body=body.slice(cut+1).trim();
  }
  return {title:title||`文档 ${m[1]} · 标题未记录`,text:body,id:'',url:'',rank:Number(m[1])};
 });
}
function structuredDocument(value, i) {
 if (typeof value === 'string') return {title: `文档 ${i+1} · 标题未记录`, text:value, id:'', url:''};
 if (!obj(value)) return {title:`记录 ${i+1}`,text:valueText(value),id:'',url:''};
 const v = obj(value.document) ? value.document : value;
 let body = valueText(v.text ?? v.contents ?? v.content ?? v.snippet ?? v.raw_text ?? v);
 let title = valueText(v.title ?? value.title);
 if (!title && /^"[^\n]+"\s*\n/.test(body)) {
  const end = body.indexOf('\n'); title = body.slice(0,end).trim().replace(/^"|"$/g,''); body = body.slice(end+1).trim();
 }
 return {title:title || `文档 ${i+1} · 标题未记录`,text:body,id:valueText(v.docid ?? v.id ?? value.docid ?? value.id),url:valueText(v.url ?? value.url)};
}
export function documents(value) {
 const decoded = readable(value);
 if (Array.isArray(decoded)) return decoded.map(structuredDocument);
 if (typeof decoded === 'string') return textDocuments(decoded);
 if (!hasValue(decoded)) return [];
 return [structuredDocument(decoded, 0)];
}
function emptyReturn(value) {
 const v = readable(value);
 return (typeof v === 'string' && !v.trim()) || (Array.isArray(v) && v.length === 0);
}

function makeGroup(events, position, doc) {
 const all = events.flatMap(e => e.fields || []);
 const modelEvents = events.filter(isModel), toolEvents = events.filter(isTool);
 const declared = modelEvents.flatMap(callsFor), observed = toolEvents.flatMap(callsFor);
 const actions = [...observed];
 const used = new Set();
 for (const declaration of declared) {
  const result = events.find(e => fieldValue(e, 'tool_call_id') && fieldValue(e, 'tool_call_id') === declaration.id);
  const at = observed.findIndex((c,i) => !used.has(i) && (c.id && declaration.id ? c.id === declaration.id : actionKey(c) === actionKey(declaration)));
  if (at >= 0) used.add(at);
  else actions.push({...declaration, requested: !result && !events.some(e=>field(e,'tool_response','tool_result','result'))});
 }
 const outputs = modelEvents.flatMap(e => {
  const f = field(e, 'output','model_response','content','thought'); return f ? [f] : [];
 });
 const visible = all.filter(f => ['model_visible','model_visible_content'].includes(f.name) && hasValue(f.value));
 const returns = events.flatMap(e => {
  const f = field(e, 'tool_response','tool_result','result');
  if (f) return [f];
  const content = isTool(e) ? field(e, 'content') : null;
  return content ? [content] : [];
 });
 const inputs = all.filter(f => ['input','messages','observation','model_input'].includes(f.name) && hasValue(f.value));
 const errors = all.filter(f => ['error','parse_error','parser_error','execution_error','runtime_error'].includes(f.name) && hasValue(f.value) && f.value !== '' && f.value !== false);
 for (const f of all) if (f.name === 'status' && /^(error|failed|failure|timeout|invalid|parse_error|parser_error|tool_error|execution_error)$/.test(valueText(f.value))) errors.push(f);
 const notices = events.filter(isSystem).flatMap(e => e.fields || []);
 const outputText = outputs.map(f => valueText(f.value)).join('\n');
 const final = !actions.length && (!!outputText && (outputText === valueText(doc.view?.raw_final_output) || /<answer>[\s\S]*?<\/answer>/.test(outputText)) || events.some(e => /^(answer|final|final_answer)$/.test(e.kind)));
 let kind = actions.some(c => /search|retrieve/i.test(c.name)) ? 'search' : actions.some(c => /read|open|browse/i.test(c.name)) ? 'read' : actions.length || toolEvents.length ? 'tool' : final ? 'answer' : notices.length ? 'system' : errors.length ? 'error' : 'model';
 const docs = visible.flatMap(f => documents(f.value).map(d => ({...d,pointer:f.pointer})));
 const flags = [];
 if (errors.length) flags.push({type:'warn',label:errors.some(f=>/pars/.test(f.name)||/pars/.test(valueText(f.value)))?'记录到解析错误':'记录到执行错误',detail:'查看原始 error / status 字段；不自动归因给模型。'});
 if (returns.some(f => emptyReturn(f.value))) flags.push({type:'warn',label:'工具返回为空',detail:'原始返回明确为空；并非日志缺失。'});
 if (all.some(f => f.name === 'observation_truncated' && f.value === true)) flags.push({type:'warn',label:'日志标记已截断',detail:'原日志 observation_truncated=true；不据此断言丢失了关键证据。'});
 const label = ({search:'搜索',read:'阅读',tool:'工具调用',answer:'回答',system:'系统提醒',error:'执行错误',model:'模型回复'})[kind];
 let headline = actions.map(c => c.query || c.name).join('；');
 if (!headline) headline = final ? valueText(doc.view?.parsed_answer) || '查看原始最终输出' : notices.length ? valueText(notices.find(f => f.name==='content')?.value) : errors.length ? valueText(errors[0].value) : '查看已记录的模型回复';
 return {position,events,all,kind,label,headline,actions,outputs,visible,returns,inputs,errors,notices,docs,flags,
  inputRecorded:inputs.length>0,visibleRecorded:visible.length>0,returnRecorded:returns.length>0,
  rawIndices:events.map(e => e.index),pointer:events[0]?.pointer,
  modelCount:modelEvents.length,toolCount:toolEvents.length,
  timestamp:all.find(f => f.name === 'timestamp')?.value,
  latency:all.find(f => f.name === 'latency_ms')?.value};
}
export function buildTrace(doc, streamIndex = 0) {
 const streams = doc?.view?.trajectories || [];
 const stream = streams[streamIndex];
 const events = stream?.steps || [];
 const grouped = [];
 for (let i = 0; i < events.length; i++) {
  const e = events[i], group = [e];
  if (isModel(e)) while (i+1 < events.length && related(e,events[i+1])) group.push(events[++i]);
  grouped.push(makeGroup(group,grouped.length,doc));
 }
 const pastQueries = new Map(), pastDocs = new Map();
 for (const step of grouped) {
  for (const c of step.actions) {
   if (!/search|retrieve/i.test(c.name)) continue;
   const key = actionKey({...c,arguments:obj(c.arguments)?{...c.arguments,query:typeof c.arguments.query==='string'?normalizeText(c.arguments.query):c.arguments.query}:c.arguments});
   if (pastQueries.has(key)) step.flags.push({type:'warn',label:`查询重复 · 第 ${pastQueries.get(key)+1} 步`,related:pastQueries.get(key),detail:'调用名称和参数一致（query 忽略多余空白）；不等于无效搜索。'});
   else pastQueries.set(key,step.position);
  }
  const fresh = [];
  for (const d of step.docs) {
   const key = normalizeText(d.text);
   if (key.length < 24) continue;
   if (pastDocs.has(key)) d.repeatedFrom = pastDocs.get(key);
   else fresh.push([key,step.position]);
  }
  const repeated = step.docs.filter(d => hasValue(d.repeatedFrom));
  if (repeated.length) step.flags.push({type:'warn',label:`${repeated.length} 段可见文本重复`,related:repeated[0].repeatedFrom,detail:'文本与前面步骤一致（仅忽略空白），不以文档 ID 重复代替内容重复。'});
  for (const [k,v] of fresh) pastDocs.set(k,v);
 }
 const last = grouped.at(-1), view = doc?.view || {};
 if (last && hasValue(view.termination_reason) && !valueText(view.parsed_answer).trim()) last.flags.push({type:'warn',label:'结束但未提取到答案',detail:'查看终止原因、原始输出与解析答案；不直接断言没有生成答案内容。'});
 return {groups:grouped,stream,streams,rawStepCount:events.length,doc};
}

function signature(group) {
 return canonical({kind:group.kind,
  actions:group.actions.map(c => [c.name,c.arguments]),
  visible:group.visible.map(f => normalizeText(valueText(readable(f.value)))),
  returned:group.returns.map(f => normalizeText(valueText(readable(f.value)))),
  // With no action, compare the actual event text, not an invented summary.
  text:group.actions.length ? null : [...group.outputs,...group.notices,...group.errors].map(f => valueText(f.value))});
}
export function firstDifference(a, b) {
 if (!a || !b) return null;
 let same = 0;
 while (same < a.groups.length && same < b.groups.length && signature(a.groups[same]) === signature(b.groups[same])) same++;
 if (same === a.groups.length && same === b.groups.length) return {identical:true,prefix:same,a:null,b:null};
 return {identical:false,prefix:same,a:same<a.groups.length?same:null,b:same<b.groups.length?same:null};
}
export function sameQuestion(a, b) {
 return !!a && !!b && !!a.view?.sample_id && a.view.sample_id === b.view?.sample_id && a.view.dataset === b.view?.dataset && a.view.question === b.view?.question;
}
export function recordedGroup(a,b) {
 const x=a?.scores?.em,y=b?.scores?.em;
 if (!hasValue(x) || !hasValue(y)) return 'missing';
 return x===0&&y===1?'rescued':x===1&&y===0?'harmed':x===1&&y===1?'both_correct':x===0&&y===0?'both_wrong':'missing';
}
