> 0.3.0 保留以下正式研究/评测接入协议。直接检查普通 JSONL 无需转换成此协议；见 README_ZH.md 和 ARCHITECTURE_V3.md。

# 接到现有 veRL + search_agent 工程 · 0.2.0

本包是独立的记录/查看层。保持你的模型请求、search/open_document/find_in_document 循环、retriever、parser、scorer、reward、checkpoint 保存流程不变。优先用现有日志做离线转换，不为调界面重跑训练，也不补造缺失的逐步输入。

## 1. 先建立项目归属

可以从不含运行或结果的 `examples/workspace_metadata.template.json` 开始，按实际方案补入 `experiments` 和 `runs`。v2 bundle 中的运行必须提供 `experiment_id`；仅有 `project_id` 标签不够。

```python
from searchagent_lab import Recorder

with Recorder('./workspace/real') as rec:
    rec.register_project(id='latent', name='Latent')
    rec.register_experiment(
        id='latent-e02', project_id='latent', name='E02 · Persistent latent',
        objective='填写待验证的问题', change='填写具体改动',
        constants='填写保持一致的条件', config={'recipe_version': '实际版本'},
        metric_definitions={
            'latent/carry_norm': {
                'label': 'Carry state L2',
                'definition': '此处填写实际计算式和平均范围，不用示意定义替代真实实现',
                'scope': '实际计算位置', 'unit': '实际单位'
            }
        },
    )
    rec.register_run(
        id='latent-e02-s42-run001', name='seed42 · run001',
        experiment_id='latent-e02', seed=42,
        base_model='Qwen3-4B-Instruct-2507', model_revision='实际模型 revision',
        config={'git_commit': '实际 commit', 'train_data_version': '实际版本'},
    )
```

同名指标不一定同定义。metric_definitions 可设在 project / experiment / run，越具体的层级优先。只显示你实际记录的数字，不由界面自行计算熵或 carry 状态。

这些元数据是不可变实验档案，重复登记必须一致。换方案用新实验，换运行用新 run；人工发现和结论用追加的实验记录，不改原配置。示例中“实际版本”等占位需要替换；比较口径未知请使用 `unknown`，不会被当成完整协议。

## 2. 先试最小合成 bundle

```bash
python -m searchagent_lab import examples/import_bundle_v2.json --data-dir ./workspace/import-demo
python -m searchagent_lab serve --data-dir ./workspace/import-demo --port 8765
```

该文件有一个合成研究项目、一个实验、一条运行、两个 checkpoint、四条合成轨迹。它只验证接入，不是从真实基准抽来的验证题。

`examples/import_bundle.json` 保留旧 v1 格式，导入后进入“未归类”；新项目使用 v2。

## 3. 显式转换你的 JSONL 日志

`examples/mapping.example.json` 的键是目标字段，值是原记录的键路径数组：

```json
{
  "dataset": ["dataset"],
  "sample_id": ["id"],
  "scores.em": ["scores", "em"],
  "raw_final_output": ["final_output"],
  "parsed_answer": ["parsed_answer"],
  "steps": ["steps"]
}
```

`steps` 必须已是文档所述的逐步结构。旧日志若使用不同结构，单独写转换逻辑，逐个映射真实保存的输入、输出、tool_call、tool_response、model_visible。缺失字段保留未知，不能用最终 messages 倒推最初 prompt，也不能把未截断工具返回冒充模型看到的内容。

完整原记录保留在 `source_record`。adapter 不算分、不解析答案、不改归属、不补调用；真实结果为 EM 0/1，不是百分数。

用合成文件验证转换器：

```bash
python examples/convert_existing_results.py \
  --panel examples/fixed_panel_v2.example.json \
  --results examples/raw_results.example.jsonl \
  --mapping examples/mapping.example.json \
  --protocol examples/protocol_v2.synthetic.json \
  --project-id adapter-demo --experiment-id adapter-demo-e01 \
  --run-id adapter-demo-s42 --checkpoint-id adapter-demo-s42-step100 \
  --evaluation-id adapter-demo-s42-step100-eval01 \
  --step 100 --seed 42 --base-model 'No actual model loaded' \
  --model-revision synthetic-ui-fixture-v2 \
  --out converted_example.json --complete --demo

python -m searchagent_lab import converted_example.json --data-dir ./workspace/converted
```

真实接入时替换 panel/results/mapping/protocol，去掉 `--demo`。合成 panel 不允许去掉 demo 冒充真实数据。`--complete` 只表示你声称完整，导入器仍核验每一题覆盖；重复题/缺题不能伪装成一轮完整评测。

