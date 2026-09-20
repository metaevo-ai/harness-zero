import asyncio
import json
from types import SimpleNamespace

import pytest

from envs.appworld import harbor_environment as env


@pytest.fixture
def setup(monkeypatch):
    env._PROOFS.clear()
    state = {'running':True,'exists':True,'stop_error':False,'false_stop':False,
             'replacement':False,'snapshot_code':0,'starts':[]}
    async def base_start(self,force_build): state['starts'].append(self.role)
    async def base_stop(self,*args,**kwargs): state.update(exists=False,running=False)
    async def stop_service(self,service):
        if state['stop_error']: raise RuntimeError('stop failed')
        if not state['false_stop']: state['running']=False
    async def service_exec(self,*args,**kwargs): return SimpleNamespace(return_code=state['snapshot_code'])
    async def compose(self,*args,**kwargs): return SimpleNamespace(stdout='original-id')
    async def docker(self,*args):
        if args[0]=='ps':
            if state['replacement']: return 'replacement-id'
            return 'original-id' if state['exists'] else ''
        if 'Labels' in args[2]: return json.dumps({'com.docker.compose.project':'test','com.docker.compose.service':'main'})
        if 'RestartPolicy' in args[2]: return 'no'
        return 'true false false' if state['running'] else 'false false false'
    monkeypatch.setattr(env.DockerEnvironment,'start',base_start)
    monkeypatch.setattr(env.DockerEnvironment,'stop',base_stop)
    monkeypatch.setattr(env.DockerEnvironment,'stop_service',stop_service)
    monkeypatch.setattr(env.DockerEnvironment,'service_exec',service_exec)
    monkeypatch.setattr(env.AppWorldDockerEnvironment,'_run_docker_compose_command',compose)
    monkeypatch.setattr(env.AppWorldDockerEnvironment,'_docker',docker)
    def make(role,key='trial'):
        result=object.__new__(env.AppWorldDockerEnvironment)
        result.role=role;result.context_id=key
        return result
    yield state,make
    env._PROOFS.clear()


@pytest.mark.parametrize('failure',['stop_error','false_stop'])
def test_swallowed_stop_failure_cannot_start_verifier(setup,failure):
    state,make=setup
    async def run():
        agent,verifier=make('agent'),make('verifier')
        await agent.start(False)
        state[failure]=True
        with pytest.raises(RuntimeError): await agent.stop_service('main')
        with pytest.raises(env.UntrustedAppWorldSnapshot):
            await agent.service_exec('python /opt/appworld_env/snapshot.py',service='world')
        # Even if Harbor subsequently cleans up successfully, the old snapshot
        # timing cannot be retroactively trusted.
        await agent.stop()
        with pytest.raises(env.UntrustedAppWorldSnapshot): await verifier.start(False)
        assert state['starts']==['agent']
    asyncio.run(run())


def test_stopped_snapshot_and_confirmed_removal_allow_verifier(setup):
    state,make=setup
    async def run():
        agent,verifier=make('agent'),make('verifier')
        await agent.start(False)
        await agent.stop_service('main')
        await agent.service_exec('python /opt/appworld_env/snapshot.py',service='world')
        await agent.stop()
        await verifier.start(False)
        assert state['starts']==['agent','verifier']
        await verifier.stop()
        assert not env._PROOFS
    asyncio.run(run())


def test_replacement_container_and_missing_context_fail_closed(setup):
    state,make=setup
    async def run():
        agent=make('agent')
        await agent.start(False)
        await agent.stop_service('main')
        await agent.service_exec('python /opt/appworld_env/snapshot.py',service='world')
        with pytest.raises(env.UntrustedAppWorldSnapshot): await make('verifier','another-trial').start(False)
        state['replacement']=True
        with pytest.raises(env.UntrustedAppWorldSnapshot): await make('verifier').start(False)
        assert env._PROOFS['trial'].failed
        assert state['starts']==['agent']
    asyncio.run(run())


def test_failed_snapshot_is_not_valid_stop_evidence(setup):
    state,make=setup
    async def run():
        agent=make('agent');await agent.start(False);await agent.stop_service('main')
        state['snapshot_code']=1
        with pytest.raises(env.UntrustedAppWorldSnapshot):
            await agent.service_exec('python /opt/appworld_env/snapshot.py',service='world')
        with pytest.raises(env.UntrustedAppWorldSnapshot): await make('verifier').start(False)
        assert state['starts']==['agent']
    asyncio.run(run())
