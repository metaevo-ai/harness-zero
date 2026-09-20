In my company, we invite an external speaker to give a talk every Thursday at 1 PM. My manager has sent me a schedule for the next few weeks over email, and has tasked me to send a reminder email to all company members a few hours before the talk. I need you to schedule these emails to be automatically sent at 9 AM of the days of the talks. The email subject should be "Talk Reminder" and its body should be as per template saved in my file system in "~/documents/work_stuff".

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
