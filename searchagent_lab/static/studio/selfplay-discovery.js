/** Self-play discovery never creates questions, learning signals or measured results. */
import {node,button,badge,card,controls,modal} from './common.js';

export function selfplayGuide(ctx,{empty=false,create}={}){
 const box=card('sp-guide');box.dataset.selfplayDiscovery='direction';
 const head=node('div','sp-guide-head');head.append(node('h2','','题目供给、训练信号与 Solver 能力'),badge(empty?'尚未接入实验':'按实验查看','muted'));box.append(head);
 box.append(node('p','s-muted',empty?'此方向尚未接入实验。新建后，沿用 SDK 或结果导入接入每轮生成题、训练使用和固定评测。':'先查看每轮新题实际进入训练的情况，再查看固定题集上的解题能力。'));
 const split=node('div','sp-guide-splits');
 for(const[title,description]of[
  ['Agent 执行评测','固定题集与评测协议，查看各个 Solver 的 EM、失败轨迹和执行成本。'],
  ['RL 训练评测','按轮次查看出题、验证、去重、入池、实际采样，以及 advantage、mask 与完成更新。'],
 ]){const part=node('div');part.append(node('strong','',title),node('p','s-muted',description));split.append(part);}box.append(split);
 box.append(node('p','sp-guide-note','两个固定参照：固定参考 Solver 看题目变化；固定评测集看 Solver 变化。'));
 const actions=controls();actions.classList.add('sp-guide-actions');
 if(empty&&!ctx.session.read_only&&create)actions.append(button('＋ 新建 Self-play 实验',create,'s-btn primary'));
 actions.append(button('查看 Self-play 接入说明',()=>openSelfplayIntegration(),'s-btn'));box.append(actions);return box;
}

export function selfplayPending(ctx,{view,scope={}}){
 const training=view==='training',box=card('sp-guide sp-pending');box.dataset.selfplayDiscovery=training?'training':'execution';
 const head=node('div','sp-guide-head');head.append(node('h2','',training?'Self-play 轮次记录待接入':'Self-play 固定评测待接入'),badge('未记录','muted'));box.append(head);
 box.append(node('p','s-muted',training?
  (scope.run_id?'接入当前运行的轮次、生成题与训练使用记录后，在这里查看新增有效题、学习信号和训练去向。':'实验已创建。接入运行后，按轮次查看生成题的验证、入池、复用、解答与更新。'):
  (scope.run_id?'接入独立于当轮生成题的固定评测，选择 Solver checkpoint 后查看 EM 与逐题轨迹。':'实验已创建。接入运行与固定评测后，在这里查看 Solver 能力随 checkpoint 的变化。')));
 box.append(button('查看 Self-play 接入说明',()=>openSelfplayIntegration(scope),'s-btn'));return box;
}

export function openSelfplayIntegration(scope={}){
 const host=modal('Self-play 接入说明'),body=node('div','sp-integration');host.append(body);
 body.append(node('p','s-muted','每轮记录题目从生成到训练更新的去向。被拒绝的题、验证器异常和未知状态都保留。'));
 const steps=node('ol');for(const text of[
  '在 Self-play 方向新建实验；已有实验可通过“整理分类”选择 Self-play。',
  '保存轮次与参与角色版本，使用稳定题目 ID 关联原题、答案、证据、验证、题池事件和多次解答。',
  '从训练器记录实际采样批次、advantage、有效 mask 与完成更新。Solver reward 单独保存。',
  '固定参考 Solver 比较各轮出题，固定外部题集比较各个 Solver。两个参照分别选择。',
 ])steps.append(node('li','',text));body.append(steps);
 const sdk=node('details','s-fold');sdk.append(node('summary','','SDK 接入记录'));
 sdk.append(node('p','s-muted','在实际运行的数据目录接入。actual_* 使用训练器真实保存的数据；未保存的字段保持未知。'));
 const sample=node('pre');sample.append(node('code','',
  'from searchagent_lab.methods.recorder import MethodEvidenceRecorder\n\n'+
  'with MethodEvidenceRecorder(\n'+
  '    data_dir="your-data-dir",\n'+
  '    scope={"run_id": '+JSON.stringify(scope.run_id||'your-actual-run')+'},\n'+
  '    usage="training",\n'+
  ') as log:\n'+
  '    log.selfplay_cycle("round-1", actual_round)\n'+
  '    log.generated_question("question-1", actual_question)\n'+
  '    log.question_review("review-1", actual_review)\n'+
  '    log.solver_attempt("attempt-1", actual_attempt)\n'+
  '    log.training_use("batch-use-1", actual_training_use)\n'+
  '    log.training_update("update-1", actual_update)'));
 sdk.append(sample,node('p','s-muted','完整轮次、题池、固定参照和预算对照字段见源码 docs/v098/INTEGRATION_ZH.md；记录通过现有结果包进入对应实验。'));body.append(sdk);
 const demo=node('details','s-fold');demo.append(node('summary','','查看独立合成示例'),node('p','s-muted','示例只演示接入与页面，不代表真实训练结果。使用尚不存在的新目录，具体命令见下载包中的使用说明。'));body.append(demo);
}
