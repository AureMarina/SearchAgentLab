# 诊断闭环数据协议 · diagnostic-loop-v1

这层扩展不改旧 bundle schema、原评测数据和评分逻辑。全部 POST 使用与旧接口相同的本地 CSRF token。源对象通过 `{file, record}` 或 `{trajectory}` 指定；写入还须携带 `source_hash`，来自 `/api/inspection/record`。

## A. 契约与四层 Scorecard

`GET /api/loop/workspace?...` 返回当前源的契约、四层检查、版本上下文、显式关联、假设、任务、结果和历史。`POST /api/loop/contract` 传 `contract` 与 `binding`。

契约格式：

```json
{
  "id": "my-case-contract",
  "version": "v1",
  "name": "实际执行要求",
  "risk": "normal",
  "mode": "shadow",
  "required_tools": [],
  "forbidden_tools": [],
  "required_skills": [],
  "output": {"format": "any"},
  "tool_schemas": {},
  "assertions": [],
  "hard_gates": [],
  "notes": "未规定必须搜索，就不因直接回答而失败。"
}
```

`output.format` 支持 any/json/answer_tag；json 可以提供 schema。输出契约不替代官方 EM。`required_tools` 和 `forbidden_tools` 以已记录调用校验，缺少调用只有在源明确 `trace_complete=true` 时才能按完整执行记录判断；不由此推断模型的计划。`required_skills` 读取明确的 `selected_skills` 数组，不猜隐式 Skill。

字段 assertions 示例：`{"id":"field-check","layer":"Outcome","pointer":"/artifact/metric","op":"number_close","expected":250,"tolerance":1}`。`tolerance` 为**绝对误差**，不是原文章示例的相对误差。也支持 exists/equals。原文没有这些具体 API，它们是本包适配，不应混同。

Validator 支持的 Schema 子集：type（含联合类型）、required、properties、additionalProperties（布尔或对象）、items、enum、const、minimum、maximum、minLength、maxLength、minItems、maxItems。title/description/$schema 是注释。`$ref`、oneOf/anyOf、format、pattern、条件分支等未实现关键字返回 insufficient，不联网取引用。上限 10,000 节点、60 层检查；超限不假装通过。

检查项 ID 可绑定 hard_gates，例如 `answer_score`、`output_contract`、`required_tools:search`、`tool_schema:0`、`assertion:field-check`。未知项、不适用项和缺证据项不能通过 gate。mode=shadow/warning/blocking 仅记录声明；本应用一律 `enforced=false`、`release_ready=null`，不连发布系统。

所有状态分开保存：运行状态；Outcome；Decision；Action；Reliability；gate。原始运行 completed/ok 不能替代任一层通过。单条轨迹 Reliability 为 insufficient；导入多次同题运行后，在对应复测报告中另行统计，不把一个重复结果永久贴成所有 checkpoint 的稳定性。

## B. 实际证据关联

推荐在现有日志出口保存（不是由看板补造）：

```json
{
  "execution_context": {
    "checkpoint_id": "actual-cp-id",
    "prompt": {"version": "actual-hash"},
    "context_builder": {"version": "actual-commit"},
    "contract_hash": "actual-frozen-contract-sha256"
  },
  "steps": [{
    "index": 1,
    "kind": "tool",
    "tool_call": {"id": "call-01", "name": "search", "arguments": {"query": "actual query"}},
    "tool_call_id": "call-01",
    "tool_response": {"documents": []},
    "model_visible": "actual text used by next model request",
    "context_transform": {
      "source_pointer": "/steps/1/tool_response",
      "target_pointer": "/steps/1/model_visible",
      "version": "actual-transform-version"
    },
    "retrieval_ms": 0
  }]
}
```

上面是字段模板，不是真实日志，不应把 0 或空列表填充到缺失记录。模型输入必须来自真实请求记录。raw/visible 同步保存只证明同一步配对；连接下一轮 input 仍需显式处理或来源指针。未知字段保留在原始 JSON。并发/重试出现重复 call ID 时不猜唯一关联。

可展示 latency_ms/queue_ms/generation_ms/retrieval_ms/retry_wait_ms/processing_ms。原文没有要求这些特定字段名，这是本包的适配接口。不同计时段可能重叠，不自动求和或推断关键路径。

训练侧 group_id、behavior_policy、advantage、mask、loss 等原字段仍可保存和查看；本版没有依据这些字段自动诊断梯度或证明信用分配机制。

## C. 假设与验证状态

`POST /api/loop/hypothesis` 携带 hypothesis：title/layer/component/observation/evidence/counter_evidence/alternatives/minimal_test。evidence 是原始 JSON pointers，必须存在；alternatives 至少一项。component 支持 prompt/tool_schema/retrieval/context_builder/parser/scorer/runtime/policy/skill/training/unknown。

`POST /api/loop/rule-hypothesis` 可将原有 finding_id 转为**未验证**假设。不是复制一个最终根因标签。

`POST /api/loop/hypothesis-review` 追加状态：unverified / supported_by_experiment / refuted / inconclusive。支持/反驳须绑定同一假设任务的 result_id；外部结果如果配置混杂则拒绝。note 必须解释适用范围。结果始终记录 `causal_proof=false`。

事实确认、语义 Judge 结论、实验支持和最终因果证明是不同的东西。系统不自动融合成一个高置信度百分数。

## D. 冻结任务与实际执行

