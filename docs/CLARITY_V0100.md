# 0.10.0 训练证据与页面组织

## 统一的对象关系

原有 Project → Experiment → Run → Checkpoint → Evaluation 不变。执行页的三种视图使用同一题单、A/B 与题目；训练批次属于明确的 run/step，不凭题目相似度关联到开发题。诊断摘要只是既有 inspection 规则与 review 的小视图，不引入新诊断表或自动根因推断。

### 字段与输入边界

`training_stage` 的允许值：`sft`、`rl`、`mixed`、`unknown`。缺失等于未登记，不从名称推断。run 顶层 / run.config / batch 的明确阶段声明不一致时，显示混合或声明不一致，不覆盖历史元数据。

`training_dataset_version` 可登记在 run 或 run.config；SFT batch 可有 dataset_version 与 source。字段不作为实际训练使用的自动证明，必须由训练端如实导出。旧 run 是不可变记录，不为补阶段而覆盖它；可在新 batch 登记。

## SFT：实际导出端接入

```python
from searchagent_lab.studio.sdk import StudioRecorder

def record_sft_step(data_dir, run_id, batch_id, step, samples, dataset_version):
    # run 应已登记。samples 是训练端实际导出的示范，不从开发题制造。
    with StudioRecorder(data_dir) as recorder:
        return recorder.log_sft_batch(
            run_id=run_id,
            batch_id=batch_id,
            step=step,
            samples=samples,
            dataset_version=dataset_version,
        )
```

每个 sample 可选保存：id、model_input（实际模型输入）、messages（原样 messages，未宣称等于最终输入）、teacher_output、supervision_target、input_ids、labels、loss_mask、loss、来源字段。SDK 会显式登记 record_purpose=sft_demonstration；不会 tokenize、重建 mask、读取固定题或生成教师回复。

若提供 input_ids，则本格式要求 labels / loss_mask 与其完整序列长度一致。labels 允许 -100；loss_mask 为整数 0/1 数组。response-only 监督应由导出端转换成明确的 full-sequence 表示后再接入；不要在查看器猜测对齐。各可选字段可以不提供。未提供的字段不会被补零或补空数组。

sample 的显式用途若与监督示范冲突则拒绝；来自 Agent/checkpoint 评测容器的轨迹不会因被引用而转为训练采样。记录不可变，同 batch_id 相同内容可幂等重传；内容改变需要新的 ID。

## RL：沿用原始采样组接口

```python
from searchagent_lab.studio.sdk import StudioRecorder

def record_rl_step(data_dir, run_id, batch_id, step, actual_groups):
    with StudioRecorder(data_dir) as recorder:
        return recorder.log_training_batch({
            "id": batch_id, "run_id": run_id, "step": step,
            "training_stage": "rl", "groups": actual_groups,
        })
```

group.samples 可以保存实际 reward / advantage / loss / token_ids / response_mask / loss_mask / steps 或有来源的 trace_ref。查看器只统计已记录标量，不从答对/答错推算奖励和优势。非零优势为 0 不等于所有梯度为 0，界面不会据此声明“无更新”。

## 问题摘要与人工复核

结果页只为当前可见页批量请求摘要；接口 GET `/api/inspection/summaries?evaluation=...&ids=<JSON array>` 最多 50 个轨迹 ID，先检查所有 ID 的评测归属，再读取已有规则与复核，不运行模型/评分器。原始轨迹是不可变输入。

轨迹页展开摘要后可选人工复核：确认观察 / 待进一步核查 / 驳回当前线索。写入仍使用现有 POST inspection/reviews，要求有效 source_hash。后来的驳回会覆盖相应线索的摘要展示，不删除历史复核记录；没有线索不等于已证明正确。

不新增错题中心、SFT/RL bad-case 子菜单或自动训练任务；不自动生成修复样本。根因假设、验证记录仍可通过原始日志/现有工具查看。

## 历史兼容与范围

旧 view=trace / trace-compare / skill / opd / entropy / selfplay 链接继续解析；旧 trace_filter、trace_q 等参数迁移为共享状态。执行页与训练页共用 query state，不修改服务端评分口径。切换题单清除旧题目和 A/B；同题单切 B 保留题目、清除不适用的具体步骤。

本次没有更换 Search Agent、采样器或 RL 实现，不提供未记录的学习证据，不把 11 题诊断结果混成七数据集全量 EM。
