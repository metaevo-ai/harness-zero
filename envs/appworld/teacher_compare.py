#!/usr/bin/env python3
"""Freeze and run an AppWorld bare/student-bank/teacher-bank comparison."""
import argparse
import copy
import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'src'))
from harness_zero.student_harness import hash_student_harness_bank, load_student_harness

AGENT = 'envs.appworld.agent:AppWorldMinisweAgent'
ENVIRONMENT = 'envs.appworld.harbor_environment:AppWorldDockerEnvironment'
ARMS = ('bare','student_harness','teacher_harness')


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def prepare(args):
    if not args.student_model.startswith('azure:') or not args.student_model.split(':',1)[1]:
        raise ValueError('This comparison requires an explicit Azure student deployment')
    if not args.teacher_model or min(args.max_turns,args.max_replacements,args.concurrency)<=0:
        raise ValueError('Model and positive turn/replacement/concurrency budgets are required')
    campaign=args.campaign.resolve();campaign.mkdir(parents=True,exist_ok=True)
    manifest=campaign/'inputs.json'
    if manifest.exists():raise FileExistsError('Campaign already frozen; use run with its explicit model parameters')
    tasks=args.tasks_file.read_text().split()
    assert tasks and len(tasks)==len(set(tasks))
    dataset=(REPO/'data/appworld-harbor/tasks').resolve()
    assert all((dataset/t/'task.toml').is_file() for t in tasks)
    source=campaign/'source'
    for relative in ['src/harness_zero','src/deepagents_harbor','envs/appworld','harness_bank/appworld','harness_bank/empty']:
        shutil.copytree(REPO/relative,source/relative,ignore=shutil.ignore_patterns('__pycache__'))
    (source/'harness_bank/__init__.py').write_text('')
    bank=campaign/'student_bank'
    shutil.copytree(REPO/'harness_bank/appworld_student',bank,ignore=shutil.ignore_patterns('__pycache__'))
    assert not load_student_harness(bank).tool_factories
    plan={'tasks':tasks,'dataset':str(dataset),'student_model':args.student_model,
          'student_reasoning':args.student_reasoning,'teacher_provider':args.teacher_provider,
          'teacher_model':args.teacher_model,'teacher_reasoning':args.teacher_reasoning,
          'max_turns':args.max_turns,'max_replacements':args.max_replacements,
          'concurrency':args.concurrency,'attempts':1,'source':str(source),'student_bank':str(bank),
          'teacher_components':str(source/'harness_bank/appworld'),
          'images':{role:subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',f'ahd-appworld-{role}:v2'],text=True).strip()
                    for role in ['client','world','verifier']},
          'task_hashes':{t:hashes(dataset/t) for t in tasks},'source_hashes':hashes(source),
          'student_bank_sha256':hash_student_harness_bank(bank),
          'packages':{p:importlib.metadata.version(p) for p in ['harbor','langchain','langgraph','langchain-openai','openai']}}
    manifest.write_text(json.dumps(plan,indent=2)+'\n')
    (campaign/'tasks.txt').write_text('\n'.join(tasks)+'\n')
    print(f'Frozen {len(tasks)} tasks and all runtime/profile code at {source}',flush=True)


def verify(campaign):
    p=json.loads((campaign/'inputs.json').read_text())
    assert hashes(Path(p['source']))==p['source_hashes'],'Frozen source changed'
    assert hash_student_harness_bank(Path(p['student_bank']))==p['student_bank_sha256']
    for role,expected in p['images'].items():
        assert subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',f'ahd-appworld-{role}:v2'],text=True).strip()==expected
    assert all(hashes(Path(p['dataset'])/t)==value for t,value in p['task_hashes'].items())
    assert all(importlib.metadata.version(name)==version for name,version in p['packages'].items())
    return p


