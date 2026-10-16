/** Optional production build. Compiled assets are included in the Python wheel.
 * `npm install && npm run build`; Node is not needed to install or run the viewer.
 * Independent pages are split; only the entry's static dependencies are preloaded.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const esbuild=process.env.SEARCHAGENT_ESBUILD_PATH?
 await import(pathToFileURL(process.env.SEARCHAGENT_ESBUILD_PATH).href):await import('esbuild');
const assetDir=path.join(root,'searchagent_lab/static/assets');
const result=await esbuild.build({absWorkingDir:root,entryPoints:{main:'searchagent_lab/static/studio/app.js',shell:'searchagent_lab/static/studio/shell.css'},
 outdir:assetDir,bundle:true,splitting:true,format:'esm',platform:'browser',target:['es2022'],minify:true,
 metafile:true,write:false,entryNames:'[name]-[hash]',chunkNames:'chunk-[hash]',legalComments:'inline'});
await fs.mkdir(assetDir,{recursive:true});
// Only remove files owned by this build. Source modules and unrelated files stay intact.
for(const name of await fs.readdir(assetDir))if(/^(main|shell|chunk)-[A-Z0-9]+\.(js|css)$/.test(name))await fs.unlink(path.join(assetDir,name));
for(const f of result.outputFiles)await fs.writeFile(f.path,f.contents);
const outputs=result.metafile.outputs,entries=Object.entries(outputs);
const main=entries.find(([,v])=>v.entryPoint==='searchagent_lab/static/studio/app.js')?.[0];
const css=entries.find(([,v])=>v.entryPoint==='searchagent_lab/static/studio/shell.css')?.[0];
if(!main||!css)throw new Error('Expected main JS and shell CSS entry outputs');
const preload=new Set();function visit(key){for(const imp of outputs[key]?.imports||[]){if(imp.kind!=='import-statement'||imp.external||preload.has(imp.path))continue;preload.add(imp.path);visit(imp.path);}}visit(main);
const url=p=>'/assets/'+path.basename(p);
const indexPath=path.join(root,'searchagent_lab/static/index.html');let html=await fs.readFile(indexPath,'utf8');
html=html.replace(/<link\b[^>]*rel="(?:stylesheet|modulepreload)"[^>]*>\s*/g,'').replace(/<script type="module"[^>]*><\/script>/g,'');
const tags=[`<link rel="stylesheet" href="${url(css)}">`,...[...preload].map(p=>`<link rel="modulepreload" href="${url(p)}">`),`<script type="module" src="${url(main)}"></script>`].join('\n');
html=html.replace('</head>',tags+'\n</head>');await fs.writeFile(indexPath,html);
await fs.writeFile(path.join(assetDir,'build-manifest.json'),JSON.stringify({builder:'esbuild 0.28.2',main:url(main),css:url(css),preload:[...preload].map(url),files:result.outputFiles.map(f=>({name:path.basename(f.path),bytes:f.contents.length}))},null,2)+'\n');
console.log(JSON.stringify({main:url(main),css:url(css),preload:preload.size,files:result.outputFiles.length,bytes:result.outputFiles.reduce((s,f)=>s+f.contents.length,0)}));
