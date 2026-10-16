# method-evidence-v1 · 明确关联，不猜测过程

## 1. 文件与对象

方法文件与旧训练 `schema_version=2` bundle **不同**。旧数据仍通过原 import 接入。

```json
{
  "method_schema": "method-evidence-v1",
  "scope": {"run_id": "registered-real-run"},
  "objects": [
    {
      "id": "actual-object-id",
      "kind": "trace",
      "title": "可读标题",
      "version": "v1",
      "usage": "training",
      "demo": false,
      "data": {"question": "原始问题", "steps": []},
      "evidence_refs": [],
      "derived_from": []
    }
  ]
}
```

这是格式示意，字符串与空步骤不是已执行证据。替换成实际记录，缺失的过程留空。`id` 在工作区全局唯一，1–180 个 ASCII 字母、数字、`_.:-`。不把重复的 `step100` 当全局 ID。

对象 `data`、scope、version、usage 等不可变。相同 ID 内容变化拒绝；修改需要新版本/ID。原始文件字节单独保留，`file sha256` 不同于规范化 data 的 `content_hash`。对象 hash 还包含归属及其他字段。列表顺序不会建立关联。

声明的 scope 与页面选择或 CLI scope 冲突时拒绝，不能通过在 UI 选择另一个项目重新归档旧结果。只给 run_id 时从已登记的归属精确补 project/experiment。无 scope 是独立检查区，不隐式合并任何研究项目。

JSONL 每行必须显式包装：

```json
{"method_schema":"method-evidence-v1","object":{"id":"actual-object-id","kind":"trace","data":{"question":"原始问题","steps":[]}}}
```

完整文件事务式导入，允许后文引用前文或反向引用。任何非法行、缺失引用或冲突会整体回滚，不静默跳行。普通逐行 trajectory JSONL 没有这些标签时，使用原 `inspect`，不猜所属轮次。

## 2. 已实现对象类型

| kind | data 的关键字段 | 解释 |
|---|---|---|
| selfplay_cycle | participants: [{id, checkpoint_id/policy_ref, model, ...}] | 可同模型不同角色；不从名字推测策略 |
| generated_question | question, proposed_answer, source_documents | proposed_answer 不是已核验 Groundtruth |
| proposal | cycle_id, question_id, actor_id, solver_actor_id?, trace_ref? | actor 必须存在于 cycle.participants |
| question_review | question_id, status, reviewer?, verified_answer?, supersedes? | accepted/rejected/needs_review；有 verified_answer 的接受记录须有 reviewer；修订同题追加 |
| solver_attempt | cycle_id, question_id, actor_id, trace_ref?, reference_used?, scores?, reward?, advantage?, group_id?, groundtruth_visible? | 原分数保留；reward 不转成 EM；EM 是 0/1/null |
| trace | 保留 steps / trajectories / messages / 原字段 | 复用原检查器，不补造执行 |
| training_use | artifact_ids[], status, batch_id?, step?, ... | selected/sampled/excluded；sampled 必须有 batch_id |
| training_update | cycle_id?, consumption_ids[], output_checkpoint_id?, checkpoint_ref_status? | 消费来源与输出权重关联，不证明某题导致提升 |
| skill_bank | 原生库 JSON | 自动适配只读浏览，不额外包成 skills[] |
| skill_retrieval | bank_id, mode?, query?, candidates[], selected[], injections[], trace_ref?, injections_complete? | 候选、入选与注入分别记录 |
| skill_evolution | bank_id, parent_bank_id?, teacher_trace_ref?, ... | 来源请另记 derived_from / evidence_refs |
| evaluation_binding | evaluation_id, bank_id?, binding_origin, state_complete?, retrieval_config, extensions?, skills_disabled? | 必须归属于该评测的 run；一条评测不可重绑 |
| dataset_manifest | 实际清单或元数据 | 来源用途由 usage 声明 |
| state_snapshot | 实际状态文件引用、形状等 | 不读取大张量、恢复状态或计算范数 |
| method_case | mode=retrospective, evidence[] | 只供事后跨对象核查，不是角色输入 |
| extension.name.v1 | 自定义对象 | 原始 JSON 展示与引用；没有该算法的自动诊断 |

除已校验字段外，未知字段保留。不是全字段语义 schema；日志写入方负责真实性。shape/type 不符合已识别字段会拒绝；没有自动对照 GPU 权重、训练器或远端服务。

## 3. 证据引用

三种形式，必须恰好一种，pointer 遵循 JSON Pointer（`~1` 表示 `/`，`~0` 表示 `~`）：

```json
{"object_id":"trace-object-id","pointer":"/steps/1/model_visible","sha256":"实际 data 内容哈希"}
```

```json
{"file":"已打开的 JSONL 文件 ID","record":1,"pointer":"/steps/0/input"}
```

```json
{"trajectory":"正式轨迹 ID","pointer":"/steps/0/input"}
```

方法对象 pointer 相对其 **data 根**，不是对象封装根。file record 使用检查器 ordinal，不等于源物理行号；原检查器另外保留物理行号。提供的 hash 不匹配即拒绝；省略时仍校验对象及路径存在，解析后返回确定 hash。原始对象不可变。

仅 `evidence_refs`、`data.evidence_refs`、`data.trace_ref/teacher_trace_ref/proposer_trace_ref/request_ref` 和 `data.injections[].evidence_ref` 被解析为证据。未知 metadata 中形似路径的字符串不自动读取文件或建立关系。

`derived_from` 表示声明的来源，而 `trace_ref` 可表示查看关联；两者不是自动的因果关系。引用其他项目对象需要源对象显式 shareable；正式 trajectory 跨项目引用仍拒绝。独立检查区来源未归属时能被明确引用，不代表被认证为哪个项目。