def config_for(plan,tasks,job):
    common={'name':AGENT,'model_name':plan['student_model'],
            'kwargs':{'model_kwargs':{'reasoning_effort':plan['student_reasoning']},
                      'student_reasoning_prefill':'none','student_max_turns':plan['max_turns'],
                      'teacher_provider':'passthrough','teacher_model':'none','teacher_reasoning_effort':'none',
                      'max_replacements_per_trial':0,'teacher_middleware_factory':None,
                      'student_harness_dir':None,'components_dir':str(Path(plan['source'])/'harness_bank/empty')},
            'env':{'HARNESS_ZERO_GATEWAY_URL':'${AZURE_OPENAI_ENDPOINT}','HARNESS_ZERO_GATEWAY_TOKEN':'${AZURE_OPENAI_API_KEY}',
                   'HARNESS_ZERO_GATEWAY_THINKING':'enabled'}}
    agents=[]
    for arm in ARMS:
        agent=copy.deepcopy(common)
        if arm=='student_harness':agent['kwargs']['student_harness_dir']=plan['student_bank']
        if arm=='teacher_harness':
            agent['kwargs'].update(teacher_provider=plan['teacher_provider'],teacher_model=plan['teacher_model'],
                teacher_reasoning_effort=plan['teacher_reasoning'],max_replacements_per_trial=plan['max_replacements'],
                components_dir=plan['teacher_components'],teacher_middleware_factory='harness_bank.appworld.profile:build_teacher_middlewares',
                teacher_prompt_path=str(Path(plan['source'])/'src/harness_zero/prompts/harness_teacher.md'))
        agents.append(agent)
    return {'job_name':job,'jobs_dir':str(REPO/'runs'),'n_attempts':1,'n_concurrent_trials':plan['concurrency'],
            'retry':{'max_retries':0},'agents':agents,
            'environment':{'type':'docker','import_path':ENVIRONMENT},
            'datasets':[{'path':plan['dataset'],'task_names':tasks}]}


