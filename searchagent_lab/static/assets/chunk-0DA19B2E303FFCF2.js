/** Entropy discovery is presentation only; it never creates evidence or results. */
import {node,button,badge,card,controls,modal} from './chunk-F51FD76F14331921.js';

function perspectives(){
 const split=node('div','en-guide-splits');
 for(const[title,description]of [
  ['Agent 执行评测','按评测、轨迹和 Step 查看熵记录，核对运行时干预与成本。'],
  ['RL 训练评测','查看熵定义、观测与训练干预，核对实际采样、完成更新和 checkpoint 变化。'],
 ]){const part=node('div');part.append(node('strong','',title),node('p','s-muted',description));split.append(part);}
 return split;
}

export function entropyGuide(ctx,{empty=false,create}={}){
 const box=card('en-guide');box.dataset.entropyDiscovery='direction';
 const head=node('div','en-guide-head');head.append(node('h2','','执行观测与训练效果'),badge(empty?'尚未接入实验':'按实验查看','muted'));box.append(head);
 box.append(node('p','s-muted',empty?'此方向尚未接入实验。新建后，可继续使用 SDK 或结果导入流程接入记录。':'熵记录随实验和运行保存，在下面两种评测视角中查看。'),perspectives());
 box.append(node('p','en-guide-note','受控评测：固定前缀对照 · 错误识别能力 · 同预算干预收益。'));
 const actions=controls();actions.classList.add('en-guide-actions');
 if(empty&&!ctx.session.read_only&&create)actions.append(button('＋ 新建 Entropy 实验',create,'s-btn primary'));
 actions.append(button('查看 Entropy 接入说明',()=>openEntropyIntegration(),'s-btn'));box.append(actions);
 return box;
}

export function entropyPending(ctx,{view,scope={}}){
 const training=view==='training',box=card('en-guide en-pending');box.dataset.entropyDiscovery=training?'training':'execution';
 const head=node('div','en-guide-head');head.append(node('h2','',training?'Entropy 训练记录待接入':'Entropy 执行记录待接入'),badge('未记录','muted'));box.append(head);
 box.append(node('p','s-muted',training?
  (scope.run_id?'接入当前运行的熵定义、观测和训练使用记录后，这里将显示干预是否被采样、是否完成更新。':'实验已创建。接入训练运行后，在这里查看熵观测、训练干预和 checkpoint 对照。'):
  (scope.run_id?'接入当前运行的正式评测与熵记录后，这里将按选中的评测和原始 Step 展示。':'实验已创建。接入运行与正式评测后，在这里查看执行中的熵记录、作用位置和成本。')));
 box.append(button('查看 Entropy 接入说明',()=>openEntropyIntegration(scope),'s-btn'));
 return box;
}

export function openEntropyIntegration(scope={}){
 const host=modal('Entropy 接入说明'),body=node('div','en-integration');host.append(body);
 body.append(node('p','s-muted','先登记实验与运行，再接入实际保存的记录。接入后刷新页面，即可在对应实验内查看。'));
 const steps=node('ol');
 for(const text of [
  '在 Entropy 方向新建实验；已有实验可通过“归类”选择 Entropy。此分类只决定浏览位置。',
  '沿用现有运行与 checkpoint 身份，用 SDK 保存熵记录，或导入已经导出的结果包。',
  'Agent 执行评测查看正式评测与逐步轨迹；RL 训练评测查看训练诊断和受控比较。',
 ])steps.append(node('li','',text));body.append(steps);
 const sdk=node('details','s-fold');sdk.append(node('summary','','SDK 最小接入'));
 sdk.append(node('p','s-muted','在实际运行所在的数据目录记录。将 actual_* 替换为训练器真实保存的数据；未保存的内容保持未知。'));
 const run=JSON.stringify(scope.run_id||'your-actual-run'),sample=node('pre'),code=node('code','',
  'from searchagent_lab.methods.recorder import MethodEvidenceRecorder\n\n'+
  'with MethodEvidenceRecorder(\n'+
  '    data_dir="your-data-dir",\n'+
  '    scope={"run_id": '+run+'},\n'+
  '    usage="training",\n'+
  ') as log:\n'+
  '    log.entropy_definition("entropy-def-v1", actual_definition)\n'+
  '    log.entropy_observation("entropy-step-12", actual_observation)\n'+
  '    log.entropy_intervention("entropy-selection-12", actual_selection)');
 sample.append(code);sdk.append(sample,node('p','s-muted','定义保存计算口径；观测绑定实际 checkpoint 与场景。执行观测还需绑定正式 evaluation / trajectory / Step，训练采样和完成更新分别记录。完整字段与 veRL / RAGEN 等接法见源码 docs/v096/INTEGRATION_ZH.md。'));body.append(sdk);
 const demo=node('details','s-fold');demo.append(node('summary','','查看独立合成示例'));
 demo.append(node('p','s-muted','示例仅演示页面与接入，不代表真实训练结果。在下载包的 source 目录运行，并使用尚不存在的新目录：'));
 const command=node('pre');command.append(node('code','',
  'python examples/entropy_tracking_example.py --data-dir /tmp/new-entropy-demo\n'+
  'python -m searchagent_lab serve --data-dir /tmp/new-entropy-demo --port 8766 --open'));
 demo.append(command);body.append(demo);
}
