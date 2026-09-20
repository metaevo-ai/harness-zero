# AppWorld teacher harness v1.1

Entry: profile.py:build_teacher_middlewares. Teacher provider/model are explicit
run parameters, not chosen by this bank. The third comparison arm is bare miniswe
with an review-before-execution teacher; no student bank or aw helper is installed.

The bank translates the student harness's useful contracts into teacher checks and
ordinary public-API replacement patterns. Teacher only sees the student-visible
prefix/current proposal and its own components. It cannot execute in the sandbox,
read task databases/GT, inspect scores, or roll back a completed call. Missing facts
must be acquired through a replacement execute step and become visible to the student.

Middleware appworld-review-v1 surfaces the latest genuine execution error, invalid
namespace/helper calls, submission/final candidates, app mutations and possible local
file/app-file confusion. It ignores demonstrations before APPWORLD_CURRENT_TASK.
Triggers are cues, not mandatory REPLACE: correct explicit answers, equivalent loops,
legitimate scratch files and already-established final states are non-error cases.
Positive/negative trigger, demo-boundary, privacy and raw-pattern tests are under
envs/appworld/tests/test_teacher_harness.py.

Sources are previous student-visible AppWorld traces: API casing/token failures,
incomplete source/member sets, subtotal/metric substitution, omissions despite
read-back, wrong app paths and destructive recovery. Task IDs and concrete values
never appear in shared components.

Components:
- memory:appworld-review — prioritized decision checks.
- skill:api-workflow — public contracts, authentication, paging, entity/metric selection.
- skill:actions-files — fixed original scope, compound operations, backup and read-back.
- tool:public-patterns — executable templates to adapt into the student's own action.

No whole aw library is copied into student actions. Replacements must contain no
review instructions, component text/private paths or hidden answers; privacy is audited
from accepted responses. Correct ordinary complete_task(answer=...) stays valid, avoiding
the student-side gate's needless interface friction on information tasks.

Evaluation: all comparison arms share official initial teaching, an isolated
task/environment, student model/reasoning/turn budget and one attempt.

Smoke correction: source IDs define original scope, but move/copy record IDs may change.
retain_dates does not imply file_id preservation; destination verification follows the public contract.
