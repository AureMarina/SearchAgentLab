"""Deterministic, fully synthetic UI fixtures. NOT benchmark examples/results."""
from __future__ import annotations
import math
import random
from .store import Store, ValidationError

DEMO_RUN = "demo-search-rl"
PROTOCOL = {
    "scorer": {"name": "synthetic_casefold_em", "version": "demo-v1", "normalization": "strip + casefold; demonstration only"},
    "parser": {"name": "recorded_final_answer", "version": "demo-v1"},
    "retriever": {"name": "synthetic-local-fixtures", "index_sha256": "demo-index-v1", "top_k": 3},
    "budget": {"max_tool_calls": 4, "max_output_tokens": 2048, "context_window": 8192},
    "decoding": {"temperature": 0, "top_p": 1},
    "harness_version": "synthetic-fixture-v1", "seed": 42,
}


def make_sample(dataset: str, i: int) -> dict:
    titles = ["The Amber Tide", "Winter at Luma", "A Map of Rain", "The Quiet Observatory", "Silver Orchard", "Paper Lanterns", "The Last Tram", "Letters to Dawn"]
    names = ["Mira Vale", "Jonas Reed", "Elena Voss", "Nolan Finch", "Celia Wren", "Ari Sol", "Lena Ash", "Noel Hart"]
    cities = ["Harborwick", "Northmere", "Willowford", "Ashhaven", "Bellwater", "Stoneharbor", "Pinecross", "Larkridge"]
    countries = ["Norland", "Veloria", "Estara", "Westmere"]
    title = f"{titles[i % 8]} {i + 1:02d}"
    author = f"{names[(i * 3) % 8]} {i + 1:02d}"
    city = cities[(i * 5) % 8]
    country = countries[i % 4]
    question = f"In which city was the author of {title} born?"
    answer = city
    if dataset == "MuSiQue":
        question = f"Which country contains the birthplace of the author of {title}?"
        answer = country
    elif dataset in ("NQ", "TriviaQA", "PopQA"):
        question = f"Who wrote {title}?"
        answer = author
    return {"id": f"synthetic-{i+1:03d}", "dataset": dataset, "question": question, "answers": [answer],
            "metadata": {"synthetic": True, "title": title, "author": author, "city": city, "country": country,
                         "hops": 3 if dataset == "MuSiQue" else 1 if dataset in ("NQ", "TriviaQA", "PopQA") else 2,
                         "reference_evidence": [f"{title} was written by {author}.", f"{author} was born in {city}.", f"{city} is located in {country}."]}}


