/** Optional offline production builder. No eval; CSP-safe, split native chunks.
 * Uses an already-installed TypeScript compiler solely to lower module syntax.
 * Normal builds continue to use the existing pinned esbuild build script.
 * Runtime has no Node/TypeScript dependency. Third-party notices stay bundled.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const compiler=process.env.SEARCHAGENT_TYPESCRIPT_PATH;
const ts=compiler?require(compiler):require('typescript');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),base=path.join(root,'searchagent_lab/static'),out=path.join(base,'assets');
const entry='studio/app.js',modules=new Map(),dynamicRoots=new Set();
const resolve=(from,spec)=>{if(!spec.startsWith('.'))throw Error('Only local source imports supported: '+spec);return path.posix.normalize(path.posix.join(path.posix.dirname(from),spec));};
async function inspect(id){
 if(modules.has(id))return;
 const text=await fs.readFile(path.join(base,id),'utf8'),sf=ts.createSourceFile(id,text,ts.ScriptTarget.ES2022,true,ts.ScriptKind.JS),record={text,static:[],dynamic:[]};modules.set(id,record);
 function visit(n){
  if((ts.isImportDeclaration(n)||ts.isExportDeclaration(n))&&n.moduleSpecifier){const spec=n.moduleSpecifier.text;record.static.push(resolve(id,spec));}
  if(ts.isCallExpression(n)&&n.expression.kind===ts.SyntaxKind.ImportKeyword){if(n.arguments.length!==1||!ts.isStringLiteral(n.arguments[0]))throw Error('Nonliteral dynamic import in '+id);const target=resolve(id,n.arguments[0].text);record.dynamic.push(target);dynamicRoots.add(target);}
  if(ts.isMetaProperty(n))throw Error('import.meta requires the standard esbuild builder: '+id);
  ts.forEachChild(n,visit);
 }
 visit(sf);for(const dep of [...record.static,...record.dynamic])await inspect(dep);
}
await inspect(entry);
function closure(id,result=new Set()){if(result.has(id))return result;result.add(id);for(const dep of modules.get(id).static)closure(dep,result);return result;}
const mainSet=closure(entry),digest=crypto.createHash('sha256').update([...modules].map(([k,v])=>k+'\n'+v.text).sort().join('\n')).digest('hex').slice(0,12).toUpperCase();
const mainName='main-'+digest+'.js',chunkMap={};for(const id of dynamicRoots)if(!mainSet.has(id))chunkMap[id]='chunk-'+crypto.createHash('sha256').update(digest+id).digest('hex').slice(0,12).toUpperCase()+'.js';
function compile(id){
 const result=ts.transpileModule(modules.get(id).text,{fileName:id,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,removeComments:false},transformers:{before:[context=>source=>{
  const visit=n=>ts.isCallExpression(n)&&n.expression.kind===ts.SyntaxKind.ImportKeyword?ts.factory.createCallExpression(ts.factory.createIdentifier('__load'),undefined,n.arguments):ts.visitEachChild(n,visit,context);
  return ts.visitNode(source,visit);
 }]}});
 return JSON.stringify(id)+':function(module,exports,require,__load){\n'+result.outputText+'\n}';
}
const factories=ids=>'{\n'+[...ids].map(compile).join(',\n')+'\n}';
const runtime=`// Offline local-module build; see THIRD_PARTY_NOTICES.md.\nconst factories=Object.create(null),cache=Object.create(null),pending=Object.create(null);\nconst chunks=${JSON.stringify(chunkMap)};\nexport function register(items){for(const [id,factory] of Object.entries(items))if(!factories[id])factories[id]=factory;}\nfunction resolve(from,spec){const parts=(from.slice(0,from.lastIndexOf('/')+1)+spec).split('/'),out=[];for(const p of parts){if(p==='.'||!p)continue;if(p==='..')out.pop();else out.push(p);}return out.join('/');}\nfunction requireModule(id){if(cache[id])return cache[id].exports;if(!factories[id])throw Error('Module not registered: '+id);const m={exports:{}};cache[id]=m;try{factories[id](m,m.exports,s=>requireModule(resolve(id,s)),s=>load(resolve(id,s)));return m.exports;}catch(e){delete cache[id];throw e;}}\nasync function load(id){if(!factories[id]){if(!chunks[id])throw Error('Missing local chunk: '+id);await(pending[id]||(pending[id]=import('./'+chunks[id]).catch(e=>{delete pending[id];throw e;})));}return requireModule(id);}\n`;
await fs.mkdir(out,{recursive:true});for(const name of await fs.readdir(out))if(/^(main|shell|chunk)-[A-Z0-9]+\.(js|css)$/.test(name))await fs.unlink(path.join(out,name));
await fs.writeFile(path.join(out,mainName),runtime+'register('+factories(mainSet)+');\nrequireModule('+JSON.stringify(entry)+');\n');
for(const [id,name] of Object.entries(chunkMap)){
 const ids=[...closure(id)].filter(dep=>!mainSet.has(dep));await fs.writeFile(path.join(out,name),`import {register} from './${mainName}';\nregister(${factories(ids)});\n`);
}
async function css(id,seen=new Set()){
 if(seen.has(id))return '';seen.add(id);const input=await fs.readFile(path.join(base,id),'utf8');let output='',last=0;
 for(const m of input.matchAll(/@import\s+['"]([^'"]+)['"];?/g)){output+=input.slice(last,m.index)+await css(resolve(id,m[1]),seen);last=m.index+m[0].length;}return output+input.slice(last);
}
const styles=await css('studio/shell.css'),cssName='shell-'+crypto.createHash('sha256').update(styles).digest('hex').slice(0,12).toUpperCase()+'.css';await fs.writeFile(path.join(out,cssName),styles);
const index=path.join(base,'index.html');let html=await fs.readFile(index,'utf8');html=html.replace(/<link\b[^>]*rel="(?:stylesheet|modulepreload)"[^>]*>\s*/g,'').replace(/<script type="module"[^>]*><\/script>/g,'');html=html.replace('</head>',`<link rel="stylesheet" href="/assets/${cssName}">\n<script type="module" src="/assets/${mainName}"></script>\n</head>`);await fs.writeFile(index,html);
const files=await Promise.all([mainName,cssName,...Object.values(chunkMap)].map(async name=>({name,bytes:(await fs.stat(path.join(out,name))).size})));
const manifest={builder:'offline-typescript-'+ts.version,mode:'csp-safe-split-local-modules',main:'/assets/'+mainName,css:'/assets/'+cssName,preload:[],files};await fs.writeFile(path.join(out,'build-manifest.json'),JSON.stringify(manifest,null,2)+'\n');console.log(JSON.stringify({main:manifest.main,mainBytes:files[0].bytes,chunks:Object.keys(chunkMap).length,modules:modules.size,cssBytes:files[1].bytes}));
