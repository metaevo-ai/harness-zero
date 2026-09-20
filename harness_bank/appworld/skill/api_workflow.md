# Public API workflow — skill:api-workflow v1

Evidence: current-task instruction, public docs and executed tool results only.
Trigger: unknown contract, failed authentication/parameters, incomplete sources, or an
answer computed from a field that does not match the request. Non-trigger: correct
explicit tokens, intentional single-page inspection, complete equivalent loops.
Intervention: supply a full executable next action using raw APIs; missing schemas are
obtained through a visible execute call. PASS useful exploration and documented alternatives.

Discovery:
```python
print(apis.api_docs.show_api_descriptions(app_name='phone'))
print(apis.api_docs.show_api_doc(app_name='phone', api_name='show_contacts'))
```
For authentication, use actual public profile fields and account_name/password records.
Login returns a dictionary; the access_token value is the string to pass. Do not reuse
fictional demo credentials or assume calling login authenticates all future calls.

For an endpoint documented with page_index/page_limit and a list response, keep its
legal page size constant and continue to an empty page. Apply this also to source contacts
and container libraries. Preserve explicit query filters. A request failure must propagate;
do not catch it as []. Repeated pages are a signal to inspect the contract, not deduplicate
until some arbitrary cap is reached. Some APIs return full nested collections without paging.

Get fields from their actual entity's detail API. A library listing may omit songs which
are present inside albums/playlists, and a container's owner is not a member's artist.
Build the union of requested entity IDs, fetch each needed detail once, then compute in
Python. Check tie rules only when the question or visible data makes them relevant.

Implicit friends/family/coworkers/roommates use phone contacts per the shared official
convention; use other relationship sources only when explicitly requested. Preserve both
participants of a transaction if the task says 'involving', and all stated date boundaries.
Task time is frozen inside appworld exec; the outer shell clock is not the task clock.
