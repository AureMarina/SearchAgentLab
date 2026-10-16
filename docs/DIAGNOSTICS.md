# 诊断口径 · trace-evidence-v1

自动检查由 `searchagent_lab/inspection/diagnostics.py` 实现。输入是某条已保存记录，输出是 JSON pointer 证据与规则候选。所有分数、答案和原始内容不变；规则不是 LLM judge，也不是因果归因。

| 规则 | 条件 | 可支持的判断与限制 |
|---|---|---|
| json_invalid | 当前行无法解析、重复键或超限 | 该行不能可靠展开；保留原行，不删除其他记录 |
| reference_only | 只有题单或参考检索字段 | 不把参考文档当作模型输入或执行过程 |
| no_steps | 没有可用步骤/消息 | 缺少定位证据，不能反推第一处错误 |
| unscored | 没有 scores / is_correct | 未评分，不等于答错；reward 不是 EM |
| runtime_error / tool_error | 状态或 error 字段明确记载异常 | 确认“记录了异常”，不猜模型或环境责任 |
| tool_args | arguments 字符串不能 JSON 解码 | 确认字符串不是 JSON；私有协议允许普通文本时可驳回 |
| tool_empty | tool_response 显式为空字符串、数组或对象 | 空返回是事实，不保证是故障 |
| tool_missing_return | 工具步骤没有 tool_response | 未记录，不等于检索器未返回 |
| visible_missing | 完整返回存在但无单独 model_visible | 不能以 raw 代替模型实际可见内容 |
| repeated_query | 同记录内 query 大小写/空白归一后重复 | 可能必要重试；不自动认定死循环 |
| alias_lost | 足够长的参考别名在 raw 里命中但 visible 中没命中 | 仅词面候选，可能有等价表达；yes/no 等不触发 |
| parser_missing | 有最终输出，无非空解析答案 | 可能仅漏记字段；不直接宣称解析器故障 |
| parser_mismatch | 恰好一个完整 answer 标签且文本与 parsed_answer 不同 | 需核对真实 parser；规范化差异可能合理 |
| score_mismatch | 诊断规范化使 parsed_answer 与别名一致，但记录 EM=0 | 核对源答案/别名/评分版本；不把诊断规范化当官方 scorer |
| budget_stop | 记录的终止原因明确涉及预算/长度 | 不证明加预算一定有效 |
| uniform_rewards | 同 Episode 多轨迹均有数值 reward 且相等 | 多角色不一定是 RL group，不能由此证明 advantage/梯度为零 |

每项 findings 包含 `id/rule/level/basis/step_index/evidence/explanation/next_check/limitation`。证据指向原始对象，不指向重新编造的摘要。ID 与规则版本和路径绑定；模型输出、scorer 版本与计算口径仍由原日志负责。

人工复核采用追加历史：`pending / confirmed / rejected`，以及文字依据与证据路径。确认候选只表示用户确认该诊断，并不修改模型得分。人工标注可以不绑定规则，直接选某字段记录其他语义问题。看板不自动判断“用了哪个错误实体”“语义证据是否充分”等无法由结构规则验证的事项。

导出的诊断报告含原始对象、源文件 SHA256/行号或正式 trajectory ID、规则版本、复核历史和限制。正式评测记录直接进入检查器时，原先的人工标注也显示并随报告保留。