## 4. SkillBank 与注入

支持 general_skills 数组、query_type_skills 或 task_specific_skills 分类对象（类别 → 数组）、common_mistakes 数组。一个条目保存原 source ID、位置、原内容、content_hash。重复 ID 可按完整原始 pointer 查阅，不能用模糊 ID 选取；缺 ID 不发明原 ID。不按数组位置推测两个版本之间的同一技能。

```json
{
  "bank_id":"actual-bank-snapshot",
  "mode":"template",
  "candidates":[{"member":"gen_007","score":0.73}],
  "selected":["gen_007"],
  "injections_complete":true,
  "injections":[{
    "member":"gen_007",
    "content_hash":"可选的该技能真实内容哈希",
    "rendered_text":"实际注入的原文",
    "evidence_ref":{"object_id":"solver-trace","pointer":"/steps/0/input/0/content"}
  }],
  "trace_ref":{"object_id":"solver-trace"}
}
```

示意分数不是默认阈值或检索结果。member 可用唯一原 ID、group/category/id 或 exact pointer，必须属于当前 bank。injections_complete 仅用于区分“完整日志仍缺注入”候选和“日志不足”，不证明收集器无遗漏。

只在 pointer 路径包含 input/model_visible/request_messages/model_request 的已记录字段核对 rendered_text 的精确片段。返回/输出里碰巧包含相同词不算输入证据；不同模板表达也可能导致字面未命中，因此不直接判技能无效。

没有 skill.execute 或虚构 start/end；内容被注入不证明行为依赖。来源、执行过程和外部语义 Judge 判断均可原样保存，但不生成“技能带来 X% 提升”的因果指标。

## 5. 评测外部状态

正式 evaluation 保留原协议不改写；用新 evaluation_binding 关联快照，要求明确 run scope 与 actual evaluation 一致。

- binding_origin 为 recorded_execution 或 retrospective，后者标成历史回填；两者都只是记录，不是远端执行证明。
- state_complete=true 还需要实际 bank 或 skills_disabled=true。使用 bank 时应给非空 retrieval_config，配置出现 unknown 不作为完整状态。
- 不允许第二条绑定把原评测改为新库；复测另建 evaluation ID。
- 同库内容、相同检索配置和 extensions 可比较权重；一侧未登记或外部状态不同则普通对比不算权重增量。两侧完全未登记沿用旧比较，同时保留 not_recorded 状态，不证明外部状态一致。
- checkpoint×bank 视图先按固定题单、原协议、底座/revision、检索配置分组，保留每次评测和 missing/unscored，不选最高分。只是分开展示，不自动分解贡献。
- 修改技能库的研究对照可在原“验证与修复”中显式允许 method_state 的预期变化；原评分器不变，结果从头执行。普通权重比较不偷偷放宽标准。

如果导入 synthetic bank 或 synthetic binding，不允许将其绑定到真实评测。

## 6. 专属诊断与来源审查

`method-evidence-checks-v1` 输出规则名、fact/candidate/insufficient、证据引用、next_check 和 limitation，不改原分数。当前覆盖：

- 缺参与者 policy、缺出题/解题轨迹、缺生成题审核或来源、评分参考缺失。
- groundtruth_visible=true 的答案辅助声明，group 有记录但 advantage 缺失。
- selected/sampled/excluded 区别、被采样对象已声明的 validation/test 上游或 unknown 用途。
- 库来源缺失、格式提示、common_mistakes 与当前根因区别。
- 检索 mode 缺失、selected 但缺 injection、缺模型输入证据、注入原文片段在输入中存在/缺失。
- 技能演化来源中的验证/测试材料、缺教师过程、评测外部状态历史回填。

来源追踪仅沿 `derived_from` 与顶层 `evidence_refs` 的 method object 来源，最多 1000 节点；不是任意日志字段上的语义溯源。不采集未知文件或扫描网络。用途枚举 training/evolution/validation/test/unknown，缺省为未知。`training_export_ready` 是声明范围内的审查提示，没有实际训练导出器，也不是全面泄漏认证。

多对象诊断按钮将明确证据值与 ref/hash 组合成 retrospective 案例，保留源对象不改写。拒绝把联合案例导出为 decision_prefix；查看单个角色历史时需打开该角色原始轨迹。该 guard 不能消除调用方写进原始 input 的泄漏，需审查真实日志。

## 7. SDK 与 HTTP

SDK：`MethodRecorder.import_file`、`record`、`record_batch`、`register_skill_bank`。旧 `Recorder` 不变；无需把方法对象塞入正式 trajectory 的 scores。

GET `/api/methods/`：catalog、object、cycle、bank、usage、diff、related、resolve、export、original、evaluation-state、evaluation-matrix。

POST `/api/methods/`：upload（原文件 bytes）、import（bundle）、review、diagnostic-case。全部写接口复用本地 CSRF/Origin 检查。批量文件最大 50 MiB；普通 JSON 请求限制仍沿用服务端原设置。

CLI：methods / import-methods / export-method；`methods --no-serve` 只导入。源 file 原字节通过 original 导出，包含依赖对象的 bundle 通过 export 导出；外部 file/trajectory 引用没有自动打包整个旧库，恢复要保留原工作区。

## 8. 重放边界

所有新增 Self-play 轨迹、训练采样、技能使用和更新都来自用户导入。页面不互相出题、不检索技能、不提炼、不微调、不重新计算 reward、不加载外部模型，不恢复 latent 状态。OPD/Entropy 等仅有可扩展原始记录能力，没有其专属自动诊断。
