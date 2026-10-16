# 第三方代码与设计参考 / 0.9.6

## 实际随包复用的代码

**Lucide 0.468.0**：`static/studio/icons.js` 内四个 SVG 路径，使用本地 DOM 渲染，不加载外网脚本。
- https://github.com/lucide-icons/lucide/tree/0.468.0/icons
- layout-grid: `f8eca3a721201b652dbbc3b76c15e92fd271c9f9`
- git-compare-arrows: `15068b1fe458c8a815cf69e705daafbcc9de388a`
- list-checks: `5119b0284673659ffe11d3a853c9a58f5aec84d7`
- flask-conical: `5917608f5544aca95cb71bf19fb408a92469a6ff`
- ISC + Feather MIT 许可完整保留在 `searchagent_lab/static/vendor/`。

**@fnando/sparkline**：训练曲线的 SVG 线段生成复用其实现；不引入运行时依赖。
- https://github.com/fnando/sparkline/blob/main/src/sparkline.js
- 已审阅源文件的 Git blob SHA：`1313b2e2f2b7411b2bd1b3e19b627d2eb83296b6`
- 本地文件 `static/vendor/sparkline.js` 是适配版本，并非未修改原包。
- 新增真实 step、带正负值坐标映射，修复根节点清理与坐标取值；缺失点由调用方拆段，checkpoint 评测仍只画离散点。没有平滑或补造数据。
- MIT 原文见同目录 `sparkline.LICENSE`。

## 设计参考，不是代码集成

Inspect View 的逐题日志、字段展开、过滤与原始证据查看：
https://inspect.aisi.org.uk/log-viewer.html

EvalScope 的评测运行、汇总、结果详情组织：
https://github.com/modelscope/evalscope

本版没有安装 Inspect/EvalScope SDK，没有迁移训练器，也没有宣称兼容它们的全部日志格式。没有实际引入 uPlot、React 或另一套数据库。
原有 SearchAgent Lab 的录入协议、工作区、跨平台同步、实验隔离与方法证据读取代码继续复用。


## 0.9.1 新增开发期工具与设计借鉴

**esbuild 0.28.2**（MIT）作为可选开发依赖，用于本地 JS/CSS bundling、压缩、页面拆分和内容哈希。生产 Python wheel 提供构建产物；运行不需要 Node，不加载远程 CDN。构建工具许可证见 `docs/v091/ESBUILD_LICENSE.txt`；官方文档 https://esbuild.github.io/api/ 。

本版进一步采用以下官方文档中的设计，不将这些平台称为已集成的运行时模块：

- Inspect headers / sample summaries / single sample：https://inspect.aisi.org.uk/eval-logs.html
- Langfuse 实验归属：https://langfuse.com/docs/evaluation/experiments/data-model
- Langfuse 基线比较：https://langfuse.com/docs/evaluation/experiments/compare-experiments
- Langfuse 字段选择与分页：https://langfuse.com/docs/api-and-data-platform/features/public-api
- promptfoo 结果表与单题详情：https://www.promptfoo.dev/docs/usage/web-ui/

具体取舍见 `docs/v091/ARCHITECTURE_ZH.md`。没有复制这些平台的后端，没有更换用户训练 harness。


## 0.9.2 评测与轨迹设计参考

本次新增评测读模型、原位双视角与 JSON 适配代码为本项目实现，没有复制以下项目的执行引擎或引入其运行时依赖：

- Anthropic：Agent 的 model + harness 与 evaluation harness 区分；保留任务结果、实际执行和判分证据。https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Inspect AI：评分与运行错误分开、原始样本与逐步日志；支持已展开的单样本 JSON 和 JSONL，不读取二进制 `.eval`。https://inspect.aisi.org.uk/scoring-policy.html 、https://inspect.aisi.org.uk/reference/inspect_ai.log.html
- EvalScope：逐步 `agent_trace.events` 展示、唯一 message ID 关联与实际评分名称；不把 preview 补成完整返回。https://github.com/modelscope/evalscope
- AgentBoard：仅展示已经记录的可验证子目标，不从搜索次数估算 Progress Rate。https://github.com/hkust-nlp/AgentBoard
- rLLM：共享 Episode / Trajectory / Step 结构，区分训练采样与独立评测的统计语义。https://docs.rllm-project.com/core-concepts/episodes-trajectories-steps 、https://docs.rllm-project.com/core-concepts/agentflow-evaluator

具体实现范围、未覆盖格式和接入方法见 `docs/v092/ARCHITECTURE_ZH.md` 与 `docs/v092/INTEGRATION_ZH.md`。这些参考不表示整套框架已被安装或已与 GPU 训练连通。

## 0.9.3 方法产物与效果评估设计参考

