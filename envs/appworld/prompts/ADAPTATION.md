# Shared official AppWorld initial context

Source: AppWorld `experiments/prompts/react_code_agent/instructions.txt` at
commit `a072b7a86e7c1d5b1d7175659d750ebb9b79f10a`. The exact source and Apache-2.0
license are preserved under upstream/, with its SHA256 in provenance.json.
This is the official ReAct teaching adapted to our bash transport, not a claim
that miniswe reproduces the upstream ReAct implementation or leaderboard score.

## Preserved

All 23 initial messages and the complete teaching sequence are retained:
API/app discovery, specific schema lookup, supervisor credentials, login,
explicit token passing on later calls, persistent variables, complete pagination,
answer submission, all A/B/C/D key instructions, and the actual task with the
supervisor's public identity. All app conventions (phone contacts, virtual file
system, simulated time and boundaries) are retained. No summary replaces the
original teaching sequence.

## Corrections and transport changes

1. Fix the password expression: select the account record, not a Boolean list.
2. Assign spotify_access_token from the login result before using it.
3. Replace the arbitrary ten-page ceiling with a loop through a terminal empty page.
4. Refresh the two shown Spotify schemas from the runtime-compatible public API
   docs. In particular, remove unsupported playlist query/sort_by parameters.
5. Use the current {name, description} API-index entry shape. Index outputs remain
   explicitly abbreviated, as in the upstream example; both Spotify index calls
   show the same relevant entries.
6. Use a clearly fictional demo identity, passwords and token. The actual task
   identity is queried through public supervisor APIs and appears only in its
   final task message. No demonstration code executes in the actual environment.
7. Convert each assistant Python block into an execute tool call containing
   `appworld exec` and a quoted heredoc. Convert each following USER Output into
   its matching ToolMessage. There are 10 assistant/tool pairs plus 3 user messages.
   The teaching explanation remains assistant content and is also supplied as
   reasoning_content for thinking-mode provider compatibility.
8. Generate coherent example outputs by executing corrected demo code against a
   pure in-memory mock. These are fictional teaching outputs, not grading results.
9. Explain the actual bash transport: its Python session persists, the outer shell
   has a different namespace/clock, task files belong to the app APIs, and local
   OS operations only affect scratch space. Do not falsely claim that all OS calls
   are disabled. Explain the fixed worker deadline and partial-mutation semantics.
10. Mark the final real user task with [APPWORLD_CURRENT_TASK]. Harness predicates
    ignore demo history while the model still sees all examples. Initial examples
    never imply that the real task is completed or that example variables exist.

The same AppWorldMinisweAgent builds this foundation for bare and harness arms.
It checks the Harbor instruction against the public task descriptor, removes the
obsolete self-written Working notes, and records appworld_initial_context.json.
The evolved bank can add behavior, but it cannot change these shared messages.
Teacher audit also retains prefilled assistant commands on the first review.

Validation includes 0, 23 and 53 playlist demo fixtures; complete role/tool pairing;
all four key-instruction sections; current schemas; exact shared-context equality;
real-task boundaries; and provider request checks. Scores under the old incomplete
base context are historical diagnostics and must not be mixed with the new study.
