AppWorld file task: use this concrete workflow before any destructive step.

EXPORT / BACKUP
First state what ONE output row represents. Resolve containers to that entity type,
then deduplicate by its stable ID. If a container lacks a requested field, inspect
its member IDs and fetch their details before writing. Do not substitute a container
title/owner or an empty cell for a requested member field.
Use io.StringIO and csv.writer for CSV text; open/os.makedirs/shell redirection only
write student scratch files, even when their path contains '~'. They cannot deliver
an AppWorld file. Once target_path and csv_text are computed from the actual task:

```python
from pathlib import PurePosixPath
parent = str(PurePosixPath(target_path).parent)
aw.call('file_system', 'create_directory',
        directory_path='~/' if parent == '~' else parent, recursive=True)
aw.call('file_system', 'create_file', file_path=target_path, content=csv_text)
saved = aw.call('file_system', 'show_file', file_path=target_path)
assert saved['content'] == csv_text
```

Keep the user's original target_path, including '~'. Confirm columns, row count and
required source membership from this API read-back BEFORE removing originals/accounts.
Inspect existing destinations rather than silently overwriting unrelated content.

MULTIPLE FILE OPERATIONS
Use aw.move_files(original_paths, rename=rename_from_original,
                  directory_for=folder_from_original).
First freeze original_paths from the complete requested source collection.
Define two PURE functions on a file's original public metadata:
- rename_from_original(file): return the required new BASENAME for every file.
- folder_from_original(file): independently return its destination DIRECTORY.
Keep a common name transform outside the conditional directory choice. The helper
applies both functions to every original file, computes all final paths before moving,
refuses collisions, preserves dates, and reads back every planned file's content/dates.
It never adds pre-existing destination files to the target set during recovery.

The helper does not infer your naming rule, route condition or source scope. Check
these against the task before execution. It does not support overwriting other files
or cyclic renames. If interrupted, inspect partial state before any retry; do not
assume files already at the destination came from your original set. Only after
verification, submit without an answer for actions only, or aw.answer(value) when
information was requested.
