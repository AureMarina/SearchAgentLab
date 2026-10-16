# 0.5.0 参考与适配边界

## SkillRL 原始来源

- 论文：https://arxiv.org/html/2602.08234v1
- 作者仓库：https://github.com/aiming-lab/SkillRL
- 实际 Search 文件：https://github.com/aiming-lab/SkillRL/blob/main/memory_data/search/claude_style_skills_search.json
- 本次核对该文件 Git blob SHA：`45fbb5d01b57a836bc955a04195fa20f3ed3417b`（blob SHA，不是整仓库 commit）。

核对了 `general_skills`、`query_type_skills`、`common_mistakes`、`metadata` 与 skill/mistake 的实际字段。适配器保留原始树，不把 Search 分类误写成唯一的 `task_specific_skills`。仓库读取失败的旧猜测路径 `claude_style_skills.json` 不作为依据。

当前版本借鉴技能库、生成／演化／检索之间的组织方式。平台自己的 `method-evidence-v1`、引用、hash、case 和诊断规则是本项目实现，不是 SkillRL 论文规定的接口。不实现论文算法，不声称复现作者分数。

SkillBank 文件内的来源字符串和统计是作者记录，不是本平台重新验真的训练来源；没有逐条来源轨迹时仍显示缺失。包内所有新演示 skill 均为自写的虚构示例，没有把原库和真实模型表现混入演示。

## 本项目已有材料

用户提供的《工具型 Agent 的分层评测方法与机制验证》区分 Outcome、Decision、Action、Reliability，并建议记录 Skill、Tool、环境和 Evaluator 版本。0.5.0 延续 0.4.0 的分层检查和验证边界，新增方法来源。没有把材料中的机制示例当成真实未知故障效果证明。

rLLM UI 的分层展开／搜索思路已用于 0.3.0 检查器；0.5.0 复用本项目检查器，不复制 ChatPanel，不引入云诊断服务。历史参考见 `RLLM_REFERENCE.md`。

这些文档中的元数据结构、训练消费层级、SkillBank×checkpoint 展示是本项目设计，不代表所有 Self-play 或 SkillRL 实现都原生记录它们。用户需要用实际日志字段显式映射。
