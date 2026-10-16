from __future__ import annotations
import json
import os
import time
import uuid
from pathlib import Path
from ..core import ValidationError, now
from ..inspection.repository import strict_json
from .package import init_source,source,export_run,read_package,add_artifact,local_dir
from .client import Outbox,sync_once,endpoint
from .util import durable_write,json_bytes


def add_parsers(sub):
    descriptions={
     'team-init':'登记本平台独立采集端身份', 'team-export-plan':'从已有本地实验导出配置/题单登记包', 'team-plan':'导入中心/本地实验配置包',
     'team-run':'运行开始时登记 UUID 和来源，不执行训练',
     'team-export':'导出自包含结果 ZIP；不复制数据库', 'team-check':'只校验结果包清单/哈希',
     'team-import':'中心离线导入（与服务互斥）', 'team-token':'创建可撤销、限定用途的访问凭证',
     'team-revoke':'撤销凭证及其会话', 'team-tokens':'只列凭证元数据，不输出秘密',
     'team-serve':'启动需认证的中心（仍只监听 loopback）',
     'team-add-artifact':'显式登记需归档的文件或外部存储引用',
     'team-enqueue':'将运行快照放入本地持久待发送队列',
     'team-sync':'采集当前运行快照并发送一次', 'team-watch':'独立采集进程：周期采集、提交及重试',
     'team-flush':'仅补传已有待发送包', 'team-status':'查看本地同步队列',
     'team-retry':'显式解除已排查的 blocked 提交', 'team-prune':'仅删除已确认送达包的本地 ZIP，保留回执'}
    for name,description in descriptions.items():
        p=sub.add_parser(name,help=description);p.set_defaults(team_command=True)
        p.add_argument('--data-dir',default=None)
        if name=='team-init':p.add_argument('--operator',required=True);p.add_argument('--platform',required=True)
        if name in ('team-plan','team-import','team-check'):p.add_argument('file')
        if name=='team-plan':p.add_argument('--center',action='store_true',help='用中心单写者事务登记；中心服务运行中请用页面登记计划')
        if name=='team-import':
            p.add_argument('--allow-definitions',action='store_true',help='管理员显式批准首次接收包中的项目/题单/用途；默认不允许新增')
            p.add_argument('--owner',required=True,help='实际提交人，必须与随后联网上传凭证的 owner 一致')
        if name in ('team-export','team-add-artifact'):p.add_argument('--run',required=True)
        if name=='team-export':p.add_argument('--out',required=True)
        if name=='team-export-plan':p.add_argument('--experiment',required=True);p.add_argument('--out',required=True)
        if name=='team-run':
            p.add_argument('--code-dir',help='实际训练代码目录，用于记录 git revision/dirty；不上传源码')
            p.add_argument('--experiment',required=True);p.add_argument('--name',required=True)
            p.add_argument('--seed',type=int);p.add_argument('--job-id',default='');p.add_argument('--base-model',default='')
            p.add_argument('--config',help='Actual configuration JSON; never reads your environment variables')
        if name=='team-token':
            p.add_argument('--owner',required=True);p.add_argument('--role',required=True,choices=['admin','viewer','uploader'])
            p.add_argument('--experiment',action='append',default=[]);p.add_argument('--days',type=int,default=90)
            p.add_argument('--out',required=True,help='New private token file, never prints token to the log')
        if name=='team-revoke':p.add_argument('--id',required=True)
        if name=='team-serve':
            p.add_argument('--port',type=int,default=8765);p.add_argument('--public-origin',help='https://lab.example.org; omit only for loopback / SSH forwarding')
        if name in ('team-sync','team-watch','team-flush'):
            p.add_argument('--url',default=os.environ.get('SEARCHAGENT_LAB_URL'))
            p.add_argument('--token-env',default='SEARCHAGENT_LAB_TOKEN')
        if name in ('team-sync','team-watch','team-enqueue'):
            g=p.add_mutually_exclusive_group(required=True);g.add_argument('--run',action='append');g.add_argument('--all',action='store_true')
        if name=='team-watch':p.add_argument('--interval',type=float,default=30)
        if name=='team-flush':p.add_argument('--force',action='store_true',help='Ignore retry backoff once; does not unblock conflicts')
        if name=='team-add-artifact':
            p.add_argument('--key',required=True);g=p.add_mutually_exclusive_group(required=True)
            g.add_argument('--file');g.add_argument('--uri');p.add_argument('--sha256');p.add_argument('--required',action='store_true');p.add_argument('--role',default='evidence')


