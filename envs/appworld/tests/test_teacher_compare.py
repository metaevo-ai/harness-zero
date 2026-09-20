import copy
from pathlib import Path

from envs.appworld.teacher_compare import ARMS, arm_of, config_for, privacy_issues


def test_three_arms_share_student_and_teacher_arm_has_no_student_bank(tmp_path):
    p={'student_model':'azure:qwen--qwen35-9b','student_reasoning':'high','max_turns':80,
       'source':str(tmp_path),'student_bank':str(tmp_path/'student'),
       'teacher_components':str(tmp_path/'teacher'),'teacher_provider':'azure_openai',
       'teacher_model':'gpt-5.6-sol','teacher_reasoning':'high','max_replacements':5,
       'concurrency':6,'dataset':str(tmp_path/'tasks')}
    c=config_for(p,['one','two'],'test-job')
    assert c['n_concurrent_trials']==6 and c['n_attempts']==1
    assert c['retry']['max_retries']==0
    assert c['environment']['import_path'].endswith(':AppWorldDockerEnvironment')
    assert [arm_of(a['kwargs']) for a in c['agents']]==list(ARMS)
    a,b,t=copy.deepcopy(c['agents'])
    b['kwargs']['student_harness_dir']=None
    assert a==b
    assert t['kwargs']['student_harness_dir'] is None
    assert t['model_name']==a['model_name'] and t['kwargs']['model_kwargs']==a['kwargs']['model_kwargs']
    assert t['kwargs']['teacher_model']=='gpt-5.6-sol' and t['kwargs']['max_replacements_per_trial']==5


def test_replacement_privacy_scan_allows_raw_public_apis():
    assert not privacy_issues({'reasoning':'I will inspect the endpoint.',
        'tool_call':{'name':'execute','command':"appworld exec <<'PY'\nprint(apis.api_docs.show_app_descriptions())\nPY"}})
    for text in ['aw.answer(5)','Read /components/memory.md','The teacher recommends this','/opt/ahd/harness/aw.py']:
        assert privacy_issues({'content':text})
