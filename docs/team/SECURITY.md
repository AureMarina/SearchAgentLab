# 权限、部署边界与备份

## 适用范围

这是受控研究团队的首版汇集服务，不是公开注册的多租户 SaaS，也未经过渗透测试或外部安全审计。默认仍绑定 loopback，远程使用 SSH 隧道或审阅后的 HTTPS 代理。源节点只访问接收 API，不直接挂载或写中心 SQLite。

权限模型是工作区级 `admin/viewer` 和指定实验的 `uploader`。viewer 可读本工作区全部数据，不存在项目级查看权限；需要人员间真正隔离时部署不同中心目录/服务。uploader 不能浏览共享工作区，只能查看自己提交的近期回执；不能建立未批准项目、修改 GT、覆盖其他提交人已认领的来源，不能走旧写接口。

管理员可以批准实验计划、代收离线结果。管理员不具有“无痕改写旧分数”的上传接口；有冲突需保留原件、建立新身份或新的明确评测记录。浏览器页面隐藏了 viewer 的接入写表单；旧版部分高级编辑按钮仍可能可见，但服务端拒绝其写入，不能把这些按钮当作中心支持的协作编辑功能。

## 凭证

凭证为随机高熵 token，服务器只保存哈希、owner、role、实验授权、过期与撤销状态。默认 90 天过期。浏览器会话最长 8 小时，内存保存 session，cookie 设置 HttpOnly、SameSite=Strict；配置 HTTPS origin 时设置 Secure。服务器重启会使浏览器 session 失效，但不会删除实验数据或凭证哈希。

POST 校验 Origin；cookie 会话还校验 CSRF token。客户端 token 仅通过请求头发送，不放 URL。HTTPS 客户端验证服务端证书且不跟随重定向。代理不得记录 Authorization、Cookie、请求体或上传配置。配置文件/原始日志可能包含敏感内容，提交前由团队审阅；程序不保证把用户主动放入原始日志的所有秘密自动脱敏。

```bash
python -m searchagent_lab team-tokens --data-dir /srv/searchagent-lab/center
python -m searchagent_lab team-revoke --data-dir /srv/searchagent-lab/center --id key-ACTUAL-ID
```

发放和撤销凭证使用独立认证数据库，可在中心运行期间执行；撤销会令其现有浏览器会话在下次请求时失效。通过 `team-token --out` 创建的文件不允许静默覆盖；丢失 token 则撤销并重新发放，不把摘要当作可恢复的明文。

## 服务部署

以独立非 root 系统用户运行中心，初始目录私有、文件创建 `UMask=0077`。确保中心目录和所有 SQLite 主库、sidecar、journals、原始 ZIP、blobs 均在常驻本地持久盘上。不要将其放到 NFS、SMB 或随作业销毁的训练实例。

`deploy/` 中是模板，不是已执行的服务器安装。替换账户、venv、路径、HTTPS 域名和证书后，使用目标服务器自带的 `nginx -t` / `systemd-analyze verify` 审核。限制请求体、连接数、慢请求时间、反向代理临时目录和中心磁盘。接收器限制 8 个处理线程，写入仍然串行；它没有大型部署的配额/高可用/多进程 writer 功能。

公网入口必须正确保留 Host，`--public-origin` 必须与浏览器 origin 一致。不要因跨域报错就关闭认证、Origin 或 TLS 检查；改正确的代理与域名配置。未配置公网域名时可以先使用 SSH 隧道完成闭环。

中心使用 SQLite attached rollback-journal 事务保证数据记录共同提交；落盘步骤在成功回执之前。此实现测试了后段校验失败回滚，没有进行真实断电、存储故障或网络文件系统破坏实验。系统用户、存储管理员和拥有中心文件访问权的人属于信任边界内；哈希不是防有权限的管理员改盘的证据签名。

## 备份与恢复

先安排停止中心服务，让正在处理的请求退出，再备份**整个**中心目录，不只复制分数表：

```bash
# 按实际 service 名称替换。生产操作由维护者确认。
sudo systemctl stop searchagent-center
sudo tar -C /srv/searchagent-lab -czf /your-backup-location/center-backup.tgz center
sudo systemctl start searchagent-center
```

备份含凭证哈希和完整研究原件，按敏感资料保存，不开放下载。对在线运行中的多个 SQLite 文件直接普通复制不能当作一致性备份；本版没有自动在线备份命令。

恢复到独立的私有目录，确认文件 owner/权限，保持中心服务停止；先用 `PRAGMA integrity_check` 检查每个 SQLite 文件，再校验 `.team/received/*.zip` 的 manifest/SHA256，并核对归档 blob。启动恢复目录，查看一条真实轨迹与一个 batch 引用。不要同时启动新旧中心写同一目录；切换后使各采集端明确指向选定中心。

`team-check <zip>` 只校验传输容器、清单和哈希，不证明模型真的运行，也不重新计算科学评分。服务器回执和上传者来源可审计，但不是远端执行的可信硬件证明。

## 官方实现参考

本项目借鉴而非声称部署了下列系统。技术部署语义参考官方文档：

- Python ZIP archive API / 安全边界：https://docs.python.org/3/library/zipfile.html
- Python HTTP server 使用边界：https://docs.python.org/3/library/http.server.html
- SQLite ATTACH 的跨数据库原子事务条件：https://www.sqlite.org/lang_attach.html
- NGINX 请求缓冲、Host 转发、超时：https://nginx.org/en/docs/http/ngx_http_proxy_module.html

本版的具体权限和格式以源码及 `test_team.py` 为准，不因为引用这些文档就声称已达到生产安全保证。
