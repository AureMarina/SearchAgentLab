# experiment-studio-v1 · 主实验与文献复现

这层不替换 v2 研究记录。原 `project → experiment → run → checkpoint → evaluation → trajectory` 继续成立；仅在侧车追加明确用途/设计、batch 链接和报告快照。

## 1. 用途和算法是两回事

`track` 只允许显式登记 `main` 或 `reproduction`。没有登记的旧实验读为 `unassigned`，仍能查看原曲线/成绩，但不输出受控增量。`kind` 为 `agent_iteration` 或 `evaluation_calibration`。某个 Dr.Zero 复现可以做完整训练；这不使它自动进入主实验。

原 experiment/run 数据不需要改名，也不重新导入。`profile` 与原 experiment ID 关联，冻结后不可变；日后的独立改进建立新 experiment，而非修改复现用途。说明性论文引用只存在于元数据，不被添加进真实 scores。

## 2. 先登记原实验，再登记用途

已有实验在网页的“未分类”标签中登记。或调用：

```python
from searchagent_lab import Recorder, StudioRecorder

workspace = './workspace/real'
with Recorder(workspace) as rec:
    rec.register_project(id='self-play', name='Self-play')
    rec.register_experiment(
        id='repro-drzero-001', project_id='self-play',
        name='Dr.Zero · 独立复现',
        objective='复现实现与设置，检查本地结果和完整执行过程',
    )
    # 如有真实训练，再用原接口登记其 run、checkpoint、指标和评测。

with StudioRecorder(workspace) as rec:
    rec.register_experiment_profile(
        experiment_id='repro-drzero-001',
        track='reproduction', kind='agent_iteration',
        question='本地实现能否按已记录设置完成复现？',
        change='填写对原配置的实际适配；未知时明确写未知',
        constants='填写实际保持条件',
        binding_origin='prospective',
        reproduction={
            'method_name': 'Dr.Zero',
            'source_url': 'https://arxiv.org/abs/2601.07055',
            'repository_url': '',
            'code_revision': 'unknown',
            'protocol_mode': 'unknown',
            'adaptations': [],
            'published_reference': {
                'metric': None, 'value': None, 'source_location': None,
                'model': None, 'dataset': None,
            },
            'local_config_refs': [],
        },
    )
```

这里的空字段是模板，不是已核实论文配置。登记 profile 不创建运行、训练样本或成绩。不要复制到已经有同 ID 不同元数据的档案上。生产 ID 和配置必须替换为实际内容；原论文模型/题单与本地不一致时，不能只因数值接近就称为复现成功。

相同结构写成 JSON 后：

```bash
python -m searchagent_lab register-profile actual_profile.json --data-dir ./workspace/real
```

主实验的 `track='main'`，可登记 `parent_experiment_id`、`baseline_evaluation_id`、`allowed_changes` 等。父实验和外部 baseline 必须是同项目、已分类的主实验；复现不能作为主线父节点。新界面遵守此规则，旧 Store.add_reference / compare 入口也检查用途。

`binding_origin=retrospective` 表示对已发生的实验补登记；`prospective` 只是调用方声明运行前设计，不是可信时间戳证明。

## 3. 实验设计与可比性边界

profile 可以保存 question/change/constants、允许变化路径、来源说明等。它不是完整的研究假设验证引擎。

**普通 checkpoint 对比**继续要求相同题单、底座及固定执行/评分协议，并检查 SkillBank 等已记录状态；profile 的 `allowed_changes` 不会悄悄放宽普通能力对比。模型/harness/技能修改的特定试验继续通过既有“验证与修复”显式登记允许差异。

新报告要求完整记录运行条件。旧协议不全时仍展示原数值与配置，增量保留为空。同样，评测校准只记录判定变化，不能当作模型提升。此版的评测版本页是实际题单/协议的可见目录，不执行新 Judge 或自动重评分。

## 4. 训练 batch 的明确关联

原训练日志出口仍用 `Recorder.log_metrics(run_id, step, metrics)`。训练批次另存：