`POST /api/loop/task` 携带 task；任务含 hypothesis_id/title/mode/change_candidate/expected_observation。保存后有 task_hash；重复执行生成新的 result，不覆盖旧记录。

### component

需要 `component:{operation,input_pointer,baseline,candidate}`。输入哈希绑定原字段；允许的纯函数详见 README。`POST /api/loop/execute` 才实际运行，且只运行内置组件。任务创建时做配置与输入可用性预校验，不把预校验当作完成模型实验。

输出 changed/status/parsed/rendered 等，不输出 QA 提升，也不改原始 parser/scorer。可用本地内置组件验证机制，不能说它等同于原训练仓库代码的逐字节复现。

### prefix / autonomous

需要 `allowed_changes`、`required_paths` 和 `expected_cases:[{dataset,sample_id},...]`。核心字段（checkpoint、底座、revision、题单、prompt/chat template/tokenizer、检索、parser/scorer、预算、解码、运行环境与状态重置）强制纳入，不能通过空列表绕过。

prefix 还要 prefix_pointer；默认 `restore_mode=approximate_text_reconstruction`。代码层可登记 recorded_state_artifact 与引用，但仍 `restoration_verified=false`；没有模型内部状态快照不能声称精确恢复。任务导出包含源引用和哈希，不向 agent 传完整 GT/参考答案。使用自己的评测器与恢复逻辑执行，没有提供通用 GPU 执行器。

外部结果格式：

```json
{
  "task_id":"copy-frozen-task-id",
  "task_hash":"copy-frozen-task-hash",
  "execution":"executed_externally",
  "baseline":{
    "evaluation_id":"distinct-real-eval-a",
    "trace_sha256":"64 lowercase hex characters from real file",
    "context":{},
    "groundtruth_assisted":false,
    "from_empty_state":true,
    "cases":[{"dataset":"actual dataset","sample_id":"actual id","em":null}]
  },
  "candidate":{
    "evaluation_id":"distinct-real-eval-b",
    "trace_sha256":"64 lowercase hex characters from real file",
    "context":{},
    "groundtruth_assisted":false,
    "from_empty_state":true,
    "cases":[{"dataset":"actual dataset","sample_id":"actual id","em":null}]
  }
}
```

这是模板，空 context / null 分数不会变成完整对比。用 `POST /api/loop/result` 导入；也可通过 evaluation-result 关联现有正式评测。源 trace 哈希必须提供但本服务不会远程打开哈希所指文件，`source_artifacts_verified=false`；导入声明不是执行证明。

逐题只在相同 dataset/sample_id 下配对。未知分数和缺题单独计数；拒绝重复键和清单之外题目；完整覆盖时先算各数据集 EM 再宏平均。baseline 不能漂离任务冻结时的版本上下文。评分器或 parser 变化不称为模型能力提升；前缀/辅助/合成记录不进入自主提升结论。

`POST /api/loop/fix` 保存 patch_ref/decision/note/result_id。decision 为 proposed / accept_for_further_evaluation / reject / needs_more_evidence。保存后 applied/published 恒为 false。改动是否发布由你的工程系统决定，不执行 patch 或 shell。

## E. Judge 交换

`POST /api/loop/judge-packet`：mode=decision_prefix 需 step_pointer；retrospective 不需要。返回系统指令、输入证据范围、prompt_version、packet_hash 和 response_schema。导出包自身包括 id/created_at，它们为运输元数据，不参与生成时的 packet_hash。

Judge 回答必须携带 packet_hash 与 judge.model/revision/prompt_version，每个 judgement 给 dimension、verdict、explanation、evidence、alternatives、next_check。dimension 支持 evidence_support/entity_consistency/constraint_retention/stopping_decision；verdict 是 supported/contradicted/insufficient（针对所检查命题，而不是“根因已证明”）。

通过 `POST /api/loop/judge-result` 导入。非弃权需原路径和原文 quote；范围外、未来、未导出的 GT 引用拒绝。校验引用不证明语义正确；`POST /api/loop/judge-review` 再保存人工确认/驳回。既不运行任何日志中指令，也不自动调用外部 LLM。

## F. Reliability 与诊断验收

`POST /api/loop/reliability` 输入 runs：每条包括 run_id/dataset/sample_id/context/em/independent_reset，可提供 latency_ms。相同 Case、唯一 run_id、版本一致（允许 seed 改变）、明确每次重置才统计。>=3 时可 reported，但仍是少量开发样本而非可靠性保证；p50/p90 使用已记录延迟的线性插值，同时显示样本数。未评分不填零；没有延迟不补测。

diagnostic-benchmark CLI 的输入 pin ruleset，cases 给 id/rule/raw/label/label_source。label 为 positive/negative/unknown；label_source 必须 independent_human 或 injected_fault。输出混淆计数、弃权、覆盖率。只衡量原规则症状检测，不声称覆盖语义 Judge 或因果诊断的准确率。

## G. 存储与安全

诊断侧车对象不可变，复核/决定追加事件；所有对象绑定 source target + hash，防止跨题、跨版本串接。命名契约定义全局按 id/version 唯一；使用同一只读契约不合并研究结果。

请求最大 1 MiB（结果建议用摘要和 trace 哈希），JSONL 原件限制沿用 0.3.0。只监听 localhost，CSRF/Origin 校验与原始日志 HTML 转义保留。没有 exec、用户自定义 shell、服务器文件浏览或外部 API 凭证。
