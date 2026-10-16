> 0.3.0 保留以下正式研究/评测接入协议。直接检查普通 JSONL 无需转换成此协议；见 README_ZH.md 和 ARCHITECTURE_V3.md。

# 数据协议 v2（兼容 v1 导入）

## 归属链与主键

`project → experiment → run → checkpoint → evaluation → trajectory`。

项目与实验是独立数据实体，不是模型/ckpt 的标签。底座、搜索环境和题单可复用，但每条评测只能属于一个真实 checkpoint。所有主实体 ID 在工作区全局唯一，字符范围维持 v1：ASCII 字母/数字/`_ - . :`，长度 1–180。展示名可以相同；`sample.id` 可以保留原始非空 ID 字符串。

SQLite 单独保存 `projects`、`experiments`、`run_ownership`；通过外键建立归属。旧 `runs/metrics/checkpoints/panels/evaluations/trajectories/annotations` 数据格式保留，新增字段在 read model 中组合。

## Bundle

```json
{
  "schema_version": 2,
  "projects": [{"id":"latent","name":"Latent"}],
  "experiments": [{"id":"lt-e02","project_id":"latent","name":"E02 · Persistent latent","config":{}}],
  "runs": [{"id":"lt-e02-s42-r001","experiment_id":"lt-e02","name":"seed42 · run001","seed":42,"base_model":"Qwen3-4B-Instruct-2507","model_revision":"ACTUAL_REVISION"}],
  "panels": [],
  "checkpoints": [],
  "metrics": [],
  "evaluations": [],
  "trajectories": [],
  "finish_evaluations": [],
  "references": [],
  "experiment_notes": []
}
```

父实体按顺序注册，单文件事务失败全部回滚。v2 run 必須有 `experiment_id`。如同时传入 `project_id`，须与父实验一致；checkpoint/evaluation 中重复声明的归属也必须与外键链一致。

v1 bundle 仍可导入，缺少归属的运行进 `legacy-unassigned`，每个旧 run 对应一个独立的旧实验，不推测研究方向。相同实体内容重复导入幂等；冲突需要新 ID。JSONL 用 `project/experiment/run/panel/checkpoint/metrics/evaluation/trajectory/evaluation_finish/run_status/reference/experiment_note` 事件，按依赖顺序出现。

## Project / Experiment / Run

项目可登记 `description/default_panel_id/metric_definitions`。实验可登记 `objective/change/constants/config/metric_definitions`。运行可登记 `seed/base_model/model_revision/algorithm/created_at/config/metric_definitions`；训练数据版本、阶段、git commit 和训练预算应放进 config，不将训练步数当成统一计算量。

`metric_definitions` 以实际指标字段名为 key：

```json
{"entropy/token_mean":{"label":"Token entropy","definition":"填写实际公式与 token mask","scope":"填写实际计算位置","aggregation":"填写实际聚合方式","unit":"nats"}}
```

显示口径按 run > experiment > project 取值。字段本身的数值仍来自 `log_metrics`；系统不从名称推算熵或创造算法。未登记口径就显示未登记。身份与配置不可静默修改，结论通过人工 append-only notes 记录。

## Checkpoint

```json
{"id":"lt-e02-s42-r001-step100","run_id":"lt-e02-s42-r001","step":100,"name":"step100","artifact_uri":"/actual/weights/step100","weights_sha256":"ACTUAL_WEIGHT_HASH"}
```

路径与哈希仅用于追踪，本包不加载/复制/读取模型验证。step 为非负整数；不同 run 的相同步数不代表等量训练。初始模型是一个明确的 checkpoint；Groundtruth 不是模型，不作为“GT 模型成绩”混进 checkpoint 表。

## 固定 Panel / Groundtruth

```json
{
 "id":"search-validation-fixed-v1","name":"search_validation_fixed_v1","version":"v1","split":"dev","demo":false,
 "samples":[{"id":"Hotpot-V01","dataset":"HotpotQA","question":"ACTUAL_EXACT_QUESTION",
   "answers":["ACTUAL_GOLD","ACTUAL_SUPPORTED_ALIAS"],
   "metadata":{"source_id":"ORIGINAL_ID","source_revision":"IMMUTABLE_REVISION","source_split":"ORIGINAL_SPLIT","reference_evidence":[]}}]
}
```

示意字符串不是实际题目。`answers` 同时容纳标准答案和合法别名；可另用 metadata 区分其来源。`split=dev/test/probe` 是你实验中的用途，不代表源数据官方划分。5–10 题是用户工作流偏好，不是强制配额；不支持每次评测随机抽题。

排序后的完整 samples（按 dataset/id 排序，包含 metadata）以确定性 JSON 编码计算 SHA256；顺序改变不改 hash，题目/答案/别名/来源变更会改。相同 panel ID 不能改内容。提供的 `fingerprint` 若与计算不符会被拒绝。`read_only/isolation/size/datasets/source_metadata_complete` 是导出视图字段，不能用它们伪造审核状态。

`freeze-panel` 对真实题目要求 metadata 的 source_id/source_revision/source_split。`candidate`、`DRAFT_NOT_APPROVED` 等待审标志不能直接注册成 frozen。导出快照可再次通过 register_panel 导入，系统重新计算并核验 hash；隔离报告须另行生成。仅题单冻结不代表已隔离。

## Evaluation 与协议 v2

