# 架构

## 不新增 Agent 框架

现有 veRL + search_agent → 独立 rank-0/sidecar 日志导出 → Recorder 或 JSON/JSONL 导入 → 本地 SQLite → stdlib HTTP → 无依赖静态 UI。

服务不加载权重、不修改训练或检索、不重评分，不需要 API key。运行时仅 Python 标准库。源码可直接运行，wheel包含静态资源。

## 存储边界

项目/实验由独立表及run_ownership外键决定；run而非model是曲线归属。checkpoint/eval依赖父记录，按选定project/experiment验证。多项目同底座不导致结果聚合。默认视图只读当前运行；cross-project显式开关与协议核验之后才能配对。

Panel全局共享，samples只读按内容哈希锁定。每次evaluation新ID，completed轨迹不可改；注释、实验notes、隔离audits另表追加。Baseline references只保存来源指针，不把来源记录计入当前项目的曲线。

旧版数据库升级先SQLite在线备份，创建v2表，再把原run对应到独立legacy experiment；v1表原始data不重写。旧record带有类似latent名字也不自动推断归属。

## 前端路由

`#/projects` 是研究入口；`#/overview?project=...&run=...&panel=...` 是当前项目的实验界面；`#/trace?evaluation=...&trajectory=...` 是证据检查；`#/compare?a=...&b=...` 是嵌入式配对界面，不增加分析平台菜单。侧栏只有训练与评测、轨迹详情两类工作页面，项目本身作为上下文切换。

Matrix读取真实selected evaluation IDs，与Checkpoint表共享选择；不按得分挑记录。各层有异步epoch/token，切换项目后的旧请求不能把旧曲线渲染回来。跨项目旧run深链接被拒绝，不静默展示foreign记录。

## API

GET `/api/session`（项目目录、manifest摘要；兼容旧客户端runs列表）、`/api/catalog?project=&experiment=&model=`、`/api/overview?project=&experiment=&run=&panel=`、`/api/matrix?project=&run=&panel=&dataset=&selections={...}`。

GET `/api/experiment?id=`、`/api/panel?id=`、`/api/evaluation?id=`、`/api/trajectories?evaluation=&dataset=&outcome=&q=`、`/api/trajectory?id=`。

GET `/api/compare?a=&b=&cross_project=1`、`/api/reproducibility?a=&b=`、`/api/audit?id=`。下载 `/api/export/panel`、`/api/export/trajectory`、`/api/export/evaluation`、`/api/export/csv`、`/api/export/reproducibility`。

POST `/api/annotations` 和 `/api/experiment-notes`，只接受同源/本地session token与application/json。API不接收启动训练、shell、任意文件浏览、远程fetch或模型调用请求。

## 安全与规模

回环地址、Host检查、同源/CSRF、静态资源白名单、CSP、HTML转义与CSV公式中和。项目隔离不是账号权限隔离，知道ID的同一工作区用户可以显式读其他项目；不用于多租户部署。原始日志保留完整可能含敏感内容，使用者应提前脱敏。

WAL与20秒busy timeout用于本机rank-0写日志/查看器读取；不是多机NFS并发存储或长期分布式平台。矩阵适合固定小面板，每次读取选定run的checkpoint，不为上万个checkpoint/数百万traces做过性能验收。
