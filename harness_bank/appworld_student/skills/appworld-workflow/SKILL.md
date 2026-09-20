---
name: appworld-workflow
description: Public API helpers for strict parameters, automatic per-app authentication, complete pagination, verified mutations and explicit action/answer completion.
---

# AppWorld workflow helpers

`aw` is already imported in the persistent `appworld exec` Python session.
Use `execute` to run:

```bash
appworld exec <<'PY'
print(aw.docs('spotify', 'show_playlist_library'))
playlists = aw.pages('spotify', 'show_playlist_library')
print(len(playlists))
PY
```

- `aw.docs(app)` returns an endpoint index; `aw.docs(app, api)` returns its full
  public schema, including response fields and constraints.
- `aw.call(app, api, field=value)` rejects undocumented/missing keywords and
  supplies per-app access tokens, including file-system tokens for attachments.
  Explicit tokens are preserved. `aw.bind_apis()` is installed at startup: ordinary
  `apis` and `requester` calls share validation and token handling. Missing page_index
  raises a clear error; explicit page_index still means exactly one page.
- `aw.pages(app, api, **filters)` uses legal page parameters and continues until
  an empty page. It returns a full list, not a partial result on failure. It does
  not deduplicate or impose a global sort; compute your metric on the full list.
  Non-paginated APIs can have multiple nested collections: inspect all relevant ones.
- `aw.finish_actions()` submits with no answer, after the requested actions and
  their read-back checks. An action-only task never needs a name, list or summary
  in the supervisor answer field.
- `aw.answer(value)` submits the computed answer for an instruction requesting
  information. It does not validate your reasoning or the business state. Ordinary
  `complete_task(answer=non_null_value)` requests an explicit choice before submitting;
  it never erases an answer or guesses your task type. Both finishing helpers accept
  `status=...` when intentionally reporting failure.

The worked example returns a count because its task requests one. Do not append
a count or summary to a pure action task. Completion means submitted, not correct;
if you identify a specific submission mistake, correct it once even when completed.

Preserve the original task verb and all qualifiers. Read referenced messages;
resolve relationships using actual evidence, not an unrelated name search.
Freeze and deduplicate target IDs across sources before changing state, preserve unrelated state,
and read back. If an object is already correct, do not modify it twice.

Do not silence a failed read as an empty set. On 401 inspect authentication;
on 422 inspect the precise schema; after an uncertain mutation inspect state
before retrying. Only read the simulated phone clock when a relative date is needed.

After a worker reset, restore the helper inside `appworld exec`:
```python
import sys
sys.path.insert(0, '/opt/ahd/harness/skills/appworld-workflow')
import aw
aw.bind_apis()
```
App state persists across a worker reset, but Python variables must be re-created.


For tasks combining operations, write an operation-to-target plan before mutation.
If every file must be renamed and a subset must also be moved, the subset still
needs both operations. Preserve original metadata needed to build final paths.
When copy/move implements rename, inspect retain_dates and preserve original dates
unless changing them is requested. Read back every output group, not only the source.

Keep application paths exactly as requested: ~/x is different from /x. Verify the
result through the user's original path, independently of the path variable used
to write. Before deleting originals after backup/export, verify that path, required
columns, row count and contents. A successful read at the wrong path is not delivery.


`aw.move_files(original_paths, rename=..., directory_for=...)` plans a fixed original
file set before applying non-overwriting moves. The pure rename callback returns each
file's basename; the independent directory callback returns its parent directory.
Both receive original show_file metadata. The helper preserves dates and checks every
planned destination's content/dates; it never adopts unrelated destination files.
It rejects existing different files and cycles, does not infer task conditions, and
does not automatically retry partial mutations. See files.md for the workflow.
