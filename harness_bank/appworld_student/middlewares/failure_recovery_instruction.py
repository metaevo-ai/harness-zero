"""Give concrete recovery steps for the latest failed execution."""
from langchain_core.messages import ToolMessage
from ._observations import current_task_messages, observation
from ._prompt_injection import PromptInstructionMiddleware, has_tool_error
from deepagents.middleware._utils import append_to_system_message


class FailureRecoveryInstructionMiddleware(PromptInstructionMiddleware):
    name = 'failure-recovery-instruction'

    def __init__(self):
        super().__init__(instruction='', predicate=has_tool_error)

    def _modify(self, request):
        latest = next((m for m in reversed(current_task_messages(request.messages)) if isinstance(m, ToolMessage)), None)
        result = observation(latest)
        if result is None or not result['error']:
            return request
        detail = str(result['signature'])
        if 'Choose the submission explicitly' in detail:
            note = ('The last submission was not applied. Re-read whether the task requests an answer. '
                    'Use aw.finish_actions() for actions only, or aw.answer(value) for requested information. '
                    'Do not copy the counting example into an action task. Preserve any intentional status. '
                    'If correcting a known earlier mistake, submitted=True does not prevent correction.')
        elif 'must be a token string' in detail:
            note = ('login() returns a dictionary, not a token string. Assign token = login_result["access_token"], '
                    'then pass that string to the endpoint. Do not slice the login dictionary or repeatedly '
                    'retry it unchanged. Fix the failed relationship/data read; do not drop a required condition.')
        elif '401' in detail:
            note = ('Authentication failed. Do not abandon this data source or guess another identity. '
                    'Use aw.call(app, api, **kwargs), which supplies the correct app token. '
                    'Bound apis calls use the same handling. An explicit token is never replaced: '
                    'check whether you supplied the wrong app token. Read affected state before retrying '
                    'an operation that may have partially completed.')
        elif 'is paginated' in detail:
            note = ('This endpoint was not called because an implicit first page is incomplete evidence. '
                    'Use aw.pages(app, api, **filters) for every result, including contacts and parent lists. '
                    'For a deliberate page sample or your own loop, pass page_index explicitly. '
                    'One explicit page still does not establish a complete set.')
        elif 'TypeError' in detail or '422' in detail or 'parameter' in detail.lower():
            note = ('Fix the failed endpoint contract before changing business strategy: '
                    'print(aw.docs(app, api)), then aw.call(app, api, field=value). '
                    'Data arguments are keyword-only; use exact names and legal limits. '
                    'Do not use limit/text/email unless that specific schema lists them.')
        elif 'PaginationIncomplete' in detail:
            note = ('Retrieval is incomplete. Do not compute an answer from the partial data. '
                    'Inspect the exact failing page/filter and schema; do not catch this error and return [].')
        elif 'NameError' in detail:
            note = ('Check the exact missing variable and whether it was assigned, rather than only printed. '
                    'Do not assume the session reset without session_reset=true. '
                    'If it did reset, restore aw and its binding using the guide, then reconstruct needed variables.')
        elif 'TimeoutError' in detail:
            note = ('Check whether the worker reset. Restore aw with the import command in your guide if needed. '
                    'Keep the next operation smaller, and read affected app state before retrying a mutation.')
        else:
            note = ('Inspect the failed endpoint schema and the actual affected state. '
                    'If an object already exists or an action was already applied, verify it instead of duplicating it. '
                    'Do not turn failed reads into empty collections or silently catch errors as success.')
        return request.override(system_message=append_to_system_message(request.system_message, note))


def make_middleware():
    return FailureRecoveryInstructionMiddleware()
