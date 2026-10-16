/** Lucide 0.468.0 subset (ISC; Feather-derived portions MIT). See vendor licenses. */
const NS='http://www.w3.org/2000/svg';
const icons={
 'layout-grid':[['rect',{width:7,height:7,x:3,y:3,rx:1}],['rect',{width:7,height:7,x:14,y:3,rx:1}],['rect',{width:7,height:7,x:14,y:14,rx:1}],['rect',{width:7,height:7,x:3,y:14,rx:1}]],
 compare:[['circle',{cx:5,cy:6,r:3}],['path',{d:'M12 6h5a2 2 0 0 1 2 2v7'}],['path',{d:'m15 9-3-3 3-3'}],['circle',{cx:19,cy:18,r:3}],['path',{d:'M12 18H7a2 2 0 0 1-2-2V9'}],['path',{d:'m9 15 3 3-3 3'}]],
 list:[['path',{d:'m3 17 2 2 4-4'}],['path',{d:'m3 7 2 2 4-4'}],['path',{d:'M13 6h8'}],['path',{d:'M13 12h8'}],['path',{d:'M13 18h8'}]],
 flask:[['path',{d:'M14 2v6a2 2 0 0 0 .245.96l5.51 10.08A2 2 0 0 1 18 22H6a2 2 0 0 1-1.755-2.96l5.51-10.08A2 2 0 0 0 10 8V2'}],['path',{d:'M6.453 15h11.094'}],['path',{d:'M8.5 2h7'}]]
};
export function icon(name){const s=document.createElementNS(NS,'svg');for(const[k,v]of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':2,'stroke-linecap':'round','stroke-linejoin':'round',class:'s-icon','aria-hidden':'true'}))s.setAttribute(k,v);for(const[tag,attrs]of icons[name]||icons.flask){const e=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);s.append(e);}return s;}
