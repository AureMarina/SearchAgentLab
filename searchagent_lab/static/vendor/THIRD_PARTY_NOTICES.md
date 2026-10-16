# 第三方代码与设计参考 / 0.9.0

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
