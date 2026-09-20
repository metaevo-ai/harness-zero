# Action scope and app files — skill:actions-files v1

Evidence: original current-task text, original object metadata, executed mutations and
read-back. Trigger: compound/negative scope, destructive writes, export-before-delete,
or completion contradicted by visible state. Non-trigger: read-only queries, already-correct
entities, fully verified alternative implementations. No hidden correctness signals.
Intervention: replace with a bounded plan/execution/check that preserves unrelated state.

Construct the full original target set before writing. Compute a final state/path for every
original ID: common naming/value changes and conditional routing are independent operations.
Do not move a subset and then rediscover a smaller source list for a global change. Do not
include old destination objects during recovery merely because their date/type matches.
Source IDs identify the ORIGINAL selection; a move/copy may create a new record ID.
retain_dates preserves dates, not file_id. Verify destination path/content/date against
the original plan; assert ID preservation only if the public contract guarantees it.
Use datetime.fromisoformat(...).strftime('%Y-%m-%d') for a requested full calendar date;
do not remove arbitrary characters from an ISO timestamp.

For file moves/renames, inspect public move_file parameters (source_file_path,
destination_file_path, retain_dates). Check collisions; preserve original metadata where
required. A move can rename in one step. Copy/delete defaults may reset dates. Preserve the
original classification fields; never reclassify moved files using mutated timestamps.

For exports, define the requested row entity and fetch all source members; use stable IDs
for deduplication. Compose CSV with csv.writer and io.StringIO, not manual comma joins.
The shell filesystem is scratch only. Write via file_system.create_file with explicit
content, then show_file at the user's ORIGINAL path and assert columns, data and row coverage
before removing originals/accounts. '~/' differs from '/', and bare '~' is not a valid
replacement for the home-directory spelling. Do not use local read-back as proof of app delivery.

Read back all final groups, not just the source. Compare required target IDs/values/names;
for 'disable the rest', assert every non-target is disabled. A success HTTP response does
not establish the required final state. If a later API fails, earlier writes remain:
repair the remaining defect without recreating correct backups or replaying successful deletes.
