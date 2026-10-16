"""Training-stage metadata is separate from methods and evaluation purpose."""
from __future__ import annotations
from ..core import ValidationError

STAGES = {"sft", "rl", "mixed", "unknown"}

def validate_stage(value):
    if not isinstance(value,str) or value not in STAGES:
        raise ValidationError("training_stage must be sft/rl/mixed/unknown (OPD/Skill/Entropy are methods, not stages)")
    return value

def stage_of(run, batch_stages=()):
    config=run.get("config") if isinstance(run.get("config"),dict) else {}
    declarations=[]
    for source,value in [("run.training_stage",run.get("training_stage")),("run.config.training_stage",config.get("training_stage"))]:
        if isinstance(value,str) and value in STAGES and value!="unknown": declarations.append({"source":source,"value":value})
    for value in sorted(set(x for x in batch_stages if isinstance(x,str))):
        if value in STAGES and value!="unknown":declarations.append({"source":"batch.training_stage","value":value})
    values={d["value"] for d in declarations}
    stage=next(iter(values)) if len(values)==1 else "mixed" if values else "unknown"
    return {"value":stage,"label":{"sft":"SFT","rl":"RL","mixed":"阶段混合 / 声明不一致","unknown":"阶段未登记"}[stage],
            "declarations":declarations,"source":"explicit" if declarations else "unrecorded",
            "dataset_version":run.get("training_dataset_version") or config.get("training_dataset_version"),
            "notice":"只按 run / batch 的明确字段展示；算法名、模型名和曲线不用于推断阶段。"}


def validate_sft_sample(sample):
    """Validate recorded full-sequence supervision; never generate masks/labels."""
    for key in ("input_ids","token_ids","labels"):
        if key not in sample:continue
        values=sample[key]
        if not isinstance(values,list) or any(type(v) is not int or (v<0 and not(key=="labels" and v==-100)) for v in values):
            raise ValidationError(key+" must be a recorded integer array; labels may contain -100")
    if "loss_mask" in sample and (not isinstance(sample["loss_mask"],list) or any(type(v) is not int or v not in (0,1) for v in sample["loss_mask"])):
        raise ValidationError("SFT loss_mask must be a recorded 0/1 integer array")
    # Full sequence vs response-only domains must not be guessed. Length checking
    # is strict only for this explicit SFT format with full input_ids.
    if "input_ids" in sample:
        for key in ("labels","loss_mask"):
            if key in sample and len(sample[key])!=len(sample["input_ids"]):
                raise ValidationError("SFT "+key+" must align with input_ids; export full-sequence arrays")
