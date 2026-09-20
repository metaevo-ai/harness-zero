# AppWorld review priorities (version 1)

1. Read the real instruction and decide what each output row/entity/metric represents.
   A container is not its members. Expand member IDs and combine every requested source
   before deduplicating stable entity IDs. Missing detail fields call for the documented
   detail endpoint, not a new metric such as frequency, row position, or a subtotal.
   A container's flag is not its members' flag: liked/favorite/followed designations come
   from the dedicated endpoint or each target entity's own boolean field, never from the
   parent collection. Union expansion serves literal "all X in my library" requests, not
   flag-filtered subsets; before writing a large expanded set, spot-check the flag on a
   few members.
2. Authentication is per app. Extract login_result['access_token'] and pass the string
   explicitly to each protected call; cross-app attachment/file operations may require
   a second token. Exact endpoint and parameter spelling matters: apis.api_docs is lowercase.
3. Terminal empty pages establish coverage only with consistent page size and filters,
   including relationship sources and parent collections. Errors never mean an empty set.
   Cache completed lists rather than repeatedly scanning them until the API budget expires.
   A filtered query's first page is not the target set either: every/all/each requests
   enumerate from the relationship source or continue to an empty page under constant filters.
4. Turn all/every/rest and compound instructions into sets of target IDs and postconditions.
   A common transformation still applies to objects sent to different destinations.
   Verify every required ID, including negatives such as all other alarms being disabled.
5. Public app files live behind file_system APIs. Shell/open writes are local scratch.
   Preserve requested home-relative paths, original metadata and scope. A backup must be
   read back at the original requested app path with correct rows/columns BEFORE deletion.
   Never recreate an existing backup with overwrite=True and omitted content during recovery.
6. For financial and ranking questions, use the exact requested field, unit and definition.
   Do not substitute a subtotal for a bill total or playlist occurrence for song likes.
   If evidence is unavailable, inspect the relevant public detail schema rather than inventing it.
7. Separate submission from verification. complete_task indicates submitted, not correct.
   Actions-only completion omits answer; information requests supply the computed answer.
   Known mistakes can be corrected, but never overwrite sound artifacts to repair a separate
   submission error. Final narrative does not perform missing API mutations.
8. Deliver only the requested payload. Replies and messages carry the requested entities
   alone, without greetings, prefixes, or summaries; unrequested prose can itself violate
   a stated exclusion. Export headers, separators and column layout follow the task text
   literally; multi-value fields join inside a column, never as the column separator.

These priorities are evidence checks, not extra task requirements. Equivalent correct
methods and already-established facts warrant PASS. A syntactic trigger alone never
proves an error; choose REPLACE only when the visible evidence supports a better action.
