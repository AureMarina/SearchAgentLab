> 本文件保留 0.7 团队接入流程，安装包路径已更新为 0.8.0；先阅读 `../v080/QUICKSTART_ZH.md` 的停机备份 / 全端升级说明。

# 0.7.0 跨平台部署与接入

本指南使用的域名、路径、账号与实验 ID 都是配置示例。没有连接或修改你的常驻服务器。建议先用一个小实验验证闭环，再接入全部研究运行。

## 1. 先分清两个目录

中心服务器用单独目录，例如 `/srv/searchagent-lab/center`，保存数据库、原始 ZIP、归档文件和凭证哈希。每个训练平台继续使用自己的本地工作区，例如 `./workspace/platform-a`。**不要把中心目录当作训练 logger 的写入目录，也不要复制 `.team/source.json` 到另一个独立平台。**

升级前停止原查看器和采集写入，备份整个 0.6.0 工作区。程序不会修改上传给开发者的 ZIP，也不会自动把你的原工作区转为中心；中心使用新目录最清楚。已经含 `.team/center.json` 的目录拒绝旧版本地 writer，以免绕过中心接收规则。

在中心和各训练平台安装 0.7.0 wheel。以下用激活后的 `python`；也可改成对应虚拟环境的完整路径。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --no-index --no-deps dist/searchagent_lab-0.8.0-py3-none-any.whl
python -m searchagent_lab --version
```

## 2. 接入已有的 0.6.0 实验：不用重跑

在原本地工作区初始化本平台身份。`operator` 是页面展示的执行人；凭证 `owner` 是中心认可的提交人账号，两者分别保存。

```bash
python -m searchagent_lab team-init \
  --data-dir ./workspace/platform-a --operator student-a --platform platform-a

# 用你已有的 experiment_id / run_id 替换示例 ID。
python -m searchagent_lab team-export-plan \
  --data-dir ./workspace/platform-a --experiment latent-e02 --out latent-e02.plan.json

python -m searchagent_lab team-export \
  --data-dir ./workspace/platform-a --run actual-run-id --out actual-run.result.zip
```

配置包由现有的项目、实验、明确的 `main/reproduction` profile，以及该实验已经使用的固定题单构成。不按名称猜用途；旧记录未分类时先用原 `register-profile` 接口补充用途。尚无评测的实验不会凭空导出题目，后续可以通过管理员计划补充固定题单。

有父实验引用时先登记父实验计划。历史 profile 内的跨运行 `baseline_evaluation_id` 不会自动改写为中心 ID；这类记录需要先明确迁移映射，当前版本会在引用不合法时报错。不要为通过导入删掉原始配置或伪造对照。

将这两个文件通过团队允许的方式传给中心管理员。中心服务未启动时执行：

```bash
umask 077
mkdir -p /srv/searchagent-lab/center

python -m searchagent_lab team-plan latent-e02.plan.json \
  --center --data-dir /srv/searchagent-lab/center

python -m searchagent_lab team-import actual-run.result.zip \
  --owner student-a --data-dir /srv/searchagent-lab/center
```

`--owner student-a` 要和该同学后续上传凭证的 owner 一致；这样离线先交、联网再交会返回 `duplicate`，不会再添一份成绩。首次导入也可由管理员明确使用 `--allow-definitions` 批准包内的新定义，已有 GT 仍不能被覆盖。日常建议先审阅并登记计划，再接收结果。

**中心服务已运行时，不要再用本地 `team-import` 争抢同一数据库。** 管理员登录「数据接入」，先导入计划 JSON，再填写实际提交人并选择 ZIP。所有写入走同一个接收器。

## 3. 建立访问凭证，启动中心

凭证只写入指定的新文件，命令输出不打印秘密。凭证文件采用 `0600` 权限。管理员、查看者和每位上传者分别使用不同凭证，不共用管理员 token。

```bash
python -m searchagent_lab team-token \
  --data-dir /srv/searchagent-lab/center \
  --owner owner --role admin --out ./admin.token

