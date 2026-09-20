# AppWorld student harness

This shared bank keeps execute as the only native tool. It reads public task/API
information and never accesses ground truth, private evaluations or task databases.
The common environment provides isolation and trusted grading for both arms; this
bank adds workflow assistance. Performance is established by the paired experiment
below, not by a claim that these rules guarantee every task is solved.

## Components

- bootstrap_instruction asynchronously loads aw and binds its public API contracts
  to normal apis/requester calls. It keeps a concise guide visible. A concrete file
  workflow is added only for matching words in the real task, never the demo history.
- failure_recovery_instruction gives specific repairs for token response dictionaries,
  schema errors, pagination, submission choice, variable mistakes and worker resets.
- context_guard bounds observations while retaining public completion/error metadata.
- submit_gate gives bounded reminders only when observed state is unfinished.
- appworld-workflow supplies aw.py, general guidance and the concrete file workflow.

Framework memory-editing instructions are disabled. The older generic retry guard
is retained but disabled. Registry factories resolve from their own frozen bank,
so later live-bank edits cannot change a running snapshot.

## Public helper contracts

aw.call validates keyword names, required fields and token types, supplies missing
per-app/cross-app credentials, and preserves explicit tokens and status. Successful
logout invalidates only the matching cached token. No failed call is replayed:
even GET download APIs may mutate another app.

Ordinary apis/requester calls use the same contracts. An explicit page_index remains
one page. aw.pages follows every page through an empty result and refuses partial
success on errors, limits or repeated pages; it does not deduplicate or globally sort.

Native non-null answer submission asks the model to explicitly choose aw.answer(value)
or aw.finish_actions() before mutation. The model still decides whether an answer is
requested. No task-type heuristic erases or rewrites an answer, and completion is not
a grading verdict. These are workflow checks, not the security boundary hiding answers.

aw.move_files takes a fixed original path list and pure basename/directory callbacks.
It computes all paths before moving, refuses collisions, preserves dates and reads
back each planned file's content/dates. It never adopts unrelated destination files,
infers the task's conditions, supports overwrites/cycles, or retries partial writes.
After a worker reset, reload aw and run aw.bind_apis(); inspect state before retrying.

## Evaluation and limits

Both arms receive identical complete official ReAct teaching through
AppWorldMinisweAgent and run in AppWorldDockerEnvironment with identical student
model, reasoning effort and turn budget. The shared environment removes host bridge
IPv4 access and refuses to start grading without trusted stop/snapshot evidence.

The harness can repair interface mistakes and assist planning; semantic interpretation,
entity selection and task-specific correctness remain model responsibilities. No task
IDs, specific answers, dates, output names or private verifier rules belong in this bank.
