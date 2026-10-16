"""Evaluate symptom detection vs independent labels, NOT root-cause accuracy."""
from ..inspection.diagnostics import diagnose,RULES,RULESET_VERSION
from ..inspection.repository import digest
from ..core import ValidationError

def assess(manifest):
    if manifest.get('ruleset')!=RULESET_VERSION:raise ValidationError('Benchmark must pin the exact ruleset')
    entries=manifest.get('cases',[])
    if not entries:raise ValidationError('No independently labelled benchmark cases')
    counts={k:0 for k in ('tp','fp','tn','fn','abstain_positive','abstain_negative','unlabelled')};items=[];seen=set()
    for entry in entries:
        id=entry.get('id');rule=entry.get('rule');label=entry.get('label')
        if not isinstance(id,str) or not id or id in seen:raise ValidationError('Unique benchmark IDs required')
        seen.add(id)
        if rule not in RULES:raise ValidationError('Unknown diagnostic rule')
        if label not in ('positive','negative','unknown'):raise ValidationError('Invalid independent label')
        if entry.get('label_source') not in ('independent_human','injected_fault'):raise ValidationError('Use independent human or injected-fault labels; system confirmations are not a gold set')
        if 'raw' not in entry:raise ValidationError('raw evidence missing')
        rep=diagnose(entry['raw'],parse_error=entry.get('parse_error'));find=[f for f in rep['findings'] if f['rule']==rule]
        gaps=any(f['rule'] in ('no_steps','reference_only','json_invalid') for f in rep['findings'])
        pred='positive' if find and any(x['basis']!='insufficient' for x in find) else 'abstain' if gaps or find else 'negative'
        if label=='unknown':cell='unlabelled'
        elif pred=='abstain':cell='abstain_'+label
        else:cell={'positive':{'positive':'tp','negative':'fn'},'negative':{'positive':'fp','negative':'tn'}}[label][pred]
        counts[cell]+=1;items.append({'id':id,'rule':rule,'label':label,'prediction':pred,'cell':cell,'raw_hash':digest(entry['raw'])})
    tp,fp,tn,fn=(counts[k] for k in ('tp','fp','tn','fn'));decided=tp+fp+tn+fn;labelled=len(entries)-counts['unlabelled']
    return {'kind':'diagnostic_rule_benchmark','ruleset':RULESET_VERSION,'n':len(entries),'counts':counts,'items':items,
            'precision':tp/(tp+fp) if tp+fp else None,'recall_excluding_abstentions':tp/(tp+fn) if tp+fn else None,
            'positive_coverage_recall':tp/(tp+fn+counts['abstain_positive']) if tp+fn+counts['abstain_positive'] else None,
            'decision_coverage':decided/labelled if labelled else None,
            'limitations':['Measures symptom/rule detection, not causal root-cause accuracy.',
              'Injected-fault fixtures are mechanism tests, not evidence of production distribution accuracy.']}
