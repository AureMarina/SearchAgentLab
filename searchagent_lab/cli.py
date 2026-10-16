from __future__ import annotations
import argparse
from .config import default_data_dir
import json
import platform
import sys
import threading
import webbrowser
from pathlib import Path
from . import __version__
from .store import Store, ValidationError, NotFoundError
from .io import import_file, import_metrics_csv
from .demo import seed_demo
from .server import LabServer


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="searchagent-lab", description="本地训练指标、checkpoint 评测与轨迹回放。不会运行训练或调用模型。")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    for command, help_text in (("inspect", "直接导入 JSONL 到独立轨迹检查区并启动，不产生正式评测成绩"), ("init", "创建空工作区"), ("demo", "生成明确标记的演示数据并启动"), ("serve", "启动本地查看器"), ("doctor", "检查 Python、数据库与记录数量"), ("import", "事务式导入 JSON / JSONL / .gz"), ("import-metrics", "导入数值 CSV（保留原列名）"), ("export", "导出某次评测的完整证据 JSON"), ("freeze-panel", "登记只读题单并导出带哈希快照；不代表已隔离"), ("validation-template", "创建待人工填入真实问题的空模板"), ("audit-isolation", "核查所提供全项目训练/正式测试清单"), ("repro-check", "比较同一 checkpoint 的两次已有评测"), ("assign-run", "将旧版未归类运行显式归入实验")):
        sp = sub.add_parser(command, help=help_text)
        sp.add_argument("--data-dir", default=None, help="工作区目录；建议演示与真实数据分开")
        if command in ("demo", "serve", "inspect"):
            sp.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
            sp.add_argument("--port", type=int, default=8765)
            sp.add_argument("--open", action="store_true", help="启动后打开默认浏览器")
        if command == "inspect":
            sp.add_argument("file", nargs="?", help="UTF-8 .json / .jsonl；也可以启动后从网页打开")
            sp.add_argument("--no-serve", action="store_true")
        if command == "demo":
            sp.add_argument("--samples-per-dataset", type=int, default=5, help="演示每数据集1–10题；仅新工作区生效，不重抽已有题单")
            sp.add_argument("--no-serve", action="store_true", help="只生成演示数据")
        if command in ("import", "import-metrics", "freeze-panel"):
            sp.add_argument("file")
        if command == "import-metrics":
            sp.add_argument("--run", required=True, help="已登记的 run ID")
            sp.add_argument("--step-key", default="step")
        if command == "export":
            sp.add_argument("--evaluation", required=True)
            sp.add_argument("--out", required=True)
        if command in ("freeze-panel", "validation-template", "audit-isolation", "repro-check"):
            sp.add_argument("--out", required=True, help="新文件路径；不覆盖既有报告或题单")
        if command == "audit-isolation":
            sp.add_argument("--panel", required=True)
            sp.add_argument("--inventory", required=True)
        if command == "repro-check":
            sp.add_argument("--a", required=True)
            sp.add_argument("--b", required=True)
        if command == "assign-run":
            sp.add_argument("--run", required=True)
            sp.add_argument("--experiment", required=True)
            sp.add_argument("--reason", required=True)
    for name, help_text in (("freeze-contract", "运行前校验并冻结 Case Contract，供评测器记录其哈希"), ("diagnostic-benchmark", "以独立标注核查诊断规则；不是根因准确率"), ("component-replay", "对固定输入执行内置纯函数组件对照，不运行 Agent")):
        sp=sub.add_parser(name,help=help_text)
        sp.add_argument("file",help="JSON 任务 / 独立标注清单")
        sp.add_argument("--out",required=True)
        sp.add_argument("--data-dir",default=None)
    for name,help_text in (("methods", "打开 SkillBank / 方法过程，不创建虚构运行"), ("import-methods", "导入版本化方法过程和产物"), ("export-method", "导出方法对象和已登记来源")):
        sp=sub.add_parser(name,help=help_text)
        sp.add_argument("--data-dir",default=None)
        if name in ("methods","import-methods"):
            sp.add_argument("file",nargs="?" if name=="methods" else None)
            sp.add_argument("--project",default="")
            sp.add_argument("--experiment",default="")
            sp.add_argument("--run",default="")
            sp.add_argument("--snapshot-id")
        if name=="methods":
            sp.add_argument("--port",type=int,default=8765)
            sp.add_argument("--host",choices=("127.0.0.1","localhost"),default="127.0.0.1")
            sp.add_argument("--open",action="store_true")
            sp.add_argument("--no-serve",action="store_true")
        if name=="export-method":
            sp.add_argument("--id",required=True);sp.add_argument("--out",required=True)
    for name, help_text in (("register-profile", "显式登记主实验或文献复现用途，不改变原记录"), ("import-batch", "导入明确关联的训练 batch / rollout group")):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("file")
        sp.add_argument("--data-dir", default=None)
    sp = sub.add_parser("setup-font", help="一次性安装仓耳玉楷；从官方获取或读取本地原始 TTF，不修改评测数据")
    sp.add_argument("--data-dir", default=None)
    sp.add_argument("--font-file", help="离线安装：已获得使用许可的仓耳玉楷官方原始 TTF")
    sp.add_argument("--timeout", type=float, default=20, help="官方字体请求超时秒数，默认 20；失败后查看器仍可运行")
    from .team.cli import add_parsers
    add_parsers(sub)
    return p