def make_trace(sample: dict, cp_index: int, sample_index: int) -> dict:
    rng = random.Random(701 + cp_index * 4099 + sample_index * 71)
    probs = [0.20, 0.31, 0.42, 0.53]
    correct = rng.random() < probs[cp_index]
    if sample_index == 0:
        correct = cp_index in (1, 3)
    runtime_error = (not correct and sample_index % 29 == 7)
    early = not correct and sample_index % 3 == 1 and not runtime_error
    meta = sample["metadata"]
    system = "You are a search assistant. Use retrieved evidence to answer the question. Return a short final answer."
    messages = [{"role": "system", "content": system}, {"role": "user", "content": sample["question"]}]
    q1 = f'"{meta["title"]}" author'
    out1 = f"I need to identify the author before looking up the birthplace.\n<search>{q1}</search>"
    result1 = {"query": q1, "results": [{"rank": 1, "doc_id": "fixture-book", "title": meta["title"], "text": meta["reference_evidence"][0]}, {"rank": 2, "doc_id": "fixture-review", "title": "Book review", "text": "A review of the work; no biographical information."}], "fixture": True}
    visible1 = f'[1] {meta["title"]}\n{meta["reference_evidence"][0]}\n[2] Book review\nA review of the work; no biographical information.'
    steps = [{"index": 0, "kind": "model", "title": "确定第一轮检索目标", "input": messages.copy(), "output": out1, "latency_ms": 421, "status": "ok", "metadata": {"fixture": True}},
             {"index": 1, "kind": "tool", "title": "检索作品作者", "tool_call": {"name": "search", "arguments": {"query": q1}}, "tool_response": result1, "model_visible": visible1, "latency_ms": 185, "status": "ok"}]
    messages = messages + [{"role": "assistant", "content": out1}, {"role": "tool", "content": visible1}]
    predicted = sample["answers"][0] if correct else "Eastford"
    if sample["dataset"] in ("NQ", "TriviaQA", "PopQA"):
        predicted = sample["answers"][0] if correct else "Robin Brook"
    if early:
        steps.append({"index": 2, "kind": "model", "title": "输出最终答案", "input": messages, "output": f"<answer>{predicted}</answer>", "status": "ok", "latency_ms": 303})
    else:
        q2 = f'"{meta["author"]}" birthplace' if correct or runtime_error else f'"{meta["title"]}" publication city'
        out2 = f"The retrieved author is {meta['author']}.\n<search>{q2}</search>"
        steps.append({"index": 2, "kind": "model", "title": "利用中间实体继续检索", "input": messages.copy(), "output": out2, "latency_ms": 389, "status": "ok"})
        text2 = " ".join(meta["reference_evidence"][1:]) if correct else f"The book's first publisher was based in Eastford."
        response2 = {"error": "Retriever timed out after 10 seconds", "code": "timeout"} if runtime_error else {"query": q2, "results": [{"rank": 1, "doc_id": "fixture-biography", "title": meta["author"] if correct else "Publisher archive", "text": text2}], "fixture": True}
        step = {"index": 3, "kind": "tool", "title": "检索中间实体", "tool_call": {"name": "search", "arguments": {"query": q2}}, "tool_response": response2, "latency_ms": 10000 if runtime_error else 237, "status": "error" if runtime_error else "ok"}
        if runtime_error:
            step["error"] = "RetrieverTimeout (synthetic example)"
        else:
            step["model_visible"] = text2
        steps.append(step)
        if not runtime_error:
            messages += [{"role": "assistant", "content": out2}, {"role": "tool", "content": text2}]
            steps.append({"index": 4, "kind": "model", "title": "汇总证据并输出答案", "input": messages, "output": f"<answer>{predicted}</answer>", "latency_ms": 337, "status": "ok"})
    final = None if runtime_error else f"<answer>{predicted}</answer>"
    return {"dataset": sample["dataset"], "sample_id": sample["id"], "scores": {"em": int(correct), "f1": int(correct)},
            "runtime_status": "timeout" if runtime_error else "ok", "raw_final_output": final,
            "parsed_answer": None if runtime_error else predicted, "reward": float(correct),
            "termination_reason": "retriever_timeout" if runtime_error else "final_answer",
            "usage": {"model_calls": sum(s["kind"] == "model" for s in steps), "tool_calls": sum(s["kind"] == "tool" for s in steps),
                      "latency_ms": sum(s["latency_ms"] for s in steps), "input_tokens": 640 + sample_index * 3, "output_tokens": 98 + len(steps) * 23},
            "steps": steps, "metadata": {"synthetic": True, "scoring_note": "All scores, input/output strings and timings are generated fixtures. Timeout explicitly scores zero in this DEMO protocol; the viewer never assigns a score itself."}}



DATASETS = ("NQ", "TriviaQA", "PopQA", "HotpotQA", "2Wiki", "MuSiQue", "Bamboogle")
PANEL_ID = "demo-search-validation-fixed-v1"
PROTOCOL_V2 = {
    **PROTOCOL, "protocol_version": 2,
    "input": {"prompt_sha256":"synthetic-prompt-v2", "chat_template_sha256":"synthetic-template-v2",
              "tokenizer_revision":"synthetic-tokenizer-v2", "stop_conditions":["</answer>"]},
    "retriever": {**PROTOCOL["retriever"], "corpus_version":"synthetic-fixtures-v2", "version":"fixture-retriever-v2",
                  "ranking":"fixture order", "truncation":{"observation_tokens":1024,"rule":"fixed fixture text"}},
    "runtime": {"backend":"synthetic-generator", "backend_version":"2", "hardware":"no GPU used",
                "dtype":"no inference", "batch_size":1, "determinism":"fixed fixture generation"},
    "mode":"inference_only", "groundtruth_to_agent":False,
    "state_reset":{"per_question":True,"between_evaluations":True},
}


