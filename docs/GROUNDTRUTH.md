# Groundtruth、隔离范围与可复现性

## 不随机重抽

从具体数据版本人工或预先随机选定若干题，保留 source_id、question、全部可接受 answers/aliases、source_revision 和 source_split。把具体记录写入 samples，冻结为一次不可变 panel。模型/项目/seed/checkpoint 切换不会重新选题。需要改内容就新建 v2，不把不同版本合并。

本包不附带真实“已隔离”的35/70题；只有明确标为 synthetic 的示例。`validation-template` 是空 draft，导入会拒绝。实际题目未与训练、正式测试全量清单核对前，不应作为“未见过”验证证据。

## Inventory 文件格式

`inventory.json` 路径以其所在目录为基准：

```json
{
 "projects":["self-play","latent","entropy"],
 "entries":[
  {"project_id":"self-play","role":"train","path":"selfplay_train_all.jsonl","revision":"ACTUAL_ROUND_SHA","description":"列明包含的SFT/RL/生成题/蒸馏/proposer或技能材料","declared_complete":true},
  {"project_id":"self-play","role":"test","path":"formal_test.jsonl","revision":"ACTUAL_TEST_SHA","description":"Self-play正式测试全集","declared_complete":true},
  {"project_id":"latent","role":"train","path":"latent_train_all.jsonl","revision":"ACTUAL_SHA","description":"Latent所有历史和当前训练材料","declared_complete":true},
  {"project_id":"latent","role":"test","path":"formal_test.jsonl","revision":"ACTUAL_TEST_SHA","description":"Latent正式测试全集","declared_complete":true},
  {"project_id":"entropy","role":"train","path":"entropy_train_all.jsonl","revision":"ACTUAL_SHA","description":"Entropy所有历史和当前训练材料","declared_complete":true},
  {"project_id":"entropy","role":"test","path":"formal_test.jsonl","revision":"ACTUAL_TEST_SHA","description":"Entropy正式测试全集","declared_complete":true}
 ]
}
```

上述路径不是真实已存在文件，须由你提供。每个 train/test 文件是 JSONL，或 JSON list / `{samples:[...]}`；每行必须有：

```json
{"dataset":"HotpotQA","id":"原始记录ID","question":"完整原文问题","metadata":{"source_id":"原始来源ID"}}
```

不同实际格式需先显式转换，程序不猜列名。每份清单计算文件 SHA256，审核前后复核文件未变。可以为每份文件登记预期 sha256，不匹配立即拒绝。

同一项目可以有多份训练/测试清单；将所有历史训练、SFT、RL、蒸馏、自蒸馏、self-play 生成和给 proposer 的材料纳入，不只看当前一个训练文件。已经被旧 checkpoint 用过的题，从当前清单删除也不能称旧模型没见过。

## 审查输出状态

| 状态 | 意义 |
|---|---|
| not_checked | 没有提供审核证据。 |
| blocked_overlap | 与至少一个项目 train/test 来源 ID 或规范化问题重叠；不能声称隔离。 |
| manual_review | 发现词面近重复候选，等待人工核实。 |
| incomplete_scope | 未覆盖登记的全部项目、缺 train/test、未声明覆盖完整或达到近重复检查上限。 |
| no_detected_overlap_in_supplied_scope | 仅在本次提供的清单内没有发现所实现规则可检测的重叠。不是全局去污染证明。 |

规范化：NFKC、casefold、标点转空格、折叠空白。跨数据集核对规范化问题；同数据集核对 source_id。近重复采用共享至少两个 token 的候选、token 集合 Jaccard ≥0.8 **或**字符 SequenceMatcher 比例 ≥0.92。默认最多 2,000,000 次近重复比较，达到上限明确报告可能不完整。

这不是向量语义检索，可能漏掉同义改写、翻译、同来源不同问法；报告保留候选和规则，不能把结果叫做“完全无泄漏”。训练清单是否真正完整是使用者声明，工具无法从缺失文件中证明。底座预训练从未包含这些题不可证实。

新 self-play 题产生后重新运行 `audit-isolation`，形成新报告，保留旧报告。看板不会自动监控远端文件变化；旧报告只对记录的文件 hash/版本有效。新增项目也须纳入下一次 inventory。

CLI 审核：0=所提供范围未检测到重叠；3=明确重叠；4=人工审核或范围不足；2=输入错误。即使非零也会在正常完成审核时保存报告；JSON格式错误/文件缺失会直接拒绝。

## 只读不等于给模型

固定文件包含 Groundtruth，用于 scorer 和事后 Inspector。你已有 eval harness 应仅把题目/允许的输入发给模型；不得将标准答案、supporting facts、decomposition 或这些题作为 proposer 的生成材料。SDK不调用模型，也不会给模型注入参考。

评测每题从空 agent 状态开始，不跨题或跨轮 carry 记忆，不更新权重。被测 latent state 可在单题内部跨工具轮次保留，按真实实验条件记录；“清空题间状态”不是把单题内 persistent-latent 强制 reset。

每个 checkpoint 可以生成不同 query；不能用别的 checkpoint 的检索路径硬替换。固定同一检索快照下的同 query 行为，保存每次实际 query、原始返回和模型可见截断内容。本包只记录你提供的快照标识和响应，不负责制造检索快照。
