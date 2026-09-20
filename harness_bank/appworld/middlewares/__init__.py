"""Candidate-only AppWorld review cues, version 1.

Consumes current task text, tool observations and the unexecuted proposal. No
backend, filesystem, network or reward access. Triggers are advisory: a valid
alternative or an already verified result should pass without extra work.
"""
import json
import re

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint

MARKER = '[APPWORLD_CURRENT_TASK]'


def current_messages(candidate):
    messages = candidate.context or []
    for index in range(len(messages)-1, -1, -1):
        message = messages[index]
        if message.get('type') in ('human','user') and isinstance(message.get('content'),str) and message['content'].startswith(MARKER):
            return messages[index:]
    return []


def execution_failed(text):
    for line in reversed(text.splitlines()):
        if line.startswith('[AppWorld result] '):
            try:
                return json.loads(line.removeprefix('[AppWorld result] ')).get('error') is True
            except json.JSONDecodeError:
                break
    return bool(re.search(r'\[exit_code=(?!0\])[-\d]+\]',text))


class AppWorldReviewMiddleware(TeacherCandidateMiddleware):
    name = 'appworld-review-v1'

    def hint(self, candidate):
        messages = current_messages(candidate)
        if not messages:
            return None
        call = candidate.original.tool_call
        command = call.command if call else ''
        task = messages[0]['content']
        notes, evidence = [], []
        if candidate.candidate_issue:
            evidence.append(candidate.candidate_issue)
            notes.append('Repair the structured execute response; code in prose does not execute.')
        latest = next((m.get('content','') for m in reversed(messages) if m.get('type')=='tool'), '')
        if isinstance(latest,str) and execution_failed(latest):
            evidence.append('Latest executed tool reports an error.')
            notes.append('Repair its actual cause using visible evidence: exact lowercase api_docs and schema, token string extraction, '
                         'explicit app token, missing assignment or session_reset. Do not replace an unavailable metric with a different one. '
                         'An error may follow earlier successful mutations: avoid replay or blank overwrite. Read skill:api-workflow.')
        if re.search(r'\bapis\.ApiDocs\b|\baw\.',command):
            evidence.append('Proposal uses a wrong API namespace or a student-harness-only helper.')
            notes.append('This student has raw apis/requester, not aw. Use apis.api_docs for discovery and translate the action into normal APIs.')
        if re.search(r'complete_task\s*\(',command) or call is None:
            evidence.append('Proposal submits or finishes the current task.')
            notes.append('Compare every requested deliverable with ACTUAL observations. Check exact metric/entity and all target IDs. '
                         'For actions only omit answer; for requested information preserve the valid computed answer. '
                         'Do not force an alternative completion helper. PASS a complete verified result; otherwise replace the unresolved step.')
            answer_arg = re.search(r'complete_task\s*\([^)]*?\banswer\s*=\s*([^,\)]+)',command)
            asks_information = re.search(r'\?|\b(?:what|which|who|how many|how much|tell me|describe|list|title|name)\b',task,re.I)
            if answer_arg and answer_arg.group(1).strip().strip('"\'') not in {'None','null',''} and not asks_information:
                evidence.append('Action-only instruction but the submitted answer is non-empty.')
                notes.append('REPLACE with a bare complete_task(): an unrequested answer fails the submission check. '
                             'Keep the verified actions; only the answer field changes.')
        relation_task = re.search(r'\b(?:friends?|roommates?|coworkers?|family|relatives?)\b',task,re.I)
        write_call = re.search(r'\b(?:like|unlike|comment|deny|approve|record_expense|create_transaction|add_friend|remove_friend|'
                               r'send_\w*message|request_\w*|share\w*|follow|rate|rating|review|post)\w*\s*\(',command)
        relation_read = re.search(r'show_contact_relationships|search_contacts\s*\([^)]*\brelationship\s*=|\brelationship\s*=',
                                  json.dumps(messages))
        if relation_task and write_call and not relation_read:
            evidence.append('Task names a personal relationship, and this write is not preceded by a relationship-source query.')
            notes.append('Derive the qualified target set from the relationship source before writing: '
                         'all contacts, a user-search result page, or same-name containers are not that set. '
                         'Replace with the relationship enumeration step unless the qualified set is already evidenced.')
        writes = re.search(r'\b(?:delete\w*|terminate\w*|move_file|copy_file|create_file|update\w*|disable\w*)\s*\(',command)
        if writes:
            evidence.append('Proposal changes or removes application state.')
            notes.append('Check original target scope and every required operation before executing; retain original classification metadata. '
                         'Verify backups through the original app path before deletion; never omit content on an overwrite. '
                         'Do not include pre-existing destination files in recovery. Read skill:actions-files when relevant.')
        if re.search(r'\b(?:file|files|backup|export|csv|directory|folder)\b',task,re.I) and re.search(r'\bopen\s*\(|os\.makedirs|\.write_text\s*\(',command):
            evidence.append('File-related task proposes local OS file access.')
            notes.append('Check whether this is legitimate scratch work or the requested app deliverable. Only public file_system APIs '
                         'operate on task files. PASS necessary scratch work, but replace a claimed app write/read with the real API operation.')
        if not notes:
            return None
        return TeacherHint('middleware:appworld-review-v1', ' '.join(evidence), '\n'.join(notes))