def seed_demo(store: Store, count: int = 5) -> dict:
    from copy import deepcopy
    if not isinstance(count,int) or isinstance(count,bool) or not 1 <= count <= 10:
        raise ValidationError("Demo questions per dataset must be 1–10; selected once in a new workspace")
    runs = store.runs()
    if runs or store.projects():
        if runs and all(r.get("demo") and r.get("demo_version")==2 for r in runs):
            panel = store.panel(PANEL_ID)
            if panel["size"] != count*7:
                raise ValidationError("Demo panel already frozen with another count; use a new workspace, never redraw it")
            return {"existing":True, **store.health()}
        raise ValidationError("Demo generation refuses a non-empty/legacy workspace. Choose a separate --data-dir")
    with store.transaction():
        definitions = {
            "self-play":{"selfplay/audit_pass_rate":{"label":"生成题审核通过率", "definition":"通过审核的合成题 / 本轮已审核合成题（演示口径）", "unit":"fraction", "scope":"proposer round"},
                          "selfplay/generated_count":{"label":"生成题数量", "definition":"本轮生成候选题数（演示）", "unit":"questions"}},
            "latent":{"latent/carry_norm":{"label":"Carry state L2", "definition":"演示标量，不代表具体算法；实际接入需记录归一化方式及计算位置。", "unit":"fixture", "scope":"solver turn"}},
            "entropy":{"entropy/token_mean":{"label":"Token entropy · nats", "definition":"演示口径：solver action tokens 的分布熵；逐 token 计算 -sum(p*log(p)) 后取均值；不是语义熵。", "unit":"nats", "scope":"solver action-token mask", "aggregation":"mean over tokens and sampled trajectories"}},
        }
        for project_id, name, description in (("self-play","Self-play","出题、审核与 solver 更新；记录每一轮的数据版本。"),
                ("latent","Latent","Text / Persistent / Reset；检查中间状态是否改善同题执行。"),
                ("entropy","Entropy","实验配置定义熵的计算位置、口径与用途。")):
            store.register_project({"id":project_id,"name":name,"description":description,"demo":True,
                "metric_definitions":definitions[project_id], "default_panel_id":PANEL_ID})
        samples=[]
        for ds in DATASETS:
            for i in range(count):
                sample=make_sample(ds,i)
                sample["id"] = f"{ds}-V{i+1:02d}"
                sample["metadata"].update(source_id="synthetic-"+ds+f"-{i+1:02d}",source_revision="synthetic-ui-v2",source_split="synthetic",notice="Not sampled from this benchmark")
                samples.append(sample)
        store.register_panel({"id":PANEL_ID,"name":"search_validation_fixed_v1","version":"v1","split":"dev","demo":True,
            "description":"共享只读的合成 Groundtruth；每数据集 5 题（或启动时指定的 1–10 题）。不是真实隔离完成的 validation。",
            "samples":samples,"selection":{"method":"deterministic synthetic fixtures", "purpose":"UI demonstration"}})
        recipes = [
          ("self-play","sp-e01","E01 · Outcome baseline","sp-text-s42","seed42 · run001",42,"4b",0),
          ("self-play","sp-e02","E02 · Self-play","sp-selfplay-s42","seed42 · run001",42,"4b",1),
          ("latent","lt-e01","E01 · Text baseline","lt-text-s42","seed42 · run001",42,"4b",0),
          ("latent","lt-e02","E02 · Persistent latent","lt-persistent-s42","seed42 · run001",42,"4b",2),
          ("latent","lt-e02","E02 · Persistent latent","lt-persistent-s43","seed43 · run002",43,"4b",3),
          ("latent","lt-e02","E02 · Persistent latent","lt-persistent-8b-s42","seed42 · 8B-run001",42,"8b",4),
          ("entropy","en-e01","E01 · Entropy experiment","en-entropy-s42","seed42 · run001",42,"4b",5),
        ]
        for project,expid,expname,runid,runname,seed,size,variant in recipes:
            store.register_experiment({"id":expid,"project_id":project,"name":expname,"demo":True,
                "objective":"观察固定题目的答案与检索路径如何随 checkpoint 变化（合成演示）。",
                "change":expname.split("·")[-1].strip(),
                "constants":"同一只读题单、检索快照、评测协议；实际训练预算另行登记。",
                "config":{"recipe":expname,"notice":"Synthetic fixtures only; no algorithm executed"}})
            store.register_run({"id":runid,"experiment_id":expid,"name":runname,"seed":seed,"demo":True,"demo_version":2,
                "base_model":"Qwen3-4B-Instruct-2507" if size=="4b" else "Qwen3-8B",
                "model_revision":"synthetic-model-revision-"+size,"algorithm":"veRL / "+expname.split("·")[-1].strip(),
                "status":"completed","created_at":"2026-10-02T08:00:00+00:00",
                "config":{"train_data_version":"synthetic-train-v1","training_budget":{"updates":200,"rollouts":"not executed", "tokens":"not executed"},
                          "stage":"solver" if project!="self-play" else "proposer → audit → solver", "actual_training":False}})
            rng=random.Random(41+variant*100+seed)
            for step in range(0,201,5):
                values={"train/loss":round(.2+.85*math.exp(-step/58)+rng.uniform(-.055,.055),5),
                        "train/reward":round(.16+.36*(1-math.exp(-step/(80+variant*5)))+rng.uniform(-.025,.025),5)}
                if project=="self-play": values.update({"selfplay/audit_pass_rate":round(.35+.24*step/200+rng.uniform(-.03,.03),4),"selfplay/generated_count":80+(step//25)*8})
                elif project=="latent": values["latent/carry_norm"]=round(.7+.1*math.sin(step/30)+rng.uniform(-.03,.03),4)
                else: values["entropy/token_mean"]=round(1.6-.28*step/200+rng.uniform(-.04,.04),4)
                store.log_metrics(runid,step,values)
            for j,step in enumerate((0,50,100,200)):
                cpid=f"{runid}-step{step}"
                store.register_checkpoint({"id":cpid,"run_id":runid,"step":step,"name":"初始模型" if step==0 else f"step{step}",
                    "artifact_uri":f"demo://{runid}/step{step}","weights_sha256":"synthetic-"+cpid})
                evid=cpid+"-eval01"
                store.start_evaluation({"id":evid,"checkpoint_id":cpid,"panel_id":PANEL_ID,"protocol":deepcopy(PROTOCOL_V2),
                    "created_at":f"2026-10-02T{8+j:02d}:30:00+00:00","notes":"合成演示；没有执行模型评测"})
                records=[]
                for i,sample in enumerate(samples):
                    record=make_trace(sample,j,i+variant*37)
                    if runid=="lt-persistent-s42" and step==100 and record["runtime_status"]!="ok":
                        record=make_trace(sample,j,i+variant*37+1)
                    records.append(record)
                    store.log_trajectory(evid,record)
                store.finish_evaluation(evid)
                if runid=="lt-persistent-s42" and step==100:
                    for repeat in (2,3):
                        revid=cpid+f"-eval0{repeat}"
                        store.start_evaluation({"id":revid,"checkpoint_id":cpid,"panel_id":PANEL_ID,"protocol":deepcopy(PROTOCOL_V2),
                            "created_at":f"2026-10-02T10:{30+repeat*5}:00+00:00","notes":"重复检查演示：02 仅耗时不同；03 有一条 query 不同"})
                        for i,record in enumerate(records):
                            copied=deepcopy(record)
                            for item in copied["steps"]: item["latency_ms"]+=repeat*7
                            if repeat==3 and i==0:
                                copied["steps"][1]["tool_call"]["arguments"]["query"] += " biography"
                                copied["metadata"]["notice"]="Intentionally different synthetic query for reproducibility inspection"
                            store.log_trajectory(revid,copied)
                        store.finish_evaluation(revid)
        store.add_reference("en-e01","lt-text-s42-step0-eval01","引用既有同协议初始模型记录，不复制曲线、轨迹或成绩；比较前仍核对底座、协议与预算。")
        store.note_experiment("lt-e02",{"kind":"observation","note":"演示记录：同一批固定题中同时存在错→对和对→错。此处不自动生成研究结论。", "evidence":[{"type":"evaluation","id":"lt-persistent-s42-step100-eval01"}]})
    return {"existing":False, **store.health()}
