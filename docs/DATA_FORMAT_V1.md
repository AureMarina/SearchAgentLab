> Legacy v1 reference. New research features are documented in DATA_FORMAT.md.

# 数据协议 v1

本协议描述**查看器接收的数据**，不要求你修改 Agent 的 messages / tool 协议。可由训练日志出口、评测脚本或离线 converter 导出。

## 1. Bundle 结构

`.json` / `.json.gz` 根对象：

```json
{
  "schema_version": 1,
  "runs": [],
  "panels": [],
  "checkpoints": [],
  "metrics": [],
  "evaluations": [],
  "trajectories": [],
  "finish_evaluations": []
}
```

同一次导入按以上实体依赖顺序写入，任一条错误则整个文件回滚。未知顶层字段会拒绝，防止把别的 JSON 格式误当成已接入。`description` 是可选顶层字段。

所有 run / checkpoint / panel / evaluation / trajectory ID 使用 1–180 个 ASCII 字母、数字、`_ - . :`；样本 ID 是非空字符串，可保留源数据 ID。主实体 ID 在整个工作区唯一，checkpoint ID 需要带 run 前缀以避免不同训练都叫 `checkpoint-100`。

完整、可运行的示例见 `examples/import_bundle.json`。

## 2. Run 与训练指标

```json
{
  "id": "run-001",
  "name": "Search RL experiment 001",
  "base_model": "Qwen3-4B-Instruct-2507",
  "algorithm": "veRL / actual recipe",
  "created_at": "2026-10-02T08:00:00+00:00",
  "status": "running",
  "demo": false,
  "config": {"git_commit": "record-your-actual-commit"}
}
```

`id/name` 必填。其余是展示/追踪元数据。`created_at` 未传时由本地记录时间填写，重复注册复用原时间。建议提供 UTC ISO 时间；历史数据不要用导入时间冒充训练时间。运行状态支持 `running/completed/failed/stopped`，通过 SDK `set_run_status` 或 JSONL `run_status` 事件更新。

每行指标：

```json
{"run_id":"run-001","step":100,"metrics":{"actor/pg_loss":-0.031,"train/reward":0.42,"actor/grad_norm":0.75}}
```

step 为非负整数。值为有限数值或 `null`，禁止 NaN / Infinity / 布尔值。不设固定 loss 定义，不强行要求 loss 单调下降。相同 step 的不同指标可增量合并；已有同名值不得改写。

训练曲线保留原值；缺失数据点不会补零。Checkpoint 表中的 loss/reward 仅取**该 step 恰好存在的记录**，不会悄悄用最近前一步代替。

## 3. Checkpoint

```json
{"id":"run-001-cp-100","run_id":"run-001","step":100,"name":"checkpoint-100","artifact_uri":"/your/checkpoints/step100","weights_sha256":"optional-actual-hash"}
```

权重路径和哈希是元数据，该包不会打开、复制或加载模型权重，也不校验权重同步。保存 checkpoint 的时间点与评测时间是两件事。

## 4. 固定评测题单 Panel

```json
{
  "id":"multihop-dev-v1",
  "name":"多跳开发集",
  "version":"v1",
  "split":"dev",
  "demo":false,
  "samples":[
    {"id":"source-id-001","dataset":"HotpotQA","question":"your exact question","answers":["gold answer","gold alias"],"metadata":{"source_split":"your frozen source split"}}
  ]
}
```

`split` 表示这份题单在**你的实验**中的用途：`dev/test/probe`，不替代源数据官方划分字段。题目、答案须来自真实固定清单。`samples` 不可为空；每个 `(dataset,id)` 只能出现一次。

SHA256 对按 `(dataset,id)` 排序后的完整样本对象做确定性 JSON 编码得到；包括问题、答案、样本 metadata。顺序变化不改变哈希，内容变化会改变哈希。相同 panel ID 不能改内容；新题单使用新 ID。

`metadata` 可保存 supporting facts / decomposition / evidence chain、原数据集版本、抽题 seed 或文件来源。题单详情可以查看这些完整数据的 JSON/导出文件；当前列表优先展示 Q/A，**不会把参考内容发送给模型**。参考链只是诊断依据，不是必须执行的唯一路径。

题单只管理评测数据。当前版本不负责训练/开发/测试泄漏检测，不能仅凭面板名称声称数据隔离已完成。

## 5. Evaluation

```json
{
  "id":"run-001-cp-100-dev-eval-01",
  "checkpoint_id":"run-001-cp-100",
  "panel_id":"multihop-dev-v1",
  "created_at":"2026-10-02T09:00:00+00:00",
  "protocol":{
    "scorer":{"name":"your_actual_em_scorer","version":"git-commit-or-file-sha"},
    "parser":{"name":"your_actual_answer_parser","version":"actual-version"},
    "retriever":{"corpus_version":"actual-corpus","index_sha256":"actual-hash","top_k":3},
    "budget":{"max_tool_calls":4,"max_output_tokens":2048,"context_window":8192},
    "decoding":{"temperature":0,"top_p":1},
    "harness_version":"actual-git-commit",
    "seed":42
  }
}
```

