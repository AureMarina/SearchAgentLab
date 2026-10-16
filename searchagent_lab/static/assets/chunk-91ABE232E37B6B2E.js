/** Local-only font loading. Opening the viewer never contacts a font CDN. */
const family='SearchAgent YuKai';
const locals=['TsangerYuKai-Regular','TsangerYuKai Regular','仓耳玉楷 标准','倉耳玉楷 標準','TsangerYuKai'];
let status={loaded:false,source:null,label:'系统字体 · 仓耳玉楷未载入'};
export function uiFontStatus(){return {...status};}
function activate(face,source){
 document.fonts.add(face);
 document.documentElement.dataset.uiFont='TsangerYuKai';
 status={loaded:true,source,label:'仓耳玉楷 · 已载入'};
 return uiFontStatus();
}
export async function loadUIFont(){
 if(typeof FontFace==='undefined'||!document.fonts)return uiFontStatus();
 try{
  const localFace=new FontFace(family,locals.map(name=>'local("'+name+'")').join(','),{style:'normal',weight:'400',display:'swap'});
  await localFace.load();return activate(localFace,'system');
 }catch{}
 try{
  const response=await fetch('/api/ui/font',{credentials:'same-origin'});
  if(!response.ok)return uiFontStatus();
  const info=await response.json();
  // Accept only the viewer's fixed local resource, never an arbitrary remote URL.
  if(!info.available||info.url!=='/fonts/TsangerYuKai-Regular.ttf')return uiFontStatus();
  const installed=new FontFace(family,'url("/fonts/TsangerYuKai-Regular.ttf") format("truetype")',{style:'normal',weight:'400',display:'swap'});
  await installed.load();return activate(installed,'workspace');
 }catch{return uiFontStatus();}
}