python -m searchagent_lab team-token \
  --data-dir /srv/searchagent-lab/center \
  --owner student-a --role uploader --experiment latent-e02 \
  --out ./student-a-upload.token

python -m searchagent_lab team-token \
  --data-dir /srv/searchagent-lab/center \
  --owner teammate-reader --role viewer --out ./reader.token
```

上传者需要多个实验时重复 `--experiment`。默认 90 天过期，可设 `--days`。把上传凭证私下交给对应同学；不要放入代码仓库、实验配置、结果包或共享日志。

最小闭环先走 SSH 隧道，不需要立即申请域名：

```bash
# 常驻服务器：只监听 127.0.0.1。
python -m searchagent_lab team-serve \
  --data-dir /srv/searchagent-lab/center --port 8765

# 你自己的电脑，另一个终端：替换 SSH 账号和服务器地址。
ssh -N -L 8765:127.0.0.1:8765 your-user@your-server
```

浏览器打开 `http://127.0.0.1:8765`，使用管理员或 viewer token 登录。浏览器不把 token 存到 localStorage；会话只使用 HttpOnly cookie。各采集平台也可以建立自己的 SSH 隧道，提交到本平台的 loopback 地址。

长期使用有 HTTPS 域名时，采用 `deploy/nginx-searchagent.conf.example` 和 `deploy/searchagent-center.service`：替换域名、证书、账户、venv 和工作区路径，再由运维审阅部署。启动时明确公网 origin：

```bash
python -m searchagent_lab team-serve \
  --data-dir /srv/searchagent-lab/center --port 8765 \
  --public-origin https://lab.example.org
```

origin 必须与浏览器实际使用的域名和端口相同，不带路径。HTTPS 由反向代理终止，接收器仍然只监听 loopback。不要把旧版 `serve` 直接转发到公网，也不要取消客户端 TLS 校验。

## 4. 各平台自动提交

在平台本地设置本人的上传 token，先跑一次同步：

```bash
export SEARCHAGENT_LAB_URL=https://lab.example.org
export SEARCHAGENT_LAB_TOKEN="$(cat /private/student-a-upload.token)"

python -m searchagent_lab team-sync \
  --data-dir ./workspace/platform-a --run actual-run-id

# 作为独立进程持续运行；不嵌进 GPU 训练线程。
python -m searchagent_lab team-watch \
  --data-dir ./workspace/platform-a --run actual-run-id --interval 30
```

同一工作区多个已授权实验可用 `--all`；不要拿仅授权一个实验的凭证同步其他实验。`team-watch` 会每 30 秒检查已记录数据，只有内容变化才生成新 revision；没有变化不重复产生快照。网络发送发生在本地记录与待发送队列落盘之后，不在数据库快照锁内等待网络。

`team-watch` 需要你启动并持续保留，命令已提供但不会自行部署到其他平台。生产上可由 `deploy/searchagent-collector.service` 托管。采集需要短暂读取锁，日志特别频繁或文件很大时应增加采集间隔，或只在 checkpoint 评测完成后调用一次 `team-sync`。

失败时检查队列：

```bash
python -m searchagent_lab team-status --data-dir ./workspace/platform-a
python -m searchagent_lab team-flush --data-dir ./workspace/platform-a --force
```

网络超时和服务端暂时错误会保留 `pending`，退避重试；权限、格式、冲突等需要人工处理的错误进入 `blocked`。修复原因后显式 `team-retry`，再补传。不能用重试命令篡改原始记录或强制覆盖冲突。

`team-sync` / `team-flush` 尚有未送达项时退出码为 3；不要把它接在训练脚本的强制失败链上。中心的接收成功回执丢失时，客户端重传同一包，中心返回 `duplicate`。

本地队列默认容量 1 GiB，并预留至少 64 MiB 空间；容量不足会报错，不声称已入队。已确认 ZIP 不自动删除，审阅回执并确认中心备份后可执行：

```bash
python -m searchagent_lab team-prune --data-dir ./workspace/platform-a
```