这些字段描述实际执行条件，示例数值不代表你必须采用这些设置。7 个一级协议键都需要存在。没有记录时显式填 `"unknown"`；含 unknown 的记录可展示，但不能宣称同协议比较。

`evaluation` 事件只负责开始记录，状态初始为 `running`。不要通过在此对象里填 `status=completed` 代替结束事件。全部题目已经收到之后：

```json
{"id":"run-001-cp-100-dev-eval-01","status":"completed"}
```

放入 `finish_evaluations`。中断的记录使用 `failed/cancelled`，允许缺少题目。`completed` 要求每个题单样本都有一条轨迹，但允许某些样本尚无评分；后者不会显示完整宏平均。

一次评测中每个题目仅一条轨迹。多 seed / 多次 rollout 使用不同 evaluation ID。每题不同 seed 时，可在 `seed` 里登记固定 seed-map 及其哈希。完成后的评测不允许追加或重写轨迹。

## 6. Trajectory

```json
{
  "evaluation_id":"run-001-cp-100-dev-eval-01",
  "dataset":"HotpotQA",
  "sample_id":"source-id-001",
  "scores":{"em":0,"f1":0.5},
  "reward":0.0,
  "runtime_status":"ok",
  "raw_final_output":"the exact model final output",
  "parsed_answer":"the actual parser output",
  "termination_reason":"final_answer",
  "usage":{"model_calls":3,"tool_calls":2,"input_tokens":3200,"output_tokens":320,"latency_ms":12500},
  "steps":[
    {"index":0,"kind":"model","title":"第一轮模型调用","input":[{"role":"user","content":"the exact input"}],"output":"the exact generation","latency_ms":500},
    {"index":1,"kind":"tool","title":"检索","tool_call":{"name":"search","arguments":{"query":"the exact query"}},"tool_response":{"raw":"full original response"},"model_visible":"the exact rendered / truncated observation","latency_ms":250}
  ]
}
```

必填 `dataset/sample_id`；bundle / JSONL 的轨迹还须带 `evaluation_id`，SDK 的目标评测 ID 是方法的第一个参数。轨迹 `id` 可省略，由 `(evaluation_id,dataset,sample_id)` 哈希生成。

`scores` 保存已有评分，不重新运行 scorer。`em` 为 `0/1/null`，不是 `0/100`；`f1/accuracy` 如提供须在 `[0,1]`。额外数值指标允许保留。页面主表以 EM 为主，不能仅提供 accuracy 然后默认当作 EM。

`runtime_status` 支持 `ok/error/timeout/cancelled/unknown`。未记录则为 `unknown`，不是自动补 `ok`。异常筛选优先显示明确的 error/timeout/cancelled；答案评分不随运行状态改写。异常且无 EM 的轨迹不记为 0 分。

`question/gold_answers` 可省略，将从固定题单取得；若显式提供则必须与题单完全一致，避免日志中的错误答案映射污染标准答案。

未保存 steps 可留空数组。step.index 为唯一、递增的非负整数，不要求连续。kind 通常为 `model/tool/observation/final/event`，其他原始类型可保留。模型与工具可能多对多，不要求每个模型调用正好配一个工具步骤。

保留模型实际输入与工具完整返回、模型可见内容之间的区别。没有保存的值用 `null` 或省略；**不得用工具完整返回替代模型实际看到的截断后观察**。不要从终局 messages 猜测早期 input，更不要补写未生成的“思考过程”。

未知的轨迹/步骤字段会保留在原始 JSON 中，例如 token IDs、logprobs、response masks、behavior-policy ID、原始日志定位、教师来源等。当前 UI 不对这些字段自动计算新指标。保存大张量建议用路径与哈希元数据，不要将模型权重内嵌进轨迹。

## 7. JSONL 增量事件

`.jsonl` / `.jsonl.gz` 每行一个完整 JSON：

```json
{"type":"metrics","data":{"run_id":"run-001","step":100,"metrics":{"train/reward":0.42}}}
```

支持 `run / checkpoint / panel / metrics / evaluation / trajectory / evaluation_finish / run_status`。按依赖顺序出现，父记录要先在工作区存在。导入文件是事务式批处理，不是直接对持续写入的半行日志做 tail。实时记录优先使用 SDK，或先封存完整 sidecar 再导入。

## 8. 导出与人工标注

`export` / 网页导出的整次评测 JSON 是 `evaluation_evidence` 审计快照，包含解析后的评测元数据和轨迹，不是顶层 import bundle，不能直接拿来再次 import。原始 SQLite 工作区或原 import bundle 用于恢复；导出快照用于分享证据。

人工标注另表追加：类别、证据步骤、文字、`pending/confirmed`、创建时间、来源 manual。不能改变原始模型输出或已记录的分数。不提供自动归因，也不让标签反向进入训练奖励。