本版的日志 helper、数据校验、方法摘要、指标视图与实验内交互由本项目实现。没有把以下论文代码复制为训练算法，也没有把其执行引擎、teacher 或评测服务打包进 wheel。已有 Lucide、sparkline 与 esbuild 的来源和许可沿用上文。

| 官方来源 | 本版借鉴的部分 | 实际实现范围 |
|---|---|---|
| [SkillRL](https://github.com/aiming-lab/SkillRL) | 技能库快照、完整技能文本和训练过程中的技能变化 | 读取已保存的 SkillRL 风格库和归档，保存来源、版本、候选/注入；不自动生成或更新技能。 |
| [ReSkill](https://github.com/amazon-science/reskill) | 新旧技能版本试验、接受/回滚过程 | 保存版本试验与原始决定；同模型下对比 Bank 版本。没有集成其训练或版本选择算法。 |
| [SkillsBench](https://www.skillsbench.ai/docs/getting-started) | 同任务有/无 skill 的配对评价 | 增加显式 Bank Off/On 受控比较，仍使用本项目的题单、执行与评分记录。 |
| [SSP](https://github.com/Alibaba-Quark/SSP) 与 [DR.ZERO](https://github.com/facebookresearch/drzero) | 出题、验证、solver 尝试与训练供给的分别记录 | 保存稳定题目身份、验证/拒绝/错误、采样、更新与固定外部测；没有集成出题器或 proposer reward。 |
| [Rethinking OPD](https://github.com/Thinking-Space/Rethinking-OPD) | teacher 对 student 当前状态的帮助与最终能力分别评估 | 保存 teacher 输入、实际接续和监督，不把概率诊断当作任务成功。 |
| [OCSD 论文](https://arxiv.org/abs/2608.04788) 与 [OVCSD 论文](https://arxiv.org/abs/2607.27937) | observation 来源、失败状态对齐、验证接续与监督的区分 | 新增 OPD 证据对象和明确范围的统计，没有实现论文 optimizer，也没有自动恢复远程环境。 |

本版仍沿用 Inspect AI / EvalScope / AgentBoard / rLLM 的前述日志和视图设计。方法特定指标是本项目按照实际记录范围实现的统计口径，不宣称是上述框架共享的官方标准分数。详细字段和边界见 [0.9.3 架构](docs/v093/ARCHITECTURE_ZH.md) 与 [方法接入说明](docs/v093/INTEGRATION_ZH.md)。

本包已有 SkillRL 归档属于用户提供的实验资料；附加到实验不改变原始归档字节，也不据此宣称某个 run 真实使用了该库。SDAR / UniOPSD 方法声明来自本包既有复现配置，不表示上游代码已经运行或新增了 teacher 轨迹。

## 0.9.4 Skill 记录与原生报告适配

新增技能包、实例、生命周期、六格／移除比较及页面代码由本项目实现，未复制上游优化器。只读报告适配按上游公开字段编写，不把上游 trainer 或模型作为安装依赖。

- Agent Skills 技能包结构参考：https://agentskills.io/specification 。
- SLIM lifecycle 报告格式与移除设计：https://github.com/ejhshen/SLIM/blob/main/slim_method/lifecycle.py 。复核日期2026-10-10，读取源码 SHA256 为 `ed1a49146b565f89b721ba9a75ba96473f9eeb300a404437877343b8e3b5471b`。只读映射保留源报告、显式版本关联和报告值语义，不执行上游验证或生命周期决策。
- Anthropic skill-creator 结果格式：https://github.com/anthropics/skills/blob/main/skills/skill-creator/references/schemas.md ；聚合口径参考：https://github.com/anthropics/skills/blob/main/skills/skill-creator/scripts/aggregate_benchmark.py 。保留 grading、timing、benchmark 原始 JSON；断言通过率不称 EM，来源不明的 token 字段不当实测 token。
- ReSkill registry / 版本测试、LangChain skills-benchmarks、SkillLearnBench 与生产技能原则作为用户设计参考，具体实现和未覆盖边界见 [0.9.4 Skill 设计](docs/v094/ARCHITECTURE_ZH.md)。本版没有完整导入这些项目的训练或评测引擎。

用户提供的完整设计说明保存在 `docs/v094/INPUT_SKILL_DESIGN_ZH.md`，其中研究描述作为用户设计输入保留；本版实际核验的代码、数据和界面能力由当前接入与验收文档说明。

## 0.9.5 OPD 原生记录适配与效果评估

新增 OPD 对象、校验、记录器、原生格式读取、受控续跑统计和界面由本项目实现。没有复制上游 trainer 或优化器，也没有将 teacher、veRL 或模型推理服务作为查看器依赖。可选 pyarrow 只用于读取原生 Parquet 文件，不随生产 wheel 安装。

- veRL 原生 OPD 配置与蒸馏语义：[官方 OPD 文档](https://verl.readthedocs.io/en/latest/algo/opd.html)。generation JSONL 字段和配置解释参照 [ray_trainer.py](https://github.com/verl-project/verl/blob/main/verl/trainer/ppo/ray_trainer.py) 与 [losses.py](https://github.com/verl-project/verl/blob/main/verl/trainer/distillation/losses.py)。
- OCSD 原生 observation / token 记录：[oc_opsd_ray_trainer.py](https://github.com/yiy1x/OCSD/blob/main/verl/trainer/ppo/oc_opsd_ray_trainer.py)。Full / Ablated 残差按来源定义保存，不推断其已经进入 optimizer。
- AgentOPSD 原生 token 记录：[opsd_ray_trainer.py](https://github.com/ZethWang/AgentOPSD/blob/master/verl/trainer/ppo/opsd_ray_trainer.py)。已过滤的导出不被补成完整训练 batch。
- UniOPSD 原生 credit 统计：[rlsd_ray_trainer.py](https://github.com/Zenghuang-Fu/Uniopsd/blob/master/verl/trainer/ppo/rlsd_ray_trainer.py)。`opsd_rescue_frac` 保留恢复 credit 的定义，不称为任务修复率。

这些公开源码在2026-10-10读取核验，具体字节 SHA256、字段与适配边界见 `docs/v095/OPD_SOURCE_AUDIT.json`、`methods/opd_adapters.py` 的 `SOURCES` 和 [OPD 接入说明](docs/v095/INTEGRATION_ZH.md)。参考源码哈希是适配格式的审核来源；真实训练记录的 `producer_revision` 仍须由训练器提供，缺失时保留未知。

Rethinking-OPD、OPDVR、SDAR、AHEAD、OVCSD、RSTG 等在用户设计中作为监督、局部恢复与独立泛化评测的参考；本版没有宣称实现这些算法或完整支持其所有日志。完整用户设计保存在 `docs/v095/INPUT_OPD_DESIGN_ZH.md`。实际能力与未覆盖边界以 [架构说明](docs/v095/ARCHITECTURE_ZH.md) 和 [验收记录](docs/v095/ACCEPTANCE_ZH.md) 为准。


## 0.9.6 Entropy 来源与实现边界

新增记录器、适配和比较代码为本项目实现，不复制上游 trainer、optimizer 或评分服务。源码读取核验日期为2026-10-10；具体URL、SHA256、读取状态、公式与导出边界见 `docs/v096/ENTROPY_SOURCE_AUDIT.json`。原用户完整设计保存在 `docs/v096/INPUT_ENTROPY_DESIGN_ZH.md`。

- veRL：`utils/torch_functional.py`、FSDP `transformer_impl.py` 和 PPO `ray_trainer.py`。区分完整词表熵、评分温度、response mask、聚合与实际采样分布；旧 `workers/actor/dp_actor.py` 路径当前不可用，审计明确记录。
- [RAGEN](https://github.com/mll-lab-nu/RAGEN)：`collapse_metrics.py` 和 MI 指标说明。保持首次turn/整轨迹命名空间、有符号输入依赖代理量与prompt辨识率；不把其 retrieval accuracy 称为搜索证据命中率。
- [SEAL](https://github.com/WindyLee0822/SEAL/blob/main/draw_token.py)：固定生成文本的评分与token比较设计。仅导入明确序列化的 `score_text` 返回数组；标准脚本的HTML不冒充已有通用JSON输出。
- [ARPO](https://github.com/RUC-NLPIR/ARPO) 与 [SSRL](https://github.com/TsinghuaC3I/SSRL)：参考实际有限候选与采样query token公式，读取显式hook记录；不声称完整词表、未导出尾部分布或内置通用指标导出器。
- [LM-Polygraph](https://github.com/IINemo/lm-polygraph)：借鉴错误/不确定性排序与风险–覆盖率评价。本项目的AUROC明确以EM错误为正类；AURC独立实现并对同分采用期望随机顺序，区别于已审阅上游的稳定输入顺序并列处理。不宣称数值完全复现上游每一种配置。

STEER、Entropy Mechanism、80/20、EntropyRL、AEPO 和 Cyclical Entropy Eruption 等保留为用户设计中的研究参考，不声称整体移植或验证其Search任务效果。测量来源、训练消费和最终自主效果在本项目中分别取证。


## 仓耳玉楷（可选本地安装；0.9.7）

字体作者：北京仓耳文字技术有限公司。官方产品页：https://tsanger.cn/product/362 。官方页面允许个人非商业免费使用，并另列商业授权；本项目不将其声明为开源或免费商用字体。

源码与 wheel 不包含字体二进制。显式 `setup-font` 命令从官方地址下载原始 TTF，或验证用户提供的同一原始文件，保存在用户数据目录；网页只加载本机或该工作区的字体。字体版权及相关许可独立于本项目的 MIT 许可。精确来源与哈希见 `docs/v097/FONT_SOURCE_AUDIT.json`。
