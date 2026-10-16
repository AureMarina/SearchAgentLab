// Small, safe DOM helpers. Log contents never become HTML or executable markup.
export function node(tag, cls = '', text) {
  const e = document.createElement(tag); if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text); return e;
}
export function button(label, fn, cls = 'button small') {
  const e = node('button', cls, label); e.type = 'button'; e.onclick = fn; return e;
}
export function pretty(v) { return typeof v === 'string' ? v : JSON.stringify(v, null, 2); }
export function short(v, n = 95) { const t = v == null ? '未记录' : typeof v === 'string' ? v : JSON.stringify(v); return t.length > n ? t.slice(0,n) + '…' : t; }
export function tag(text, cls = '') { return node('span', 'ix-tag ' + cls, text); }
export function empty(host, text) { host.replaceChildren(node('div','ix-empty',text)); }
export function ptr(base, key) { return base + '/' + String(key).replaceAll('~','~0').replaceAll('/','~1'); }
export function highlight(host, value, query) {
  host.replaceChildren(); const text = String(value);
  if (!query) { host.textContent = text; return; }
  // Never insert HTML from model/tool content. Unicode case-insensitive literal match.
  const safe = query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  let last = 0, matches = 0;
  for (const m of text.matchAll(new RegExp(safe, 'giu'))) {
    host.append(document.createTextNode(text.slice(last,m.index)), node('mark','',m[0]));
    last=m.index+m[0].length; if (++matches>=500) break;
  }
  host.append(document.createTextNode(text.slice(last)));
}
export function saveJSON(value, name) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
  const a=node('a'); a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
