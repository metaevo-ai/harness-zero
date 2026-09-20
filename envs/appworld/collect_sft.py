#!/usr/bin/env python3
"""Collect fixed-budget AppWorld teacher-assisted training trajectories."""
import argparse
import copy
import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO));sys.path.insert(0,str(REPO/'src'))
from envs.appworld.teacher_compare import hashes,config_for


def verify(directory):
    plan=json.loads((directory/'inputs.json').read_text())
    assert hashes(Path(plan['source']))==plan['source_hashes'], 'Frozen source changed'
    assert all(hashes(Path(plan['dataset'])/t)==v for t,v in plan['task_hashes'].items())
    for role,expected in plan['images'].items():
        assert subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',f'ahd-appworld-{role}:v2'],text=True).strip()==expected
    assert all(importlib.metadata.version(p)==v for p,v in plan['packages'].items())
    assert hashlib.sha256(Path(plan['teacher_prompt_path']).read_bytes()).hexdigest()==plan['teacher_prompt_sha256']
    return plan


def prepare(args):
    directory=args.output.resolve()
    if directory.exists():raise FileExistsError('Use a new collection directory')
    train=(REPO/'data/appworld-harbor/split_train147.txt').read_text().split()
    test=(REPO/'data/appworld-harbor/split_test168.txt').read_text().split()
    assert len(train)==147 and len(set(train))==147 and not set(train)&set(test)
    previous=json.loads((args.reference/'inputs.json').read_text())
    assert hashes(Path(previous['source']))==previous['source_hashes']
    directory.mkdir(parents=True)
    source=directory/'source'
    shutil.copytree(previous['source'],source,ignore=shutil.ignore_patterns('__pycache__'))
    target=source/'src/harness_zero/prompts/harness_teacher_sft.md'
    shutil.copy2(args.teacher_prompt,target)
    # The requested SFT prompt governs intervention size; the domain supplement
    # remains the validated teacher bank, not a new task-specific policy.
    tasks=REPO/'data/appworld-harbor/tasks'
    plan={**previous,'tasks':train,'test_tasks':test,'dataset':str(tasks.resolve()),'source':str(source),
          'teacher_components':str(source/'harness_bank/appworld'),'student_bank':None,
          'student_model':args.student_model,'student_reasoning':args.student_reasoning,
          'teacher_provider':args.teacher_provider,'teacher_model':args.teacher_model,
          'teacher_reasoning':args.teacher_reasoning,'max_turns':args.max_turns,'concurrency':args.concurrency,
          'budgets':args.budgets,'attempts':1,'requested_teacher_prompt':str(args.teacher_prompt.resolve()),
          'teacher_prompt_path':str(target),'teacher_prompt_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
          'task_hashes':{t:hashes(tasks/t) for t in train},'source_hashes':hashes(source)}
    plan.pop('student_bank_sha256',None)
    assert args.budgets==[5,3,1] and args.teacher_provider=='azure_openai'
    assert args.student_model.startswith('azure:') and args.teacher_model and args.max_turns>0 and args.concurrency>0
    name=datetime.datetime.now().strftime('%Y%m%d-')+'appworld-sft-'+directory.name
    base=config_for(plan,train,name)
    template=base['agents'][2]
    template['kwargs']['teacher_prompt_path']=str(target)
    agents=[]
    for budget in args.budgets:
        agent=copy.deepcopy(template);agent['kwargs']['max_replacements_per_trial']=budget
        assert agent['kwargs']['student_harness_dir'] is None
        agents.append(agent)
    base['agents']=agents
    assert base['n_concurrent_trials']==args.concurrency and base['n_attempts']==1
    (directory/'inputs.json').write_text(json.dumps(plan,indent=2)+'\n')
    (directory/'config.json').write_text(json.dumps(base,indent=2)+'\n')
    (directory/'tasks.txt').write_text('\n'.join(train)+'\n')
    verify(directory)
    print(f'Prepared147 tasks x budgets{args.budgets},441 trials; prompt SHA256 {plan["teacher_prompt_sha256"]}',flush=True)