def _print(d):print(json.dumps(d,ensure_ascii=False,indent=2),flush=True)


def dispatch(args):
    cmd=args.command;data_dir=args.data_dir
    if cmd=='team-init':_print(init_source(data_dir,args.operator,args.platform));return
    if cmd=='team-check':
        m,p,b=read_package(args.file);_print({'manifest':m,'transport_integrity':'verified','semantic_import':'not performed','remote_execution_verified':False});return
    if cmd in ('team-token','team-tokens','team-revoke'):
        from .auth import Auth
        a=Auth(data_dir)
        if cmd=='team-revoke':a.revoke(args.id);_print({'revoked':args.id});return
        if cmd=='team-tokens':_print({'tokens':a.list()});return
        out=Path(args.out).expanduser().resolve()
        if out.exists():raise ValidationError('Token file already exists; choose a new private filename')
        token,record=a.issue(args.owner,args.role,args.experiment,args.days)
        try:durable_write(out,(token+'\n').encode(),exclusive=True);out.chmod(0o600)
        except BaseException:a.revoke(record['id']);raise
        _print(record|{'token_file':str(out),'secret_printed':False});return
    if cmd in ('team-serve','team-import') or (cmd=='team-plan' and args.center):
        from .hub import Hub
        with Hub(data_dir) as hub:
            if cmd=='team-import':_print(hub.ingest(args.file,allow_definitions=args.allow_definitions,submitter_owner=args.owner));return
            if cmd=='team-plan':_print(hub.apply_plan(strict_json(Path(args.file).read_text(encoding='utf-8-sig'))));return
            from .server import TeamServer
            if not 1<=args.port<=65535:raise ValidationError('Port must be 1–65535')
            if (local_dir(data_dir)/'source.json').exists():raise ValidationError('Use a separate center workspace, not an active collector source workspace')
            with TeamServer(('127.0.0.1',args.port),hub,args.public_origin) as server:
                _print({'version':'0.7.0','mode':'authenticated_center','origin':server.public_origin,
                        'listener':'127.0.0.1:'+str(args.port),'data_dir':str(hub.data_dir),
                        'notice':'Use HTTPS reverse proxy with body/concurrency limits, or SSH/VPN. No remote GPU jobs are started.'})
                try:server.serve_forever(poll_interval=.3)
                except KeyboardInterrupt:print('Center stopped; records retained.')
        return
    if cmd=='team-plan':
        from ..studio.sdk import StudioRecorder
        p=strict_json(Path(args.file).read_text(encoding='utf-8-sig'))
        if p.get('plan_schema')!='searchagent-lab.plan/v1':raise ValidationError('Unsupported plan schema')
        if p['experiment']['project_id']!=p['project']['id'] or p['profile']['experiment_id']!=p['experiment']['id']:raise ValidationError('Plan ownership mismatch')
        # Source registration is done once before training. Rollback guarantees for
        # ongoing multi-DB imports belong to center mode, not this setup operation.
        with StudioRecorder(data_dir) as r:
            r.store.register_project(p['project']);r.store.register_experiment(p['experiment'])
            for panel in p.get('panels',[]):r.store.register_panel(panel)
            from .hub import clean_profile
            profile=r.repo.register_profile(clean_profile(p['profile']))
        _print({'experiment_id':p['experiment']['id'],'track':profile['track']});return
    if cmd=='team-export-plan':
        from ..studio.sdk import StudioRecorder
        from ..core import Store as CoreStore
        from .hub import clean_profile
        with StudioRecorder(data_dir) as r:
            exp=r.store._get('experiments',args.experiment);profile=r.repo.profile(args.experiment)
            if profile['track']=='unassigned':raise ValidationError('Register an explicit main/reproduction profile first')
            ids=[x[0] for x in r.store.conn.execute('SELECT DISTINCT e.panel_id FROM evaluations e JOIN checkpoints c ON e.checkpoint_id=c.id JOIN run_ownership o ON c.run_id=o.run_id WHERE o.experiment_id=?',(args.experiment,))]
            p={'plan_schema':'searchagent-lab.plan/v1','project':r.store._get('projects',exp['project_id']),
               'experiment':exp,'profile':clean_profile(profile),'panels':[CoreStore.panel(r.store,k) for k in ids]}
        durable_write(Path(args.out).expanduser().resolve(),json_bytes(p),exclusive=True)
        _print({'plan_file':args.out,'experiment_id':args.experiment,'panels':len(ids),'note':'Import parent plans first. Cross-run baseline IDs are not automatically relocated.'});return
    if cmd=='team-run':
        from .sdk import register_team_run
        config=strict_json(Path(args.config).read_text(encoding='utf-8-sig')) if args.config else {}
        rid=register_team_run(data_dir,args.experiment,args.name,seed=args.seed,base_model=args.base_model,config=config,platform_job_id=args.job_id,code_dir=args.code_dir)
        _print({'run_id':rid,'state':'registered','training_started':False});return
    if cmd=='team-export':_print(export_run(data_dir,args.run,args.out));return
    if cmd=='team-add-artifact':_print(add_artifact(data_dir,args.run,args.key,args.file,args.uri,args.sha256,args.required,args.role));return
    if cmd in ('team-status','team-retry','team-prune','team-flush'):
        with Outbox(data_dir) as box:
            if cmd=='team-status':_print(box.status())
            elif cmd=='team-retry':_print({'unblocked':box.retry_blocked()})
            elif cmd=='team-prune':_print(box.prune())
            else:
                if not args.url:raise ValidationError('--url or SEARCHAGENT_LAB_URL is required')
                token=os.environ.get(args.token_env,'')
                if not token:raise ValidationError('Set upload credential environment variable '+args.token_env)
                result=box.flush(args.url,token,args.force);_print(result)
                if result['counts']['pending'] or result['counts']['blocked']:raise SystemExit(3)
        return
    if cmd in ('team-sync','team-enqueue','team-watch'):
        def runs():
            if args.run:return args.run
            from ..store import Store
            with Store(data_dir) as store:return [r['id'] for r in store.runs()]
        if cmd!='team-enqueue':
            if not args.url:raise ValidationError('--url or SEARCHAGENT_LAB_URL is required')
            endpoint(args.url)
            if not os.environ.get(args.token_env):raise ValidationError('Set upload credential environment variable '+args.token_env)
        def once():return sync_once(data_dir,runs(),getattr(args,'url',None),os.environ.get(getattr(args,'token_env',''),''),cmd!='team-enqueue')
        if cmd=='team-watch':
            if args.interval<5:raise ValidationError('Collector interval must be at least 5 seconds')
            try:
                while True:
                    try:_print(once())
                    except (OSError,ValidationError) as e:_print({'collector_error':str(e),'training_interrupted':False})
                    time.sleep(args.interval)
            except KeyboardInterrupt:print('Collector stopped; pending packages retained.')
        else:
            result=once();_print(result)
            if any(x['state']=='collection_error' for x in result['collection']) or (cmd=='team-sync' and (result['counts']['pending'] or result['counts']['blocked'])):raise SystemExit(3)
        return
    raise ValidationError('Unknown team command')
