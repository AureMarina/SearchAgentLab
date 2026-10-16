"""Generate explicit synthetic fixtures, NEVER benchmark results or GT."""
from pathlib import Path
import json


def records():
    return [
      {
        'id':'synthetic-001','dataset':'SYNTHETIC · UI TEST ONLY','demo':True,
        'question':'[合成测试] Where does the fictional botanist Ada Vale work?',
        'gold_answers':['Copper Harbor'],'scores':{'em':0},'reward':0.0,
        'raw_final_output':'<answer>Copper Harbor</answer>','parsed_answer':'Silver Bay',
        'runtime_status':'error','termination_reason':'tool_error',
        'steps':[
          {'index':0,'kind':'model','title':'准备检索','input':[{'role':'user','content':'Find the workplace of fictional Ada Vale.'}], 'output':'I will search for the fictional botanist.'},
          {'index':1,'kind':'tool','title':'检索结果经过截断','tool_call':{'name':'search','arguments':{'query':'Ada Vale workplace'}},'tool_response':{'documents':[{'title':'Fictional field notes','text':'Ada Vale works at Copper Harbor. hidden_signal_8472 is a synthetic test keyword.'},{'title':'Other notes','text':'A distractor describes Silver Bay.'}]},'model_visible':'Fictional field notes: Ada Vale is a botanist. [remaining text removed by fixture renderer]'},
          {'index':2,'kind':'model','title':'再次搜索','input':[{'role':'tool','content':'Fictional field notes: Ada Vale is a botanist.'}],'output':'The relevant workplace is not in the visible excerpt. Search again.'},
          {'index':3,'kind':'tool','title':'相同查询遇到服务错误','tool_call':{'name':'search','arguments':{'query':'Ada Vale workplace'}},'tool_response':{'error':'synthetic HTTP 503'},'model_visible':'Search failed: synthetic HTTP 503','status':'error'},
          {'index':4,'kind':'tool','title':'调用参数未通过 JSON 解析','tool_call':{'name':'search','arguments':'{query: Ada Vale}'},'error':'synthetic invalid arguments','tool_response':[],'model_visible':''},
          {'index':5,'kind':'final','title':'结束输出','output':'<answer>Copper Harbor</answer>'}
        ],
        'custom_metadata':{'a/b':{'tilde~key':'special_pointer_953'},'nested':{'large_array':[{'n':i,'value':'array_tail_317' if i==317 else f'fixture-{i}'} for i in range(340)]},'untrusted_markup':'<img src=x onerror=alert(1)> <script>throw Error("not executed")</script>'}
      },
      {'id':'synthetic-rllm-002','demo':True,'task':{'question':'[合成测试] Solver and verifier roles — not a proven RL rollout group'},'is_correct':False,'trajectories':[
        {'uid':'solver','reward':0,'steps':[{'observation':{'prompt':'Synthetic task'},'thought':'This is explicitly saved thought text.','action':{'type':'search','query':'fictional notes'},'model_response':'Need more evidence.','reward':0,'done':False},{'model_response':'Silver Bay','reward':0,'done':True}]},
        {'uid':'verifier','reward':0,'steps':[{'input':{'candidate':'Silver Bay'},'output':'No complete evidence was provided.','reward':0,'done':True}]}]},
      {'id':'synthetic-messages-003','demo':True,'question':'[合成测试] OpenAI-style message log','messages':[
        {'role':'user','content':'Find fictional place.'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'call-1','type':'function','function':{'name':'search','arguments':'{"query":"fictional place"}'}}]},
        {'role':'tool','tool_call_id':'call-1','content':'{"title":"Synthetic note","text":"Copper Harbor"}'},
        {'role':'assistant','content':'Copper Harbor'}]},
      {'id':'synthetic-scoring-004','demo':True,'question':'[合成测试] Answer-parser/scorer audit','answers':['The Copper Harbor'],'parsed_answer':'Copper Harbor','raw_final_output':'Copper Harbor','scores':{'em':0}},
      {'id':'synthetic-gt-005','demo':True,'question':'[合成题单] Reference only, not an agent execution','answers':['Copper Harbor'],'metadata':{'supporting_facts':['Synthetic notes']}}
    ]

if __name__=='__main__':
    rows=records()
    target=Path(__file__).with_name('inspection_demo.jsonl')
    target.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows[:2])+'\n\n'+'\n'.join(json.dumps(r,ensure_ascii=False) for r in rows[2:])+'\n{"broken":\n',encoding='utf-8')
    print(target)