def run(args):
    campaign=args.campaign.resolve();p=verify(campaign)
    for key in ['student_model','student_reasoning','teacher_provider','teacher_model','teacher_reasoning','max_turns','max_replacements','concurrency']:
        assert getattr(args,key)==p[key],f'Explicit {key} differs from frozen plan'
    tasks=args.tasks_file.read_text().split() if args.tasks_file else p['tasks']
    assert tasks and len(tasks)==len(set(tasks)) and set(tasks)<=set(p['tasks'])
    stage=campaign/args.stage
    if stage.exists():raise FileExistsError('Stage exists; never reuse a comparison result')
    stage.mkdir()
    job=datetime.datetime.now().strftime('%Y%m%d-')+'appworld-teacher-'+campaign.name+'-'+args.stage
    assert not (REPO/'runs'/job).exists()
    config=config_for(p,tasks,job)
    (stage/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    source=Path(p['source'])
    env={**os.environ,'PYTHONPATH':str(source)+os.pathsep+str(source/'src'),
         'BUILDX_BUILDER':'default','DOCKER_BUILDKIT':'0','COMPOSE_BAKE':'false',
         'HARNESS_ZERO_TEACHER_MAX_CONCURRENCY':str(p['concurrency'])}
    probe="import harness_zero.harness,envs.appworld.agent,harness_bank.appworld.profile; from pathlib import Path; root=Path.cwd(); assert all(Path(m.__file__).is_relative_to(root) for m in [harness_zero.harness,envs.appworld.agent,harness_bank.appworld.profile]); print('Frozen runtime imports verified')"
    subprocess.run([str(REPO/'.venv/bin/python'),'-c',probe],cwd=source,env=env,check=True)
    print(f"Job {job}: {len(tasks)} tasks x 3 arms, student {p['student_model']}/{p['student_reasoning']}, teacher {p['teacher_provider']}:{p['teacher_model']}/{p['teacher_reasoning']}, max replacements {p['max_replacements']}, total concurrency {p['concurrency']}",flush=True)
    subprocess.run([str(REPO/'.venv/bin/harbor'),'run','--config',str(stage/'config.json'),'--yes'],cwd=source,env=env,check=True)
    collect(campaign,args.stage)


def arm_of(kwargs):
    if kwargs['teacher_provider']!='passthrough':return 'teacher_harness'
    return 'student_harness' if kwargs.get('student_harness_dir') else 'bare'


def privacy_issues(response):
    text=json.dumps(response,ensure_ascii=False)
    patterns=[r'/components(?:/|\b)',r'ahd-teacher-',r'/opt/ahd/harness',r'\baw\.',
              r'ground_truth',r'/solution/',r'/logs/verifier',r'submit_review',
              r'(?i)\b(?:the teacher|teacher instructions|reviewed candidate|triggered middleware guidance)\b']
    return [pattern for pattern in patterns if re.search(pattern,text)]


def collect(campaign,stage_name):
    p=verify(campaign);stage=campaign/stage_name;expected=json.loads((stage/'config.json').read_text())
    job=REPO/'runs'/expected['job_name']
    assert json.loads((job/'result.json').read_text()).get('finished_at')
    actual=json.loads((job/'config.json').read_text())
    assert actual['n_concurrent_trials']==p['concurrency'] and actual.get('n_attempts',1)==1
    assert actual['environment']['import_path']==ENVIRONMENT
    pairs={};issues=[];common={};systems={}
    for path in sorted(job.glob('*/result.json')):
        result=json.loads(path.read_text());agent=result['config']['agent'];kw=agent['kwargs'];arm=arm_of(kw);task=result['task_name']
        assert arm not in pairs.setdefault(task,{})
        assert agent['name']==AGENT and agent['model_name']==p['student_model']
        assert kw['model_kwargs']=={'reasoning_effort':p['student_reasoning']} and kw['student_max_turns']==p['max_turns']
        expected_agent=next(a for a in expected['agents'] if arm_of(a['kwargs'])==arm)
        assert kw==expected_agent['kwargs'],(task,arm,'agent kwargs changed')
        error=(result.get('exception_info') or {}).get('exception_type')
        reward=(result.get('verifier_result')or{}).get('rewards',{}).get('reward')
        category='infra' if (error and error!='StudentTurnLimitError') or (error is None and reward is None) else 'pass' if reward==1 else 'fail'
        directory=path.parent/'agent';reviews_path=directory/'teacher/trial/trajectory.jsonl'
        reviews=[json.loads(l) for l in reviews_path.read_text().splitlines()] if reviews_path.exists() else []
        if category!='infra':assert reviews and len(reviews)<=p['max_turns']
        replacements=[r for r in reviews if r['review']['decision']=='REPLACE']
        assert len(replacements)<= (p['max_replacements'] if arm=='teacher_harness' else 0)
        for review in replacements:
            assert review['accepted']==review['review']['replacement']
            found=privacy_issues(review['accepted'])
            if found:issues.append({'task':task,'arm':arm,'candidate_id':review['candidate_id'],'patterns':found})
        calls_path=directory/'teacher/trial/teacher_llm_calls.jsonl'
        calls=[json.loads(l) for l in calls_path.read_text().splitlines()] if calls_path.exists() else []
        if arm!='teacher_harness':assert not calls
        elif category!='infra':assert calls,'Teacher did not run'
        teacher_models=set();teacher_tools=set();teacher_tokens={'input':0,'output':0}
        for call in calls:
            response=call.get('response') or {}
            metadata=response.get('response_metadata') or {}
            model=metadata.get('model') or metadata.get('model_name')
            if model:teacher_models.add(model)
            teacher_tools.update(t['name'] for t in response.get('tool_calls',[]))
            usage=response.get('usage_metadata') or {}
            teacher_tokens['input']+=usage.get('input_tokens',0)
            teacher_tokens['output']+=usage.get('output_tokens',0)
        if arm=='teacher_harness' and category!='infra':
            assert teacher_models=={p['teacher_model']},teacher_models
            assert teacher_tools <= {'ls','read_file','glob','grep','submit_review'}
            assert 'submit_review' in teacher_tools
        request_path=directory/'student_model_requests.jsonl'
        if request_path.exists():
            with request_path.open() as stream: request=json.loads(next(stream))
            assert request['model']==p['student_model'].split(':',1)[1]
            assert request['reasoning_effort']==p['student_reasoning']
            assert len(request['messages'])==24 and request['messages'][23]['content'].startswith('[APPWORLD_CURRENT_TASK]')
            assert [t['function']['name'] for t in request['tools']]==['execute']
            common.setdefault(task,{})[arm]=request['messages'][1:24]
            systems.setdefault(task,{})[arm]=request['messages'][0]
        bank=directory/'student_harness_bank.txt'
        if arm=='student_harness' and category!='infra':assert bank.read_text().strip()==p['student_bank_sha256']
        if arm!='student_harness':assert not bank.exists()
        timing=result.get('agent_execution') or {}
        seconds=None
        if timing.get('started_at') and timing.get('finished_at'):
            seconds=(datetime.datetime.fromisoformat(timing['finished_at'])-
                     datetime.datetime.fromisoformat(timing['started_at'])).total_seconds()
        student_calls=directory/'llm_calls.jsonl'
        student_tokens={'input':0,'output':0}
        if student_calls.exists():
            for line in student_calls.read_text().splitlines():
                usage=(json.loads(line).get('response')or{}).get('usage_metadata')or{}
                student_tokens['input']+=usage.get('input_tokens',0)
                student_tokens['output']+=usage.get('output_tokens',0)
        pairs[task][arm]={'reward':reward,'category':category,'exception':error,'turns':len(reviews),
                         'replacements':len(replacements),'teacher_calls':len(calls),
                         'teacher_reviewed_turns':len({c['candidate_id'] for c in calls}),
                         'replacement_budget_reached':arm=='teacher_harness' and len(replacements)==p['max_replacements'],
                         'agent_seconds':seconds,
                         'student_tokens':student_tokens,
                         'teacher_tokens':teacher_tokens,'teacher_models':sorted(teacher_models),
                         'teacher_tools':sorted(teacher_tools),'trial':str(path.parent)}
    assert set(pairs)==set(expected['datasets'][0]['task_names'])
    for task,arms in pairs.items():
        assert set(arms)==set(ARMS),task
        contexts=list(common.get(task,{}).values())
        assert not contexts or all(x==contexts[0] for x in contexts),task
        system=systems.get(task,{})
        if {'bare','teacher_harness'}<=system.keys():assert system['bare']==system['teacher_harness'],task
    counts={a:{c:sum(t[a]['category']==c for t in pairs.values()) for c in ['pass','fail','infra']} for a in ARMS}
    comparisons={a:{'improved':[t for t,v in pairs.items() if v[a]['category']=='pass' and v['bare']['category']=='fail'],
                    'regressed':[t for t,v in pairs.items() if v[a]['category']=='fail' and v['bare']['category']=='pass']}
                 for a in ARMS if a!='bare'}
    complete={t:arms for t,arms in pairs.items()
              if all(row['category']!='infra' and row['reward'] in (0,1) for row in arms.values())}
    paired={}
    for baseline,assisted in [('bare','student_harness'),('bare','teacher_harness'),('student_harness','teacher_harness')]:
        paired[f'{assisted}_vs_{baseline}']={
            'tasks':len(complete),
            'baseline_pass':sum(v[baseline]['reward']==1 for v in complete.values()),
            'assisted_pass':sum(v[assisted]['reward']==1 for v in complete.values()),
            'improved':[t for t,v in complete.items() if v[baseline]['reward']==0 and v[assisted]['reward']==1],
            'regressed':[t for t,v in complete.items() if v[baseline]['reward']==1 and v[assisted]['reward']==0]}
    report={'job':str(job),'counts':counts,'vs_bare':comparisons,'privacy_issues':issues,
            'valid':not issues and all(c['infra']==0 for c in counts.values()),
            'common_complete_tasks':sorted(complete),'excluded_incomplete_tasks':sorted(set(pairs)-set(complete)),
            'common_paired_comparisons':paired,'pairs':pairs}
    (stage/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='pairs'},indent=2),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    for name in ['prepare','run','collect']:
        p=sub.add_parser(name);p.add_argument('--campaign',type=Path,required=True)
        if name=='collect':p.add_argument('--stage',required=True);continue
        p.add_argument('--student-model',required=True);p.add_argument('--student-reasoning',required=True)
        p.add_argument('--teacher-provider',required=True,choices=['azure_openai'])
        p.add_argument('--teacher-model',required=True);p.add_argument('--teacher-reasoning',required=True)
        p.add_argument('--max-turns',type=int,required=True);p.add_argument('--max-replacements',type=int,required=True)
        p.add_argument('--concurrency',type=int,required=True)
        p.add_argument('--tasks-file',type=Path,required=name=='prepare')
        if name=='run':p.add_argument('--stage',required=True)
    args=parser.parse_args()
    if args.command=='prepare':prepare(args)
    elif args.command=='run':run(args)
    else:collect(args.campaign.resolve(),args.stage)


if __name__=='__main__':main()
