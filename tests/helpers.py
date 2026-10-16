from copy import deepcopy
from searchagent_lab.demo import PROTOCOL


def seed_minimal(store, n=2, datasets=None):
    datasets = datasets or ["HotpotQA"]
    store.register_run({"id":"run", "name":"CPU test", "demo":True})
    samples = [{"id":f"q{i}", "dataset":ds, "question":f"Question {ds} {i}?", "answers":[f"gold-{i}"]} for ds in datasets for i in range(n)]
    store.register_panel({"id":"panel", "name":"Fixed test panel", "version":"v1", "demo":True, "samples":samples})
    for idx in (0,1):
        store.register_checkpoint({"id":f"cp{idx}","run_id":"run","name":f"Checkpoint {idx}","step":idx*100})
        store.start_evaluation({"id":f"eval{idx}","checkpoint_id":f"cp{idx}","panel_id":"panel","protocol":deepcopy(PROTOCOL)})
    return samples


def trajectory(i=0, em=1, dataset="HotpotQA", **kwargs):
    result={"dataset":dataset,"sample_id":f"q{i}","scores":{"em":em},"raw_final_output":f"gold-{i}","parsed_answer":f"gold-{i}","runtime_status":"ok","termination_reason":"final_answer",
      "steps":[{"index":0,"kind":"model","input":[{"role":"user","content":f"Question {dataset} {i}?"}],"output":"search..."},
               {"index":1,"kind":"tool","tool_call":{"name":"search","arguments":{"query":"explicit query"}},"tool_response":{"text":"untruncated return"},"model_visible":"truncated view"}]}
    result.update(kwargs)
    return result