对已有复杂项目元数据，可追加 `--workspace-metadata metadata.json`，从该文件读取与给定 project/experiment/run ID 对应的完整不可变对象，而不是用默认短元数据重新登记。文件结构为 `projects/experiments/runs` 数组。`examples/metadata.synthetic.json` 可配合它包含的 `example-latent / example-latent-e01 / example-run` ID 试用。

同一 run 的多 checkpoint 可以分次导入，但所有登记元数据必须一致，包括运行状态。已完成运行应在 metadata 中写实际 `status`，不要用默认 running 重新注册；也可以在独立 JSONL 中只登记新 checkpoint/evaluation，不再发送 run。每次复测使用新 evaluation ID，输出文件也不覆盖旧文件。

## 4. 实时 loss/reward 接入

父记录登记一次；实际训练的 rank-0 日志出口调用：

```python
# actual_step / actual_metrics 来自真实训练日志；不是本包计算的值。
rec.log_metrics(run_id, actual_step, actual_metrics)
```

支持原始数值和 null，不收 Tensor，不替训练端做 reduce，不对 loss 做平滑后覆盖原值。同一个 run/step/metric 的冲突值被拒绝。CSV 可用：

```bash
python -m searchagent_lab import-metrics metrics.csv --run your-real-run \
  --step-key step --data-dir ./workspace/real
```

单独体验写入与自动刷新：

```bash
# 终端 A：完全合成的数值，不是训练脚本。
python examples/live_logger.py --data-dir ./workspace/live-demo --steps 40 --interval 1
# 终端 B
python -m searchagent_lab serve --data-dir ./workspace/live-demo
```

启用页面的“自动刷新”；训练总览会定时重读，轨迹页不自动刷新以免打断人工核查。该示例生成自己的演示项目，不会污染 Latent/Self-play 的数据。

## 5. 一次真实评测的记录生命周期

下面的所有 `actual_*` 和 ID 均来自你的已有配置/结果。先登记项目、实验、运行，再执行这些记录方法：

```python
with Recorder('./workspace/real') as rec:
    rec.register_panel(actual_frozen_panel)
    rec.register_checkpoint(
        id=checkpoint_id, run_id=run_id, step=actual_step,
        artifact_uri=actual_checkpoint_path, weights_sha256=actual_weights_hash,
    )
    rec.start_evaluation(
        id=evaluation_id, checkpoint_id=checkpoint_id,
        panel_id=actual_frozen_panel['id'], protocol=actual_protocol,
    )
    for trajectory in already_recorded_evaluation_results:
        rec.log_trajectory(evaluation_id, trajectory)
    rec.finish_evaluation(evaluation_id)
```

请复用真实 checkpoint 元数据；如果之前登记时没有 weights_sha256，不能用同一 ID 增补不同内容，需在初次登记前把元数据准备齐。记录权重哈希不代表查看器加载并验证过权重。

标准答案通过固定 panel 查到。日志显式传入 question/gold_answers 时必须与 panel 一致。参考证据可事后查看，不会由看板发送给 agent。

固定推理协议可从 `examples/protocol_v2.template.json` 填写；里面的 unknown 必须替换为实际版本/参数才具备完整口径。mode=inference_only、groundtruth_to_agent=false 和每题/每轮重置是调用方契约，不代表查看器能检查远端是否真的执行。Persistent latent 可以在一道题内部跨轮保持，但下一题与下一次评测必须重置。

## 6. 最小真实验收

先用已有两个 checkpoint 的同批固定题结果接入。逐题核验看板与原始 CSV 的 EM、答案解析和异常标记一致，且总表没有缺题补零。检查错→对/对→错分组与实际同题 join 一致。

再由你现有评测器对同一 checkpoint、同一协议运行两次，每次独立从空状态开始；用不同 evaluation ID 导入后，在界面或 `repro-check` 比较保存的答案、评分、query、工具返回及模型可见输入。看板不会代你启动这两次运行。

最后登记实际训练/正式测试 manifests，运行 `audit-isolation`。后续 self-play 产物加入训练前需重新审查并归档报告，旧报告不是持续监控。

## 7. 限制

这是 Python 标准库 + 自带网页，不需要改动 veRL/Torch/vLLM 环境；推荐独立 venv。多 worker 先写独立 sidecar，由采集进程导入；不要把多机 NFS 并发写 SQLite 当成支持的部署模式。

不解析 W&B `.wandb` 二进制，不提供云同步/API key，不自动猜测仓库字段，也不从 checkpoint 生成完整历史曲线。没有真实日志的字段就明确“未记录”。测试用 Playwright/Chromium 不是用户运行依赖。
