# Recovery Playbook

## Empty workspace

Symptoms:

- worker cannot find repo files
- workspace only contains a lockfile or a partial clone

Operator action:

- inspect the workspace
- verify the clone hook
- rerun after fixing the workflow
- do not trust a worker that kept reasoning against an empty workspace

## Wrong branch

Symptoms:

- worker reimplements code that already exists
- diffs do not match current branch expectations

Operator action:

- inspect the checked out branch in the workspace
- correct the clone strategy
- move the issue back to `Todo` if the worker result is not safe to integrate

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

## Blocked dependency chain

Symptoms:

- ready work is stuck in `Backlog`
- downstream issues start too early or never start

Operator action:

- verify blocker relations in Linear
- confirm the issue body `Dependencies` section matches the actual blocker graph
- only activate the next wave when upstream work is really ready