def ensure_new(path: str) -> Path:
    out=Path(path).expanduser()
    if out.exists(): raise ValidationError("Output already exists; choose a new filename")
    out.parent.mkdir(parents=True,exist_ok=True)
    return out


def write_new(path: str, data: dict) -> None:
    out=ensure_new(path)
    with out.open("x",encoding="utf-8") as handle:
        json.dump(data,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        if hasattr(args, "data_dir") and args.data_dir is None:
            args.data_dir = default_data_dir()
        if args.command == "setup-font":
            from .ui_fonts import setup_font
            print(json.dumps(setup_font(args.data_dir, args.font_file, args.timeout), ensure_ascii=False, indent=2))
            return
        if getattr(args, "team_command", False):
            from .team.cli import dispatch
            dispatch(args)
            return
        if args.command in ("demo", "serve", "inspect", "methods") and not 1 <= args.port <= 65535:
            raise ValidationError("Port must be between 1 and 65535")
        if args.command in ("freeze-contract", "diagnostic-benchmark", "component-replay"):
            from .inspection.repository import strict_json
            source=strict_json(Path(args.file).read_text(encoding="utf-8-sig"))
            if args.command=="freeze-contract":
                from .diagnostic_loop.contracts import validate_contract
                from .inspection.repository import digest,now
                from .diagnostic_loop.repository import LoopRepository
                validate_contract(source)
                ensure_new(args.out)
                with LoopRepository(args.data_dir) as loop:
                    loop._put('frozen:'+source['id']+':'+source['version'],'contract_definition',{'target':'contract-registry','source_hash':'global'},source)
                report={"kind":"frozen_case_contract","contract":source,"contract_hash":digest(source),"frozen_at":now(),
                        "note":"Record contract_hash in the actual execution context. Freezing is not proof the remote agent followed it."}
            elif args.command=="diagnostic-benchmark":
                from .diagnostic_loop.benchmark import assess
                report=assess(source)
            else:
                from .diagnostic_loop.verification import component
                from .inspection.repository import digest
                report={"kind":"standalone_component_comparison","input_hash":digest(source["input"]),
                        "baseline":component(source["input"],source["operation"],source["baseline"]),
                        "candidate":component(source["input"],source["operation"],source["candidate"]),
                        "autonomous_uplift":None,"causal_claim":False}
            write_new(args.out,report)
            print(json.dumps({"out":args.out,"kind":report["kind"]},ensure_ascii=False));return
        if args.command in ('methods','import-methods','export-method'):
            from .methods.sdk import MethodRecorder
            from urllib.parse import urlencode
            with MethodRecorder(args.data_dir) as rec:
                selected={k:v for k,v in [('project_id',getattr(args,'project','')),('experiment_id',getattr(args,'experiment','')),('run_id',getattr(args,'run',''))] if v}
                if args.command=='export-method':
                    write_new(args.out,rec.repo.export(args.id));print('Exported:',args.out);return
                result=None
                if args.file:
                    result=rec.import_file(args.file,selected or None,args.snapshot_id)
                    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
                if args.command=='import-methods' or args.no_serve:return
                with LabServer((args.host,args.port),rec.store) as server:
                    url='http://'+args.host+':'+str(server.server_address[1])+('/#/skill-library' if result and result.get('archive_id') else '/#/methods')
                    params={**selected}
                    if result:
                        params['archive' if result.get('archive_id') else 'id']=result.get('archive_id') or result.get('snapshot_id') or result['object_ids'][0]
                        if not selected:params['standalone']='1'
                    if params:url+='?'+urlencode(params)
                    print('SearchAgent Lab '+__version__+'\n'+url+'\nLocal evidence only; Ctrl+C to stop.',flush=True)
                    if args.open:webbrowser.open(url)
                    try:server.serve_forever(poll_interval=.3)
                    except KeyboardInterrupt:print('Stopped. Evidence retained.')
            return
        if args.command in ("register-profile", "import-batch"):
            from .studio.sdk import StudioRecorder
            from .inspection.repository import strict_json
            data = strict_json(Path(args.file).read_text(encoding="utf-8-sig"))
            with StudioRecorder(args.data_dir) as rec:
                result = rec.repo.register_profile(data) if args.command == "register-profile" else rec.repo.record_batch(data)
            print(json.dumps(result,ensure_ascii=False,indent=2));return
        with Store(args.data_dir) as store:
            if args.command == "init":
                print(f"Workspace ready: {store.data_dir}\nStart: python -m searchagent_lab serve --data-dir \"{store.data_dir}\"")
            elif args.command == "doctor":
                print(json.dumps({"version": __version__, "python": sys.version.split()[0], "platform": platform.platform(), "database": str(store.db_path), **store.health()}, indent=2, ensure_ascii=False))
            elif args.command == "import":
                print(json.dumps({"imported": import_file(store, args.file), "workspace": str(store.data_dir)}, indent=2, ensure_ascii=False))
            elif args.command == "import-metrics":
                print(json.dumps(import_metrics_csv(store, args.file, args.run, args.step_key), indent=2, ensure_ascii=False))
            elif args.command == "export":
                out = Path(args.out).expanduser()
                if out.exists():
                    raise ValidationError("Output already exists; choose a new filename")
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(store.export_evaluation(args.evaluation), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                print(f"Exported: {out.resolve()}")
            elif args.command == "validation-template":
                write_new(args.out, {"id":"search-validation-fixed-v1", "name":"search_validation_fixed_v1", "version":"v1", "split":"dev", "demo":False,
                    "selection_status":"DRAFT_NOT_APPROVED", "description":"待填入真实题目并审核；空模板不可导入，不是已验证隔离的70题。",
                    "target_counts":{ds:10 for ds in ("NQ","TriviaQA","PopQA","HotpotQA","2Wiki","MuSiQue","Bamboogle")},
                    "samples":[], "sample_contract":{"id":"stable validation ID", "dataset":"actual dataset", "question":"exact question", "answers":["gold and supported aliases"], "metadata":{"source_id":"original ID", "source_revision":"immutable source version", "source_split":"original split"}}})
                print("Draft template created. Fill actual records, review, then remove draft/sample_contract fields explicitly before freeze-panel.")
            elif args.command == "freeze-panel":
                ensure_new(args.out)
                source=json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
                if not isinstance(source,dict): raise ValidationError("Panel must be an object")
                if not source.get("demo"):
                    for sample in source.get("samples",[]):
                        meta=sample.get("metadata",{})
                        for field in ("source_id","source_revision","source_split"):
                            if not meta.get(field) or store._contains_unknown(meta[field]):
                                raise ValidationError("Real fixed questions require metadata."+field)
                panel_id=store.register_panel(source)
                write_new(args.out,store.panel(panel_id))
                print(json.dumps({"panel_id":panel_id,"sha256":store.panel(panel_id)["fingerprint"],"isolation":"not implied by freezing; audit separately"},ensure_ascii=False,indent=2))
            elif args.command == "audit-isolation":
                from .isolation import audit_panel
                ensure_new(args.out)
                path=Path(args.inventory).resolve()
                inventory=json.loads(path.read_text(encoding="utf-8-sig"))
                projects=[p["id"] for p in store.projects() if not p.get("legacy")]
                report=audit_panel(store.panel(args.panel),inventory,path.parent,projects or None)
                report=store.record_audit(args.panel,report)
                write_new(args.out,report)
                print(json.dumps({"status":report["status"],"exact_conflicts":len(report["exact_conflicts"]),"near_candidates":len(report["near_candidates"]),"out":str(Path(args.out).resolve())},ensure_ascii=False))
                if report["status"]=="blocked_overlap": raise SystemExit(3)
                if report["status"]!="no_detected_overlap_in_supplied_scope": raise SystemExit(4)
            elif args.command == "repro-check":
                ensure_new(args.out)
                report=store.reproducibility(args.a,args.b)
                write_new(args.out,report)
                print(json.dumps({"eligible":report["eligible"],"counts":report.get("counts"),"all_recorded_evidence_matches":report.get("all_recorded_evidence_matches",False)},ensure_ascii=False))
                if not report.get("all_recorded_evidence_matches"): raise SystemExit(3)
            elif args.command == "assign-run":
                store.assign_legacy_run(args.run,args.experiment,args.reason)
                print("Legacy run classified; original metrics, checkpoints and evaluation evidence retained.")
            else:
                if args.command == "inspect":
                    from .inspection import InspectionRepository
                    if args.file:
                        path=Path(args.file).expanduser()
                        if path.stat().st_size > 50*1024*1024: raise ValidationError("File exceeds 50 MiB")
                        with InspectionRepository(store.data_dir) as repo:
                            result=repo.import_bytes(path.read_bytes(), path.name)
                            print(json.dumps(result, ensure_ascii=False), flush=True)
                    if args.no_serve: return
                if args.command == "demo":
                    print("DEMO ONLY — 合成轨迹与成绩，不是真实训练/评测结果。", flush=True)
                    print(json.dumps(seed_demo(store, args.samples_per_dataset), ensure_ascii=False), flush=True)
                    if args.no_serve:
                        return
                with LabServer((args.host, args.port), store) as server:
                    url = f"http://{args.host}:{server.server_address[1]}"
                    if args.command == "inspect": url += "/#/trace"
                    print(f"\nSearchAgent Lab {__version__}\n{url}\nWorkspace: {store.data_dir}\nLocal only; Ctrl+C to stop. No external requests.\n", flush=True)
                    if args.open:
                        t = threading.Timer(.6, lambda: webbrowser.open(url))
                        t.daemon = True
                        t.start()
                    try:
                        server.serve_forever(poll_interval=.3)
                    except KeyboardInterrupt:
                        print("\nStopped. Logs and annotations are retained.", flush=True)
    except (ValueError, NotFoundError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
