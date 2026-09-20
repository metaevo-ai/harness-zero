"""Public AppWorld workflow helpers: documented calls, credentials and complete pages."""
import json
import re
from pathlib import PurePosixPath
import urllib.parse
import urllib.request


class PaginationIncomplete(RuntimeError):
    pass


class Workflow:
    def __init__(self):
        import apis
        self.apis = apis
        self.tokens = {}
        self.schemas = {}
        self._raw_call = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def bind_apis(self):
        """Apply the helper contracts to ordinary apis and requester calls too.

        Keep explicit page requests as single pages. Never silently expand an
        endpoint response or change a submitted answer based on task wording.
        """
        app_class = type(self.apis.supervisor)
        original = getattr(app_class.call, '_aw_original', app_class.call)
        self._raw_call = original

        def checked_call(proxy, api, **kwargs):
            if proxy.name == 'supervisor' and api == 'complete_task' and kwargs.get('answer') is not None:
                raise ValueError(
                    'Choose the submission explicitly before changing task state. '
                    'For actions only, use aw.finish_actions() with no answer. '
                    'Only if the instruction requests information, use aw.answer(value). '
                    'The counting demonstration is not a template for action tasks; '
                    'do not attach an action count or a summary. '
                    'If you intentionally set status, preserve it with status=... in either helper. '
                    'This call has NOT submitted or replaced an answer.')
            return self.call(proxy.name, api, **kwargs)

        checked_call._aw_original = original
        app_class.call = checked_call

    def docs(self, app, api=None):
        """Return the endpoint index or a full public endpoint schema."""
        key = (app, api)
        if key not in self.schemas:
            query = urllib.parse.urlencode({'app': app, 'endpoint': api or ''})
            with self.opener.open('http://world:8000/docs?' + query, timeout=30) as response:
                self.schemas[key] = json.load(response)
        return self.schemas[key]

    def login(self, app, *, refresh=False):
        """Log in as the supervisor's account and cache that app's token."""
        if app in self.tokens and not refresh:
            return self.tokens[app]
        schema = self.docs(app, 'login')
        params = {item['name']: item for item in schema['parameters']}
        if not {'username', 'password'} <= params.keys():
            raise ValueError(f'{app}.login has a different contract; inspect aw.docs first')
        profile = self.apis.supervisor.show_profile()
        passwords = {item['account_name']: item['password']
                     for item in self.apis.supervisor.show_account_passwords()}
        identity = 'phone_number' if 'phone_number' in params['username'].get('description', '') else 'email'
        token = getattr(self.apis, app).login(username=profile[identity], password=passwords[app])['access_token']
        self.tokens[app] = token
        return token

    def call(self, app, api, **kwargs):
        """Validate documented keywords, add missing access tokens, call one public API.

        Explicit tokens are never replaced. A failed call is never automatically
        replayed: even some GET download APIs can write files in another app.
        """
        schema = self.docs(app, api)
        parameters = {item['name']: item for item in schema['parameters']}
        extra = kwargs.keys() - parameters.keys()
        if extra:
            raise ValueError(f'Undocumented parameters: {sorted(extra)}. '
                             f'Allowed for {app}.{api}: {sorted(parameters)}. '
                             'Use the exact schema names; ignored filters do not filter records.')
        if {'page_index', 'page_limit'} <= parameters.keys() and 'page_index' not in kwargs:
            raise ValueError(f'{app}.{api} is paginated. Use aw.pages(app, api, **filters) for the complete list. '
                             'Pass page_index explicitly only when deliberately inspecting a single page; '
                             'one page cannot establish a total or maximum.')
        for name, parameter in parameters.items():
            if name not in kwargs or not (name == 'access_token' or name.endswith('_access_token')):
                continue
            if kwargs[name] is None and not parameter['required']:
                continue
            if not isinstance(kwargs[name], str):
                raise TypeError(f'{name} must be a token string. Pass login_result["access_token"], '
                                'not the entire login response dictionary. The supplied identity was '
                                'not changed, and this endpoint was not called.')
        automatic = {}
        for name in parameters:
            if name in kwargs:
                continue
            if name == 'access_token':
                automatic[name] = app
            elif name.endswith('_access_token'):
                automatic[name] = name.removesuffix('_access_token')
        missing = [name for name, parameter in parameters.items()
                   if parameter['required'] and name not in kwargs and name not in automatic]
        if missing:
            raise ValueError(f'Missing required parameters for {app}.{api}: {missing}. Read aw.docs first.')
        for name, account in automatic.items():
            kwargs[name] = self.login(account)
        try:
            if self._raw_call is not None:
                result = self._raw_call(getattr(self.apis, app), api, **kwargs)
            else:
                result = getattr(getattr(self.apis, app), api)(**kwargs)
        except RuntimeError as exc:
            if not str(exc).startswith('Response status code is 401:') or not automatic:
                raise
            for account in set(automatic.values()):
                self.tokens.pop(account, None)
            raise RuntimeError('Authorization failed; automatic tokens were invalidated. '
                               'The call was not replayed. Read back affected state before retrying '
                               'if this operation could modify it, including GET downloads. ' + str(exc)) from exc
        if api == 'logout' and kwargs.get('access_token') == self.tokens.get(app):
            self.tokens.pop(app, None)
        return result

    def pages(self, app, api, *, max_pages=500, **filters):
        """Fetch every page through a final empty page. Never return incomplete data.

        Returns the original list items in API order, without deduplication.
        Global rankings must be computed from the returned collection, not its order.
        """
        schema = self.docs(app, api)
        parameters = {item['name']: item for item in schema['parameters']}
        if schema['method'].upper() != 'GET' or not {'page_index', 'page_limit'} <= parameters.keys():
            raise ValueError('aw.pages requires a documented GET with page_index and page_limit; '
                             'use aw.call and inspect nested collections for non-paginated APIs.')
        if 'page_index' in filters or 'page_limit' in filters:
            raise ValueError('aw.pages controls page_index/page_limit; pass only documented filters.')
        if not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError('max_pages must be a positive integer')
        bounds = re.findall(r'<=\s*(\d+(?:\.\d+)?(?:e[+\-]?\d+)?)',
                            ' '.join(parameters['page_limit'].get('constraints', [])), re.IGNORECASE)
        size = min(20, int(float(bounds[-1]))) if bounds else parameters['page_limit'].get('default', 5)
        if not isinstance(size, int) or size < 1:
            raise ValueError('Cannot determine a valid page_limit from public docs')
        start = parameters['page_index'].get('default', 0)
        records, seen = [], set()
        for offset in range(max_pages):
            try:
                page = self.call(app, api, page_index=start + offset, page_limit=size, **filters)
            except (RuntimeError, ValueError, OSError) as exc:
                raise PaginationIncomplete(f'{app}.{api} failed at page {start + offset}; '
                                           f'collected {len(records)} records but retrieval is NOT complete: {exc}') from exc
            if not isinstance(page, list):
                raise PaginationIncomplete('Expected a top-level list; inspect the documented response shape')
            if not page:
                print(f'[aw.pages] {app}.{api}: {len(records)} records, {offset} nonempty pages; terminal empty page observed')
                return records
            signature = json.dumps(page, sort_keys=True, default=str)
            if signature in seen:
                raise PaginationIncomplete('A whole page repeated; pagination coverage is not established')
            seen.add(signature)
            records.extend(page)
        raise PaginationIncomplete(f'Reached max_pages={max_pages} before an empty page. '
                                   f'{len(records)} records collected; do not report these as the complete set.')

    def finish_actions(self, *, status=None):
        """Submit an action-only task without an invented answer. Verify actions first."""
        kwargs = {} if status is None else {'status': status}
        result = self.call('supervisor', 'complete_task', **kwargs)
        print('[aw.finish_actions] submitted with no answer; completion is not a grading verdict')
        return result

    def move_files(self, paths, *, rename, directory_for=None):
        """Plan then execute non-overwriting moves of a fixed original file set.

        Pure callbacks receive original metadata. rename chooses a basename for
        EVERY source; directory_for independently chooses its final directory.
        Never discovers extra targets in destination directories or retries a
        partial mutation. Existing different files and rename cycles are rejected.
        """
        records = [self.call('file_system','show_file',file_path=path) for path in list(paths)]
        if len({f['file_id'] for f in records}) != len(records):
            raise ValueError('The original file set contains duplicate identities')
        plan = []
        for record in records:
            name = rename(dict(record))
            if not isinstance(name,str) or name in ('','.','..') or '/' in name:
                raise ValueError('rename must return a basename, not a directory or complete path')
            directory = directory_for(dict(record)) if directory_for else PurePosixPath(record['path']).parent
            destination = str(PurePosixPath(directory)/name)
            plan.append({'original':record,'destination':destination,'unchanged':False})
        if len({p['destination'] for p in plan}) != len(plan):
            raise ValueError('Two original files map to the same destination')
        for item in plan:
            path = item['destination']
            if self.call('file_system','file_exists',file_path=path)['exists']:
                existing = self.call('file_system','show_file',file_path=path)
                if existing['file_id'] != item['original']['file_id']:
                    raise FileExistsError(f'Destination already belongs to another file: {path}; no moves applied')
                item['unchanged'] = True
        print('[aw.move_files] frozen plan:',[
            {'file_id':p['original']['file_id'],'source':p['original']['path'],'destination':p['destination']}
            for p in plan])
        for directory in sorted({str(PurePosixPath(p['destination']).parent) for p in plan if not p['unchanged']}):
            self.call('file_system','create_directory',directory_path='~/' if directory=='~' else directory,recursive=True)
        verified = []
        for item in plan:
            source, target = item['original'], item['destination']
            if not item['unchanged']:
                self.call('file_system','move_file',source_file_path=source['path'],
                          destination_file_path=target,retain_dates=True)
            actual = self.call('file_system','show_file',file_path=target)
            if any(actual[field] != source[field] for field in ['content','created_at','updated_at']):
                raise RuntimeError(f'Moved file verification failed: {target}; inspect partial state before retrying')
            verified.append(target)
        print(f'[aw.move_files] verified {len(verified)} planned files; no destination-only objects were added')
        return verified

    def answer(self, value, *, status=None):
        """Submit the computed answer only when the instruction asks for one."""
        kwargs = {} if status is None else {'status': status}
        result = self.call('supervisor', 'complete_task', answer=value, **kwargs)
        print('[aw.answer] submitted answer:', value)
        return result


aw = Workflow()


def __getattr__(name):
    """Both `import aw` and `from aw import aw` expose the same helper."""
    return getattr(aw, name)
