# SearchAgent Lab

Search Agent 评测框架，基于当前部署的 0.10.0 源码整理。框架与真实数据独立存放；安装、升级或提交框架不需要复制数据库。

```text
eval/
├── eval-framework/               # 代码仓库范围
│   ├── searchagent_lab/          # 后端、存储、评测与诊断逻辑
│   │   └── static/              # 前端源码及可直接使用的构建产物
│   ├── scripts/                 # 构建、安装、启动脚本
│   ├── tests/                   # 测试代码
│   ├── examples/                # 合成示例、格式模板与接入示例
│   ├── docs/                    # 架构、数据格式与接入说明
│   ├── deploy/                  # 通用部署模板
│   └── config.example.json
├── eval-data/                   # 本地私有数据，不上传 GitHub
│   ├── center/                  # 现有中心数据库、.team 附件和字体
│   ├── public-preview/          # 已有预览数据
│   ├── workspace/               # 新建本地工作区的默认位置
│   ├── imports/                 # 原始导入文件
│   ├── exports/                 # 已有导出
│   ├── results/                 # 外部实验结果文件
│   ├── trajectories/            # 外部轨迹文件
│   ├── videos/                  # 外部视频文件
│   ├── backups/                 # 原有备份
│   ├── reference-assets/        # 真实题单、SkillRL 原始归档
│   ├── secrets/                 # 私有认证文件
│   ├── logs/                    # 运行日志
│   └── releases/                # 历史部署包、旧环境及验证数据
└── searchagent-lab -> eval-data  # 原部署路径的兼容入口
```

沿用原 Python 包结构，避免移动前端静态资源和模块后破坏安装包。现有 SQLite 的表结构、文件名以及 `.team` 相对位置保持兼容；轨迹、结果和导入原文中已有的数据库记录继续保存在工作区数据库中。上面的独立文件目录供外部产物使用，不会自动把数据库内容拆成文件。数据库迁移仍由现有存储模块负责。

## 运行

在此目录执行，无需下载模型或安装 Node 即可使用已构建前端：

```bash
python3 -m searchagent_lab init
python3 -m searchagent_lab serve --port 8765
```

默认创建旁边的 `../eval-data/workspace`，不随 shell 的当前目录改变。安装为 wheel 后默认使用 `~/.local/share/searchagent-lab/workspace`。新工作区为空；合成演示另用 `bash scripts/start_demo.sh`，写入 `../eval-data/demos/studio-demo`。

现有中心继续使用其部署管理器、认证和 `team-serve` 参数；不要用普通 `serve` 打开中心工作区。

## 自定义数据位置

优先级：显式 `--data-dir` > `SEARCHAGENT_LAB_DATA_DIR` > `SEARCHAGENT_LAB_CONFIG` 指定的 JSON > 默认目录。CLI 和默认 `Recorder()` 共用解析规则；SDK 显式传入的路径优先。

```bash
python3 -m searchagent_lab serve --data-dir /mnt/private/search-eval/workspace
# 或
export SEARCHAGENT_LAB_DATA_DIR=/mnt/private/search-eval/workspace
python3 -m searchagent_lab serve
# 或复制配置文件，按需修改 data_dir
cp config.example.json config.json
export SEARCHAGENT_LAB_CONFIG="$PWD/config.json"
python3 -m searchagent_lab serve
```

JSON 相对路径以配置文件所在目录为基准。配置采用标准库 JSON，无额外 YAML 依赖；本地 `config.json` 已被 Git 忽略。演示脚本使用各自独立的数据目录，可用脚本参数（检查器）或直接 CLI 覆盖。

```python
from searchagent_lab.sdk import Recorder
with Recorder() as recorder:  # 与 CLI 使用相同配置
    pass
# with Recorder('/mnt/private/search-eval/workspace') as recorder: ...
```

## 开发与验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
# 前端修改后构建（需要 Node 和构建依赖）
npm ci
npm run build
```

单元测试使用临时工作区。涉及原始 SkillRL 归档的测试读取 `../eval-data/reference-assets/skillrl/SkillRL_skills_20261005.zip`，也可设置 `SEARCHAGENT_LAB_SKILLRL_ARCHIVE`；未提供归档时仅这些真实数据测试跳过。浏览器、历史快照校验和性能脚本需自行提供对应数据及工具，历史验收截图和日志保留在 `eval-data/releases/`。

GitHub 仓库根目录应选本目录。`.gitignore` 排除数据库、导入文件、结果、视频、备份、认证文件、环境及构建安装包。真实数据没有复制进框架；已有部署包与环境保留在私有数据目录，便于回溯。

原目录通过同文件系统重命名迁移到 `eval-data`，并建立旧路径兼容软链接；没有修改数据库内容，也没有重启或切换现有部署到新源码。后续发布从本目录构建安装包，继续显式指定现有 `eval-data/center`。访问方式和部署脚本仍可通过旧路径工作。迁移记录见私有数据目录的 `layout-migration.json`。