```json
{
  "id": "actual-run-b100",
  "run_id": "actual-run-id",
  "step": 100,
  "behavior_policy": "actual-rollout-policy-version",
  "groups": [
    {
      "id": "actual-group-id",
      "sample_id": "training-question-id",
      "samples": [
        {
          "id": "actual-rollout-id",
          "reward": null,
          "advantage": null,
          "trace_ref": {"trajectory": "actual-saved-trajectory-id"}
        }
      ]
    }
  ]
}
```

`null` 是未记录，不是零。也支持指向已经打开文件的 `{file, record}` 或 method 对象引用；格式和跨项目规则沿用 [METHOD_EVIDENCE.md](METHOD_EVIDENCE.md)。不存在的引用会拒绝，不自动创建轨迹。

实际 token IDs / response_mask 可选。提供时必须是等长数组，token IDs 为非负整数；mask 为 0/1。大数组可以不导入，只保留原件引用。系统不从最终 messages 反推训练 token、不重新计算 advantage、不判断“reward相同就一定没有梯度”。

```python
with StudioRecorder(workspace) as rec:
    rec.log_training_batch(actual_record)
```

```bash
python -m searchagent_lab import-batch actual_batch.json --data-dir ./workspace/real
```

批次 ID 不可变，重复组 ID、错位 mask、内容冲突拒绝；一致重导幂等。返回值含 content_hash，可用同一对象重复提交。`step=100` 只和同一 run 的100步关联，没有真实记录的98步返回空，不用最近一批冒充。

## 5. 结果、图与分母

一个 checkpoint 可以选择多条独立 evaluation。默认最近保存记录，不取最高分；选择历史项后，矩阵和点图使用同一 ID。题目键为 `(dataset, sample_id)`。

每个结果同时保留 planned/executed/scored/error 等事实（视原日志可用字段而定）。未评测与未评分不同；运行异常不会重写已有 EM。筛选修复题目不改原题单正确数分母。只能在同题同协议、完整评分时给完整净增/宏平均差值。

训练 reward 是实际数值曲线，validation是离散点。含 null 的训练曲线断开，不补零。某列协议不同或外部状态不同，不连接为同一固定条件曲线；历史原分仍在表里。

## 6. 报告快照

`POST /api/studio/report` 输入 `{id,name,b,a?}`。它冻结当前选择的原始评测引用、对比口径和逐题表；不运行模型也不改变评分。重复 report ID 拒绝覆盖。

跨用途、跨项目、关键协议缺失或不一致的记录可以查看各自原分和配置，但不能冻结为有效A/B对照。单记录报告没有增量含义。复现内部可比报告明确 `included_in_main_iteration=false`。

## 7. HTTP 接口

GET:

- `/api/studio/catalog?project=...&track=main|reproduction|unassigned`
- `/api/studio/profile?experiment=...`
- `/api/studio/dynamics?run=...&panel=...&baseline=previous|initial&selections={...}`
- `/api/studio/batches?run=...&step=100`
- `/api/studio/reports?project=...&track=...`
- `/api/studio/report?b=...&a=...`
- `/api/studio/pair?a=...&b=...`
- `/api/studio/saved-report?id=...`
- `/api/studio/position?evaluation=...&trajectory=...`（深链接分页定位，不猜行号）

POST: `profile / experiment / project / report / batch / import`。全部复用原服务 loopback、Origin 与 CSRF 检查。`experiment` 输入 `{experiment:{...}, profile:{...}}`；`import` 只接收原 schema_version1/2 bundle，不猜测私有字段。普通JSON请求限制1MiB，大文件用CLI导入或旧原件上传。

## 8. 兼容、备份与范围

新增侧车 `experiment_studio.sqlite3`，其他4个库保留。请备份完整目录，或在停止写入后复制；不要只复制活跃 SQLite 主文件而丢 WAL。新旧包不要同时写同一工作区。数据分区不是安全多租户；同一用户仍可主动打开所有本地文件。

原 `legacy.html` 仅作高级工具兼容入口。旧高级页面可能展示未分类记录，但新主页面的默认列表/计数/曲线按用途隔离；旧对比与 baseline 引用后端已加跨用途保护。没有后台执行器、自动改代码、模型恢复或论文成绩认证。
