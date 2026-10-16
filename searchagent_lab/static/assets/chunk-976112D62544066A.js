import {node,button,modal,jsonModal,controls,badge} from './chunk-F51FD76F14331921.js';
import {recordedTokenFields,trainingNumber} from './chunk-DB3887FF11C2DF88.js';
const present=value=>value!==undefined&&value!==null;
export function showSupervisionSample(sample,batch,back){
 const body=modal('SFT 监督样本 · '+(sample.id??'未记录 ID'));
 body.append(controls(button('← 返回本步批次',back,'s-link'),badge('真实监督记录'),node('span','s-muted','step '+batch.step)));
 body.append(node('p','s-muted','这里只展示训练时保存的示范与监督；不是模型自主执行，也不计入固定题成绩。'));
 const fields=[['model_input','模型实际输入'],['messages','保存的 messages（未声称等于最终模型输入）'],['teacher_output','教师原始示范'],['supervision_target','实际监督目标'],['labels','实际 labels'],['input_ids','实际 input_ids'],['loss_mask','实际 loss_mask']];
 for(const [key,title] of fields){
  const box=node('section','cl-recorded-section');box.append(node('h3','',title));
  if(present(sample[key]))box.append(node('pre','tb-plain',typeof sample[key]==='string'?sample[key]:JSON.stringify(sample[key],null,2)));
  else box.append(node('span','s-muted','未记录；不从教师文本或其他字段重建。'));
  body.append(box);
 }
 body.append(node('p','s-muted',recordedTokenFields(sample)+' · Loss '+trainingNumber(sample.loss)),button('完整原始样本与来源',()=>jsonModal('SFT 原始记录',{batch_id:batch.id,step:batch.step,dataset_version:batch.dataset_version,source:batch.source,sample}),'s-link'));
}
export function trainingFormatHelp(){
 const body=modal('训练记录接入 · 保留原始字段');
 body.append(node('p','s-muted','新 run 可明确登记 training_stage="sft" 或 "rl"；旧 run 不必覆盖，可在新接入的 batch 上登记。OPD、Skill、Entropy 仍是方法，不是训练阶段。'));
 body.append(node('pre','tb-plain',`# 在实际训练导出端记录，不从固定开发题生成示范
with StudioRecorder(DATA_DIR) as recorder:
    recorder.log_sft_batch(
        run_id=RUN_ID, batch_id=BATCH_ID, step=STEP,
        dataset_version=TRAIN_DATA_VERSION,
        samples=ACTUAL_EXPORTED_SAMPLES)

# RL 继续使用已有接口，training_stage 为可选明确字段
recorder.log_training_batch({
    "id": BATCH_ID, "run_id": RUN_ID, "step": STEP,
    "training_stage": "rl", "groups": ACTUAL_ROLLOUT_GROUPS
})`));
 body.append(node('p','s-muted','完整说明：source/docs/CLARITY_V0100.md。缺失 input、labels、mask、reward 或 advantage 时保持缺失，不自动补零。'));
}
