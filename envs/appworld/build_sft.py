#!/usr/bin/env python3
"""Audit complete successful AppWorld sessions, masking official demonstrations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'src'));sys.path.insert(0,str(REPO))
from harness_zero.chatfmt import lc_to_openai,create_qwen35_renderer,to_renderer_messages
from harness_zero.dataset_utils import conversation_messages,assert_student_only,reasoning_leaks_review
from harness_zero.review import AssistantResponse
from harness_zero.tools import execute_tool_schema
from harness_zero.train import prepare_datums
from deepagents_harbor.message_content import assistant_text
from langchain_core.messages import AIMessage

MARKER='[APPWORLD_CURRENT_TASK]'


def canonical(messages):
    result=[]
    for raw in messages:
        m=lc_to_openai(raw)
        if m is None:raise ValueError('Unsupported message role')
        for c in m.get('tool_calls',[]):
            c['function']['arguments']=json.dumps(json.loads(c['function']['arguments']),ensure_ascii=False,sort_keys=True)
        result.append(m)
    return result


def row_from_trial(trial,train_tasks,expected_prompt_hash=None):
    result=json.loads((trial/'result.json').read_text());task=result['task_name'];k=result['config']['agent']['kwargs']
    if task not in train_tasks:raise ValueError('outside_train_pool')
    if k.get('student_harness_dir') or k['teacher_provider']!='azure_openai':raise ValueError('not_bare_teacher_source')
    if k['teacher_model']!='gpt-5.6-sol':raise ValueError('wrong_teacher_model')
    if result.get('exception_info'):raise ValueError('trial_exception')
    if (result.get('verifier_result')or{}).get('rewards',{}).get('reward')!=1:raise ValueError('not_reward_one')
    prompt=Path(k['teacher_prompt_path'])
    if expected_prompt_hash and hashlib.sha256(prompt.read_bytes()).hexdigest()!=expected_prompt_hash:
        raise ValueError('wrong_teacher_prompt')
    agent=trial/'agent';event_path=agent/'teacher/trial/trajectory.jsonl'
    events=[json.loads(l) for l in event_path.read_text().splitlines() if l.strip()]
    if not events:raise ValueError('missing_events')
    messages=conversation_messages(events[-1]);assert_student_only(messages)
    task_positions=[i for i,m in enumerate(messages) if m['role']=='user' and isinstance(m['content'],str) and m['content'].startswith(MARKER)]
    if len(task_positions)!=1:raise ValueError('invalid_real_task_boundary')
    boundary=task_positions[0];offset=sum(m['role']=='assistant' for m in messages[:boundary])
    if offset!=10:raise ValueError('official_demo_count_mismatch')
    assistants=[m for m in messages if m['role']=='assistant']
    if len(assistants)!=offset+len(events):raise ValueError('assistant_event_count_mismatch')
    requests=[json.loads(l) for l in (agent/'student_model_requests.jsonl').read_text().splitlines() if l.strip()]
    # Additional API transport retries may log an identical request. Consume in
    # order; every event still needs an exact student-visible request match.
    request_index=0;masked=[];source_decisions=[]
    for i,event in enumerate(events):
        if event['system_message']!=events[0]['system_message']:raise ValueError('student_system_changed')
        expected_prefix=canonical(([event['system_message']] if event['system_message'] else [])+event['context'])
        if expected_prefix!=canonical(messages[:len(expected_prefix)]):raise ValueError('history_prefix_rewritten')
        found=False
        while request_index<len(requests):
            request=requests[request_index];request_index+=1
            if canonical(request['messages'])==expected_prefix:
                found=True;break
        if not found:raise ValueError('missing_exact_sdk_prefix')
        if request['model']!=result['config']['agent']['model_name'].split(':',1)[1]:raise ValueError('sdk_model_mismatch')
        if request['reasoning_effort']!=k['model_kwargs']['reasoning_effort']:raise ValueError('sdk_reasoning_mismatch')
        if [t['function']['name'] for t in request['tools']]!=['execute']:raise ValueError('extra_native_tools')
        actual=assistants[offset+i];accepted=AssistantResponse.model_validate(event['accepted'])
        if assistant_text(accepted.model_dump())!=assistant_text(actual):raise ValueError('accepted_content_changed')
        calls=actual.get('tool_calls')or[]
        if len(calls)>1 or any(c['function']['name']!='execute' for c in calls):raise ValueError('invalid_native_tool')
        if calls:
            args=json.loads(calls[0]['function']['arguments'])
            if set(args)!={'command'} or not isinstance(args['command'],str) or not args['command']:raise ValueError('invalid_execute_arguments')
            if accepted.tool_call is None or args['command']!=accepted.tool_call.command:raise ValueError('accepted_command_changed')
        elif accepted.tool_call is not None:raise ValueError('missing_accepted_tool_call')
        if not calls and not assistant_text(actual)[1].strip():raise ValueError('non_actionable_response')
        if event['review']['decision']=='REPLACE':
            if event['accepted']!=event['review']['replacement']:raise ValueError('replacement_changed')
            if reasoning_leaks_review(event['accepted']):masked.append(offset+i)
        elif event['review']['decision']=='PASS':
            if event['raw_original'].get('invalid_tool_calls') or len(event['raw_original'].get('tool_calls',[]))>1:raise ValueError('invalid_pass_candidate')
            original=AssistantResponse.from_message(AIMessage(**event['raw_original']))
            if original.model_dump()!=accepted.model_dump():raise ValueError('pass_changed_original_response')
        else:raise ValueError('unknown_review_decision')
        thinking,text=assistant_text(actual)
        if '<tool_call>' in thinking or '<function=' in thinking:masked.append(offset+i)
        if re.search(r'<\|im_start\|>|<\|im_end\|>|<tool_call>|<function=',text):raise ValueError('role_or_tool_markup_in_visible_text')
        action_text=text+'\n'+(accepted.tool_call.command if accepted.tool_call else '')
        if re.search(r'/components/|/opt/ahd/harness|\baw\.|ahd-teacher-|submit_review',action_text):raise ValueError('private_or_helper_dependency')
        source_decisions.append(event['review']['decision'])
    if assistants[-1].get('tool_calls'):raise ValueError('missing_final_visible_answer')
    pending=set();used=set()
    for m in messages:
        if m['role']=='assistant':
            if pending:raise ValueError('missing_tool_observation')
            pending={c['id'] for c in m.get('tool_calls',[])}
            if pending&used:raise ValueError('reused_tool_id')
            used|=pending
        elif m['role']=='tool':
            if m['tool_call_id'] not in pending:raise ValueError('orphan_tool_observation')
            pending.remove(m['tool_call_id'])
    if pending:raise ValueError('unobserved_final_action')
    row={'task_id':task,'trial':events[-1]['trial'],'turns':len(events),'replacements':source_decisions.count('REPLACE'),
         'kind':'session','messages':messages,'masked_assistant_turns':list(range(offset)),
         'masked_reasoning_turns':sorted(set(masked))}
    provenance={'task':task,'budget':k['max_replacements_per_trial'],'source_trial':str(trial.resolve()),
        'source_decisions':source_decisions,'teacher_prompt_sha256':hashlib.sha256(prompt.read_bytes()).hexdigest(),
        'teacher_model':k['teacher_model'],'student_model':result['config']['agent']['model_name'],
        'source_sha256':{str(p.relative_to(trial)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
          [trial/'result.json',event_path,agent/'student_model_requests.jsonl',agent/'appworld_initial_context.json']}}
    return row,provenance


def audit_row(row,renderer,max_length):
    from tinker_cookbook.renderers import TrainOnWhat
    from tinker_cookbook.supervised.data import conversation_to_datum
    datum_list,stats=prepare_datums(rows=[row],renderer=renderer,max_length=max_length)
    if stats['skipped_too_long']:raise ValueError('too_long')
    datum=datum_list[0];targets=datum.loss_fn_inputs['target_tokens'].tolist();weights=datum.loss_fn_inputs['weights'].tolist()
    assert datum.model_input.to_ints()[1:]==targets[:-1]
    messages=row['messages'];boundary=next(i for i,m in enumerate(messages) if m['role']=='user' and isinstance(m['content'],str) and m['content'].startswith(MARKER))
    tools=[{'type':'function','function':execute_tool_schema()}]
    prefix=renderer.build_generation_prompt(to_renderer_messages(renderer,messages[:boundary+1],tools)).to_ints()
    assert datum.model_input.to_ints()[:len(prefix)]==prefix
    unmasked={**row,'masked_reasoning_turns':[]}
    reference,_=prepare_datums(rows=[unmasked],renderer=renderer,max_length=max_length)
    before=reference[0].loss_fn_inputs['weights'].tolist()
    header=renderer.tokenizer.encode('<|im_start|>assistant\n',add_special_tokens=False)
    opens=renderer.tokenizer.encode('<think>',add_special_tokens=False)
    closes=renderer.tokenizer.encode('</think>',add_special_tokens=False)
    def positions(needle,start=0,end=None):
        end=len(targets) if end is None else end
        return [i for i in range(start,end-len(needle)+1) if targets[i:i+len(needle)]==needle]
    assistant_positions=positions(header);assistants=[m for m in messages if m['role']=='assistant']
    assert len(assistant_positions)==len(assistants)
    # A generation prompt already includes the real assistant's opening tokens.
    # Those belong to the actual response, not to the example. Check the role
    # boundary independently so the first real reasoning tokens stay trainable.
    real_start=assistant_positions[len(row['masked_assistant_turns'])]
    assert all(w==0 for w in weights[:real_start]), 'demonstration or prompt has nonzero loss'
    assert sum(weights[real_start:])>0
    expected=set()
    for turn in row['masked_reasoning_turns']:
        start=assistant_positions[turn]+len(header);stop=assistant_positions[turn+1] if turn+1<len(assistants) else len(targets)
        left=positions(opens,start,stop)[0]+len(opens);right=positions(closes,left,stop)[0]
        thinking,_=assistant_text(assistants[turn]);needle=renderer.tokenizer.encode(thinking.strip(),add_special_tokens=False)
        candidates=positions(needle,left,right);assert len(candidates)==1
        a=candidates[0];expected.update(range(a,a+len(needle)))
    actual={i for i,(a,b) in enumerate(zip(before,weights)) if a>0 and b==0}
    assert actual==expected and all(b==0 for a,b in zip(before,weights) if a==0)
    assert abs(sum(weights)-1)<1e-5
    return stats,{'tokens':datum.model_input.length,'trainable_tokens':sum(w>0 for w in weights),
                  'demo_prefix_tokens':real_start,'masked_reasoning_tokens':len(expected),
                  'token_sha256':hashlib.sha256(json.dumps(datum.model_input.to_ints()).encode()).hexdigest(),
                  'mask_sha256':hashlib.sha256(bytes(w>0 for w in weights)).hexdigest()}


def build(args):
    from transformers import AutoTokenizer
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    train=set((REPO/'data/appworld-harbor/split_train147.txt').read_text().split())
    test=set((REPO/'data/appworld-harbor/split_test168.txt').read_text().split());assert not train&test
    renderer=create_qwen35_renderer(AutoTokenizer.from_pretrained(args.base_model,local_files_only=True))
    rows=[];provenance=[];excluded=[];audits=[];counts={};seen=set()
    for job in args.jobs:
        result=json.loads((job/'result.json').read_text());assert result.get('finished_at'),'Job not complete'
        for path in sorted(job.glob('*/result.json')):
            trial=path.parent
            if str(trial.resolve()) in seen:raise ValueError('duplicate_source_trial')
            seen.add(str(trial.resolve()))
            raw=json.loads(path.read_text());kw=raw['config']['agent']['kwargs']
            if kw['teacher_provider']=='passthrough' or kw.get('student_harness_dir'):continue
            budget=kw['max_replacements_per_trial'];counts.setdefault(str(budget),{'source':0,'reward_one':0,'eligible':0});counts[str(budget)]['source']+=1
            counts[str(budget)]['reward_one']+=(raw.get('verifier_result')or{}).get('rewards',{}).get('reward')==1
            try:
                row,origin=row_from_trial(trial,train,args.prompt_sha256)
                stats,audit=audit_row(row,renderer,args.max_length)
            except (ValueError,AssertionError,KeyError,FileNotFoundError) as error:
                excluded.append({'task':raw['task_name'],'budget':budget,'trial':str(trial),'reason':str(error) or type(error).__name__});continue
            rows.append(row);provenance.append(origin);audits.append(audit);counts[str(budget)]['eligible']+=1
            if len(rows)%10==0:print(f'Audited {len(rows)} eligible sessions',flush=True)
    for name,data in [('sessions.jsonl',rows),('provenance.jsonl',provenance)]:
        (output/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in data))
    (output/'excluded.json').write_text(json.dumps(excluded,indent=2)+'\n')
    if rows:
        _,stats=prepare_datums(rows=rows,renderer=renderer,max_length=args.max_length)
        assert stats['skipped_too_long']==0 and stats['masked_assistant_messages']==10*len(rows)
    else:stats={}
    audit={'passed':bool(rows),'base_model':args.base_model,'max_length':args.max_length,'datum_stats':stats,
           'data_sha256':hashlib.sha256((output/'sessions.jsonl').read_bytes()).hexdigest(),'per_session':audits}
    (output/'audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    covered={r['task_id'] for r in rows}
    summary={'sessions':len(rows),'unique_tasks':len(covered),'by_budget':counts,'excluded':len(excluded),
             'uncovered_train_tasks':sorted(train-covered),'historical_fixture_only':args.historical_fixture,
             'no_test_overlap':not covered&test,'training_started':False}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    snapshot=output/'build_source';snapshot.mkdir()
    for relative in ['src/harness_zero','src/deepagents_harbor','envs/appworld']:
        shutil.copytree(REPO/relative,snapshot/relative,ignore=shutil.ignore_patterns('__pycache__'))
    (output/'build-source-hashes.json').write_text(json.dumps({str(p.relative_to(snapshot)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(snapshot.rglob('*')) if p.is_file() and '__pycache__' not in p.parts},indent=2)+'\n')
    # Review every budget's short/long and masked sessions without selecting or
    # reweighting training rows by task. HTML rendering is optional: it needs a
    # renderer module on REVIEW_RENDERER_PATH providing render(events, title).
    render = None
    renderer_path = os.environ.get('REVIEW_RENDERER_PATH')
    if renderer_path:
        sys.path.insert(0, renderer_path)
        try:
            from ahd_reviews_to_html import render as _render
            render = _render
        except ImportError:
            render = None
    review_dir = output/'review_examples'
    if render is not None:
        review_dir.mkdir()
    selected=set()
    for budget in sorted({p['budget'] for p in provenance}):
        indices=[i for i,p in enumerate(provenance) if p['budget']==budget]
        if indices:
            ordered=sorted(indices,key=lambda i:audits[i]['tokens'])
            selected.update([ordered[0],ordered[-1],ordered[len(ordered)//2]])
            masked=next((i for i in indices if rows[i]['masked_reasoning_turns']),None)
            if masked is not None:selected.add(masked)
    if render is not None:
        for i in sorted(selected):
            p=provenance[i];events=Path(p['source_trial'])/'agent/teacher/trial/trajectory.jsonl'
            (review_dir/f'b{p["budget"]}-{p["task"]}.html').write_text(render(
                [json.loads(l) for l in events.read_text().splitlines()],f'budget={p["budget"]} task={p["task"]}'))
    (output/'review_selection.json').write_text(json.dumps([{'row':i,**provenance[i],'tokens':audits[i]['tokens']}
        for i in sorted(selected)],indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='uncovered_train_tasks'}),flush=True)
    if not rows:raise RuntimeError('No eligible SFT sessions; inspect exclusions rather than starting training')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',nargs='+',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--base-model',required=True);parser.add_argument('--max-length',type=int,required=True)
    parser.add_argument('--prompt-sha256');parser.add_argument('--wait',action='store_true');parser.add_argument('--historical-fixture',action='store_true')
    args=parser.parse_args()
    if args.wait:
        print('Background finalizer waiting for complete rollout artifacts',flush=True)
        while True:
            ready=True
            for job in args.jobs:
                try:ready=ready and bool(json.loads((job/'result.json').read_text()).get('finished_at'))
                except (FileNotFoundError,json.JSONDecodeError):ready=False
            if ready:break
            time.sleep(30)  # Runs only in the background finalizer, never a blocking UI wait.
    build(args)


if __name__=='__main__':main()
