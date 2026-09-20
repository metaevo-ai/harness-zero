I have received job application emails for Customer Support role over the last 4 weeks. I need you to do two things for my ongoing recruiting process. 1. Create a "applications_list.csv" file with the headers "First Name", "Last Name" and "LinkedIn URL" fields, the values of which should be extracted from the emails. Skip ones who did not provide their LinkedIn URLs in their emails. 2. Download their email attachments in "attachments/<first_name>__<last_name>/" directory. Replace <first_name> and <last_name> as per their names (lower cased), and use file names as per their attachment names in the email. The csv file and "attachments/" directory should be in "~/documents/work/recruiting_process/" directory in my file system.

## Working notes

The AppWorld task is already initialized. Its database runs in a separate
service. Use the following CLI from bash (available with or without a harness):

```bash
appworld docs supervisor
appworld docs supervisor show_account_passwords
appworld exec <<'PY'
print(apis.supervisor.show_profile())
print(apis.supervisor.show_account_passwords())
PY
```

`apis` and `requester` are already defined. `import apis` and
`from apis import spotify` also work and use the same public API proxies. Python
variables and app state persist across `appworld exec` calls. Standard Python
imports are supported. Print values you want to inspect. On an exception,
earlier mutations remain applied and earlier printed output is preserved.
Python has a 100-second execution deadline (override with --timeout, max 120).
A timed-out worker is stopped and its Python variables reset; app state remains.
Read affected entities before retrying, since an API in flight may still finish.

`appworld docs <app>` lists endpoints; add the endpoint name for its complete
parameters and response schemas. `appworld task` shows the fixed task id and
instruction. `appworld status` reports whether the supervisor marked the task
complete; it does not report grading results. Use the `execute` tool for all these CLI commands.

Credentials come from `apis.supervisor.show_account_passwords()` (a list keyed
by `account_name`) and `apis.supervisor.show_profile()` (email and phone number).
The simulated date comes from the phone app, not the system clock.

Finish with `apis.supervisor.complete_task(answer=...)` if the instruction
requests an answer, otherwise `apis.supervisor.complete_task()`. Only submit
again if you need to correct your previous submission; a new submission
replaces the previous answer. Completion status does not mean the task passed.

Only public app APIs affect the graded world. Local files are your workspace;
the verifier evaluates the state collected directly from the AppWorld service.
