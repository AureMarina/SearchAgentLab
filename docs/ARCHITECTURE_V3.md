# 0.3.0 增量架构

原路径：Project → Experiment → Run → Checkpoint → Evaluation → Trajectory，由既有 store/core 与不可变题单协议承载。本版不改变其 schema 2、评分和归属。

新路径：原始 UTF-8 文件 → 按内容哈希的 Inspection File → 原物理行/JSON元素 → 适配后的只读视图 → 版本化规则 → 追加人工复核。

两条路径共用同一个检查 UI；但文件检查不会伪装成正式评测。正式记录进入检查器时按原 trajectory ID 读取，候选计算和复核保存在侧车，不回写评分。

## API

- POST `/api/inspection/upload?name=...&format=jsonl`：application/octet-stream，需会话 CSRF token；严格字节上限。
- GET `/api/inspection/files`：只列本工作区已经上传的文件，不提供任意服务器文件浏览。
- GET `/api/inspection/records?file=...&q=...&outcome=...&rule=...&offset=...&limit=...`：摘要分页与完整原始行搜索。
- GET `/api/inspection/record?file=...&record=...`，或 `?trajectory=...`：读取某条原始证据、适配视图、规则与复核。
- GET `/api/inspection/search?...&q=...`：遍历完整原始对象，返回字段 JSON pointers（最多 200 条结果，明确报告截断）。
- POST `/api/inspection/reviews`：目标参数和 review 对象；验证源哈希、字段路径与当前规则 ID，追加历史。
- GET `/api/inspection/export?...`：证据诊断报告，不是正式打分 bundle。
- GET `/api/inspection/original?file=...`：原文件逐字节下载。
- GET `/api/inspection/rules`：规则名称、级别、依据类型与版本。

源文件留存 BLOB、SHA256、真实行号、原文；没有未知字段丢弃或错误行跳过。浏览器仅按需请求当前记录并逐层生成 DOM。数组每批 80 个元素，“展开 JSON”单次约束节点数量；搜索能够主动载入未展开分支。JSON 重复键不被静默覆盖：相应行记为异常并保留。

## 安全和边界

本地环回服务 + Host 校验 + 写请求 CSRF；不执行日志中代码/HTML，不跟随模型返回的 URL，不代入外部 prompt。字段和高亮使用 DOM textContent/Text nodes。文件名不成为服务器文件路径，HTTP 不接受任意本地路径读取。

这里是本地研究工具，不是多租户权限隔离或沙箱。停止写入后备份完整工作区，或对两个 SQLite 库使用在线 backup API。并发多机/NFS、持续 tail 的增量日志、跨行事件重组不在本版范围。