只删已确认包的本地 ZIP，保留回执，不删除待发送包、数据库和原实验日志。中心原始 ZIP / blobs 的保留与磁盘监控由维护者管理；本版不自动清理中心历史。

## 5. 新实验：先登记 run，再训练

管理员维护一份 `searchagent-lab.plan/v1` 配置包，在中心与各源平台分别 `team-plan`。`examples/team/plan.template.json` 只含空实验定义，没有虚构验证题或成绩；复制后换成真实 ID、研究问题和明确固定题单。

新运行用可选 SDK 自动登记 UUID 和来源，再继续调用原本的 logger：

```python
from searchagent_lab.team.sdk import register_team_run
from searchagent_lab.studio.sdk import StudioRecorder

workspace = "./workspace/platform-a"
run_id = register_team_run(
    workspace, "latent-e02", "seed42", seed=42,
    base_model="Qwen3-4B-Instruct-2507",
    config={"learning_rate": 1e-5, "precision": "bf16"},
    platform_job_id="your-actual-job-id", code_dir="/path/to/training/repository",
)
# 保存 run_id 到本次 job 的 manifest，恢复时复用已记录身份；独立重跑创建新 ID。
with StudioRecorder(workspace) as recorder:
    recorder.store.log_metrics(run_id, 0, {"train/loss": 0.7})
    # 后续沿用现有 SDK 记录 metrics/checkpoint/evaluation/trajectory/batch/method。
```

示例中的 loss 只是 API 调用示范，不是真实实验记录。实际集成应传入训练程序产出的值。没有采集的 token/mask 不会事后“重建”。日志名称继续保留你自己的定义，Entropy 等方法指标不会自动转化成结论。

`register_team_run` 记录 Python、系统架构、允许名单中的软件包版本，以及指定代码目录的 git commit / dirty / 差异哈希；不导入 torch、不扫描环境变量、不保存 git remote。它**不归档完整工作树，不自动确认 GPU/推理精度/检索快照**。这些需要实际配置或显式产物补充。已有运行不会被事后补写为“训练开始前已登记”。

CLI 等价入口：`team-run --experiment latent-e02 --name seed42 --seed 42 --config actual-config.json --code-dir ...`。它返回 run_id，但不启动训练。恢复/续训应保留来源与执行阶段；本版不自动把两段曲线按 step 拼接。

## 6. 文件与权重

```bash
# 显式归档实际配置、代码补丁、小型证据文件。
python -m searchagent_lab team-add-artifact \
  --data-dir ./workspace/platform-a --run actual-run-id \
  --key actual-config-v1 --file ./actual-config.json --required

# 大权重只登记持久位置；外部可访问性不会被自动判定为成功。
python -m searchagent_lab team-add-artifact \
  --data-dir ./workspace/platform-a --run actual-run-id \
  --key final-checkpoint --uri s3://your-bucket/your-run/final --role checkpoint
```

同一个 key 对应的内容不可变，新版本用新 key。单文件 96 MiB、ZIP 128 MiB、解压总量 512 MiB，超限不入库。外部 URI 不允许嵌入账号密码或临时 query token，接受 https/s3/gs/az。不把远端 `/home/...` 路径误认为中心共享目录，也不替你验证 S3 权限。

包内已关联的 inspector 原文件与方法来源字节会一起归档。某个方法原文件包含其他对象时，原字节也包含这些对象；提交前检查敏感内容。没有结构化关联的本地路径只作为原始元数据保留，需要访问的文件请显式登记。

## 7. 验收你的部署

先用一份小结果包做「离线导入 → 联网重复提交 → 浏览器查看」；应只有一条运行、相同题目数量、相同原始分数。再追加一个实际 metric step，应增加 revision 而不是第二条运行。断开采集端网络，应留下 pending，训练 logger 仍能在本地继续写入；恢复后补传并检查回执。最后实际打开一次 batch 关联和一条轨迹，而不只看分数卡片。

未进行真实公网部署、TLS 证书配置、磁盘故障注入或你的 GPU 平台联调，这些需要在部署环境验证。备份与恢复见 [权限与备份](SECURITY.md)。
