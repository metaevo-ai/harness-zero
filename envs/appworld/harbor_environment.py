"""Host-side proof that student execution stopped before snapshot and grading."""
import asyncio
from dataclasses import dataclass
import json

from harbor.environments.docker.docker import DockerEnvironment


class UntrustedAppWorldSnapshot(RuntimeError):
    pass


@dataclass
class StopProof:
    container_id: str
    project: str
    stopped: bool = False
    snapshot: bool = False
    failed: bool = False


# Both environments share the host trial process. This is never mounted or sent
# to a student; missing evidence (including after process loss) fails closed.
_PROOFS = {}


class AppWorldDockerEnvironment(DockerEnvironment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.role = json.loads((self.environment_dir/'appworld-role.json').read_text())['role']
        if self.role not in ('agent','verifier'):
            raise ValueError('Unknown trusted AppWorld environment role')

    async def _docker(self, *args):
        process = await asyncio.create_subprocess_exec('docker', *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise UntrustedAppWorldSnapshot('Docker stop verification failed: '+stderr.decode()[:500])
        return stdout.decode().strip()

    async def _check_stopped(self, proof):
        ids = (await self._docker('ps','-a','--no-trunc',
            '--filter',f'label=com.docker.compose.project={proof.project}',
            '--filter','label=com.docker.compose.service=main','--format','{{.ID}}')).split()
        if set(ids)-{proof.container_id}:
            raise UntrustedAppWorldSnapshot('Unexpected replacement student container')
        if proof.container_id in ids:
            state = await self._docker('inspect','--format',
                '{{.State.Running}} {{.State.Paused}} {{.State.Restarting}}',proof.container_id)
            if state != 'false false false':
                raise UntrustedAppWorldSnapshot('Student container has not stopped')

    async def start(self, force_build):
        key = str(self.context_id)
        if self.role == 'verifier':
            proof = _PROOFS.get(key)
            if proof is None or proof.failed or not proof.stopped or not proof.snapshot:
                raise UntrustedAppWorldSnapshot('No trusted stop-before-snapshot evidence; verifier will not start')
            try:
                await self._check_stopped(proof)
            except Exception:
                proof.failed = True
                raise
            return await super().start(force_build)
        await super().start(force_build)
        result = await self._run_docker_compose_command(['ps','--all','--quiet','main'])
        ids = result.stdout.split()
        if len(ids)!=1:
            raise UntrustedAppWorldSnapshot('Expected exactly one student container')
        labels = json.loads(await self._docker('inspect','--format','{{json .Config.Labels}}',ids[0]))
        project = labels.get('com.docker.compose.project')
        restart = await self._docker('inspect','--format','{{.HostConfig.RestartPolicy.Name}}',ids[0])
        if not project or labels.get('com.docker.compose.service')!='main' or restart not in ('','no'):
            raise UntrustedAppWorldSnapshot('Untrusted student container identity or restart policy')
        _PROOFS[key] = StopProof(ids[0],project)

    async def stop_service(self, service):
        if self.role!='agent' or service!='main':
            return await super().stop_service(service)
        proof = _PROOFS.get(str(self.context_id))
        try:
            await super().stop_service(service)
            if proof is None:
                raise UntrustedAppWorldSnapshot('Missing student container identity')
            await self._check_stopped(proof)
            proof.stopped = True
        except Exception:
            if proof is not None: proof.failed = True
            raise

    async def service_exec(self, command, *, service=None, **kwargs):
        if self.role!='agent' or service!='world' or command.strip()!='python /opt/appworld_env/snapshot.py':
            return await super().service_exec(command,service=service,**kwargs)
        proof = _PROOFS.get(str(self.context_id))
        try:
            if proof is None or proof.failed or not proof.stopped:
                raise UntrustedAppWorldSnapshot('Cannot snapshot an unverified student stop')
            await self._check_stopped(proof)
            result = await super().service_exec(command,service=service,**kwargs)
            if result.return_code:
                raise UntrustedAppWorldSnapshot('World snapshot failed')
            proof.snapshot = True
            return result
        except Exception:
            if proof is not None: proof.failed = True
            raise

    async def stop(self, *args, **kwargs):
        try:
            return await super().stop(*args, **kwargs)
        finally:
            if self.role=='verifier': _PROOFS.pop(str(self.context_id),None)