def run(args):
    directory=args.output.resolve();plan=verify(directory)
    for key in ['student_model','student_reasoning','teacher_provider','teacher_model','teacher_reasoning','max_turns','concurrency','budgets']:
        assert getattr(args,key)==plan[key],key
    assert hashlib.sha256(args.teacher_prompt.read_bytes()).hexdigest()==plan['teacher_prompt_sha256']
    config=json.loads((directory/'config.json').read_text())
    if (REPO/'runs'/config['job_name']).exists():raise FileExistsError('Refuse reuse of an existing collection job')
    source=Path(plan['source'])
    env={**os.environ,'PYTHONPATH':str(source)+os.pathsep+str(source/'src'),
         'BUILDX_BUILDER':'default','DOCKER_BUILDKIT':'0','COMPOSE_BAKE':'false',
         'HARNESS_ZERO_TEACHER_MAX_CONCURRENCY':str(plan['concurrency'])}
    probe="import harness_zero.harness,envs.appworld.agent,harness_bank.appworld.profile; from pathlib import Path; assert all(Path(m.__file__).is_relative_to(Path.cwd()) for m in [harness_zero.harness,envs.appworld.agent,harness_bank.appworld.profile]); print('Frozen imports verified')"
    subprocess.run([str(REPO/'.venv/bin/python'),'-c',probe],cwd=source,env=env,check=True)
    print(f"Starting {config['job_name']}; student={plan['student_model']}/{plan['student_reasoning']}, teacher={plan['teacher_provider']}:{plan['teacher_model']}/{plan['teacher_reasoning']}, prompt={plan['teacher_prompt_path']}, budgets={plan['budgets']}, total concurrency={plan['concurrency']}",flush=True)
    subprocess.run([str(REPO/'.venv/bin/harbor'),'run','--config',str(directory/'config.json'),'--yes'],cwd=source,env=env,check=True)
    collect(directory)


def collect(directory):
    plan=verify(directory);config=json.loads((directory/'config.json').read_text());job=REPO/'runs'/config['job_name']
    rows=[];seen=set()
    for path in sorted(job.glob('*/result.json')):
        r=json.loads(path.read_text());k=r['config']['agent']['kwargs'];budget=k['max_replacements_per_trial'];key=(r['task_name'],budget)
        assert key not in seen and budget in plan['budgets'];seen.add(key)
        assert r['task_name'] in plan['tasks'] and not k.get('student_harness_dir')
        assert k['teacher_prompt_path']==plan['teacher_prompt_path'] and k['teacher_model']==plan['teacher_model']
        assert r['config']['agent']['model_name']==plan['student_model']
        error=(r.get('exception_info')or{}).get('exception_type');reward=(r.get('verifier_result')or{}).get('rewards',{}).get('reward')
        rows.append({'task':r['task_name'],'budget':budget,'reward':reward,'exception':error,'trial':str(path.parent)})
    finished=bool(json.loads((job/'result.json').read_text()).get('finished_at'))
    if finished:assert len(rows)==len(plan['tasks'])*len(plan['budgets'])
    report={'job':str(job),'finished':finished,'counts':{str(b):{'completed':sum(x['budget']==b for x in rows),
        'pass':sum(x['budget']==b and x['reward']==1 and not x['exception'] for x in rows),
        'scored_fail':sum(x['budget']==b and x['reward']==0 and not x['exception'] for x in rows),
        'exception':sum(x['budget']==b and bool(x['exception']) for x in rows)} for b in plan['budgets']},'trials':rows}
    (directory/'rollout-results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['counts']),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='action',required=True)
    for action in ['prepare','run','collect']:
        p=subs.add_parser(action);p.add_argument('--output',type=Path,required=True)
        if action=='collect':continue
        p.add_argument('--teacher-prompt',type=Path,required=True)
        p.add_argument('--student-model',required=True);p.add_argument('--student-reasoning',required=True)
        p.add_argument('--teacher-provider',required=True);p.add_argument('--teacher-model',required=True);p.add_argument('--teacher-reasoning',required=True)
        p.add_argument('--budgets',nargs='+',type=int,required=True);p.add_argument('--max-turns',type=int,required=True);p.add_argument('--concurrency',type=int,required=True)
        if action=='prepare':p.add_argument('--reference',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='prepare':prepare(args)
    elif args.action=='run':run(args)
    else:collect(args.output.resolve())


if __name__=='__main__':main()
