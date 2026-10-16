# rLLM UI 源码参考与本版边界

本轮参考页面： https://docs.rllm-project.com/experimental/ui

实际对照的公开代码快照：`rllm-org/rllm-ui@2e4e50ae119c125f83b3be32ca70c9c36d3340b1`。此前读取了组件目录，以及以下组件的相关实现：

- `frontend/src/components/EpisodePanel.tsx`：Episode/Trajectory/Step 数据类型，episode/trajectory/group/batch 分开的展开状态，按 field 记录搜索匹配位置并展开祖先层级。
- `frontend/src/components/ui/CollapsibleSection.tsx`：可控制的展开状态与独立内容区域；键盘 Enter/Space 的交互。
- 组件目录中的 `TrainingRunDetail.tsx / MetricsDashboard.tsx / RewardChart.tsx` 与训练、指标、轨迹职责分离的组织。

本版借鉴的是交互和职责拆分，不是整体复制仓库。新检查器独立为 `workbench / trace-tree / json-tree / diagnosis-panel`；后端拆为 adapters/diagnostics/repository/routes。为了保持随包 wheel 可离线运行，仍使用标准库 HTTP/SQLite 和浏览器 ES modules，不要求重装 React/Vite 或迁移训练端。

**没有移植** rLLM 的 ChatPanel、Observability AI agent、云账户、LLM API、技能蒸馏、聚类或模型训练服务；不将本地规则称作相同能力的替代。

rLLM 文档说明 UI 包含 episode/trajectory search，并可配置 observability AI agent。用户明确不要聊天框，本版采用可定位的规则候选与人工复核，而不是把聊天组件隐藏后假称有 agent 自动诊断。

未把上游代码文件或依赖复制到安装包，不附带字体文件。新实现的源码在本包中可直接修改。