同一个 checkpoint 重测必须用新的 evaluation ID，不覆盖旧记录。每条 eval 在 panel 中每题最多一条轨迹；多 seed/rollout 用不同 eval。评测初始化状态 running；只有所有固定题都产生记录时可标 completed，未评分仍原样保持 null。中断使用 failed/cancelled。

```json
{
 "id":"lt-e02-s42-r001-step100-validation-001",
 "checkpoint_id":"lt-e02-s42-r001-step100","panel_id":"search-validation-fixed-v1",
 "protocol":{
  "protocol_version":2,
  "scorer":{"name":"ACTUAL_SCORER","version":"ACTUAL_SHA"},
  "parser":{"name":"ACTUAL_PARSER","version":"ACTUAL_SHA"},
  "input":{"prompt_sha256":"ACTUAL_SHA","chat_template_sha256":"ACTUAL_SHA","tokenizer_revision":"ACTUAL_REV","stop_conditions":["ACTUAL_STOP"]},
  "retriever":{"corpus_version":"ACTUAL_SNAPSHOT","index_sha256":"ACTUAL_SHA","version":"ACTUAL_REV","top_k":3,"ranking":"ACTUAL_RULE","truncation":{"observation_tokens":1024,"rule":"ACTUAL_RULE"}},
  "budget":{"max_tool_calls":4,"max_output_tokens":2048,"context_window":8192},
  "decoding":{"temperature":0,"top_p":1},
  "runtime":{"backend":"ACTUAL_BACKEND","backend_version":"ACTUAL_VERSION","hardware":"ACTUAL_GPU","dtype":"ACTUAL_DTYPE","batch_size":1,"determinism":"ACTUAL_SETTINGS"},
  "state_reset":{"per_question":true,"between_evaluations":true},
  "mode":"inference_only","groundtruth_to_agent":false,
  "harness_version":"ACTUAL_COMMIT","seed":42
 }
}
```

这些数值是格式示意，不是替你规定实验超参数。未记录项明确写 `unknown`，不填假值来通过门槛。v2 必须声明纯推理、不给 agent Groundtruth、题间/轮间状态重置；显式违反的评测会被拒绝。配置缺失可作为不完整证据查看，但不能据此声称受控效果对比或完成复现验收。

这检查的是登记条件，而非 GPU 服务的真实执行。不要因为页面提醒而只改元数据，不重新用真实配置评测。

## Trajectory / Metrics

延续 v1（详见 `DATA_FORMAT_V1.md`）。例子：

```json
{"evaluation_id":"EVAL_ID","dataset":"HotpotQA","sample_id":"Hotpot-V01",
 "scores":{"em":0,"f1":0.4},"reward":0,"runtime_status":"ok",
 "raw_final_output":"ACTUAL_RAW_OUTPUT","parsed_answer":"ACTUAL_PARSED_ANSWER","termination_reason":"final_answer",
 "steps":[{"index":0,"kind":"model","input":[],"output":"ACTUAL_OUTPUT"},
 {"index":1,"kind":"tool","tool_call":{"name":"search","arguments":{"query":"ACTUAL_QUERY"}},"tool_response":"ACTUAL_FULL_RESULT","model_visible":"ACTUAL_TRUNCATED_RESULT"}]}
```

示意占位不是可用原始轨迹。实际 inputs/outputs 直接记录，不倒推、不补写思考。可省略 question/gold_answers，由 fixed panel 取得；显式填写须完全一致。

EM 为 0/1/null，不是百分数。异常状态不自动转 0；看板不评分或修改奖励。`answer_correct` 按 EM=1 统计，和运行异常独立；固定题分母是 panel 题数，同时显示评分覆盖率。不完整宏平均留空，不把缺失当零。

## 对比、矩阵与复现

矩阵默认每个 checkpoint 的最近记录（created_at，随后插入顺序），不是最高分。用户可明确改选历史记录，Checkpoint 表和矩阵同步。不同题单不进入同一张矩阵。协议不一致/不完整时不显示改善/退化归因。

跨运行比较额外核验底座名称和 revision；跨项目需要 `allow_cross_project=true`（HTTP `cross_project=1`）。训练预算只记录并提示，不因 step 相同自动判断可比。

复现核对要求同 checkpoint ID、不同 eval ID、相同题单与完整协议。比较解析答案、原始最终输出、原始 scores、运行状态/终止原因、步骤语义投影。步骤投影字段：`index/kind/input/output/observation/tool_call/tool_response/model_visible/error/status/token_ids`。顶层耗时、时间戳、展示标题不参与；工具返回内部的任何字段仍参与。首个差异步骤按保存顺序定位；缺失记录/空 steps 不判一致。

## 人工记录与 baseline 引用

```json
{"references":[{"experiment_id":"lt-e02","evaluation_id":"EXISTING_EVAL_ID","reason":"真实配置完全匹配时引用已有 baseline"}],
 "experiment_notes":[{"experiment_id":"lt-e02","note":{"kind":"negative","note":"填写观察到的退化及证据","evidence":[{"type":"evaluation","id":"EXISTING_EVAL_ID"}]}}]}
```

reference 只保存指针，不能复制曲线到当前项目。Notes 的 kind 为 observation/positive/negative/decision；证据 ID 必须存在。UI 人工笔记每次追加；bundle 同内容 note 生成稳定 event_id，重复导入不重复追加。支持显式 event_id；同 ID 不同内容拒绝。
