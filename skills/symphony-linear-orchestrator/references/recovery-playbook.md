# Recovery Playbook

## Bootstrap assertion failure

Symptoms:

- worker never reaches the real repo
- workspace is missing `.git`, the expected branch, or repo anchor paths
- worker comments show confusion about missing files before any meaningful diff exists

Operator action:

- treat this as a dispatch failure, not normal worker output
- fix the workflow bootstrap contract or clone assumptions
- requeue the issue only after the assertions match reality
- do not let the same issue immediately redispatch if the workflow has not changed

## Empty workspace

Symptoms:

- worker cannot find repo files
- workspace only contains a lockfile or a partial clone

Operator action:

- inspect the workspace
- verify the clone hook and workspace assertions
- rerun after fixing the workflow
- do not trust a worker that kept reasoning against an empty workspace

## Wrong branch

Symptoms:

- worker reimplements code that already exists
- diffs do not match current branch expectations

Operator action:

- inspect the checked out branch in the workspace
- correct the clone strategy or `required_branch` assertion
- move the issue back to `Todo` if the worker result is not safe to integrate

## No progress

Symptoms:

- worker burns time or tokens without any real workspace diff
- only orchestration noise files change
- retries repeat the same analysis with no output

Operator action:

- stop the run using your no-progress guardrail
- comment a short reason and requeue the issue
- suppress immediate redispatch until the issue body, routing lane, or workflow materially changes
- split the issue or raise the model lane only if the ticket shape actually requires it

## Validation drift

Symptoms:

- worker says it passed checks, but repo-level validation fails
- issue body has weak or missing validation commands

Operator action:

- tighten the issue body
- add exact validation commands
- rerun the issue only after the acceptance criteria are concrete

## Oversized ticket

Symptoms:

- worker touches many unrelated files
- repeated retries with low progress

Operator action:

- split the issue into smaller units
- reduce scope
- keep shared-file changes explicit in `Touched Areas`

## Sensitive data leak

Symptoms:

- issue body, worker comment, validation output, or learnings note contains a secret, token, cookie, personal data, or raw customer payload

Operator action:

- redact the leaked material immediately
- rotate the secret if needed
- replace raw payloads with redacted identifiers or secure references
- update `AGENTS.md`, the issue template, or the runbook if the leak came from a repeatable workflow gap

## Blocked dependency chain

Symptoms:

- ready work is stuck in `Backlog`
- downstream issues start too early or never start

Operator action:

- verify blocker relations in Linear
- confirm the issue body `Dependencies` section matches the actual blocker graph
- only activate the next wave when upstream work is really ready

## After any incident

Operator action:

- add a short entry to `.orchestration/LEARNINGS.md` with the signal, root cause, and fix
- update `.orchestration/RUNBOOK.md` if the same recovery step should happen again
- promote stable lessons into `AGENTS.md`, the workflow, or the issue template
- if the incident involved secrets or personal data, document the prevention step, not the sensitive content itself
