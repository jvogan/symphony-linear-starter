# Release Manager Lane

The Release Manager lane solves a specific failure mode: many agents finish at once, then all try to merge, rebase, and deploy against a moving `main` branch.

The fix is not more deploy-capable workers. The fix is a single writer for `main`.

## Roles

- Implementation workers write code on branches and open PRs.
- Implementation workers never push, rebase, merge, or deploy `main`.
- Workers mark an issue ready by attaching a GitHub PR URL and adding `release:ready`. This is the "deploy" signal at scale — workers self-mark on passing validation; there is no per-PR manual "tell it to deploy" step for the operator.
- The Release Manager lane is the only lane allowed to queue PRs for merge/deploy.
- GitHub Merge Queue is preferred when branch protection supports it.

## State Contract

Recommended Linear states:

- `In Review`: worker output is complete and reviewable.
- `Ready to Merge`: optional explicit release queue state.
- `Merging`: optional state while the Release Manager has queued the PR.
- `Done`: PR is merged and deployment ownership has passed to normal CI/CD.

Recommended labels:

- `sym:<campaign>`: implementation campaign routing label.
- `release:ready`: PR has passed worker validation and may be considered by the Release Manager.

## Worker Closeout

Workers should post an outcome comment with a PR URL:

```markdown
<!-- symphony-outcome
status: success
files_touched: src/foo.ts, tests/foo.test.ts
validation_summary: npm test passed
pr_url: https://github.com/owner/repo/pull/123
suggested_action: release_manager
-->
```

Then the worker adds `release:ready` or moves the issue to `Ready to Merge`, depending on your Linear state model.

## Release Manager Pass

Run dry first:

```bash
python3 skills/symphony-linear-orchestrator/scripts/release_manager.py \
  --workflow .orchestration/release-manager.WORKFLOW.md \
  --json
```

Apply after the dry-run output is sane:

```bash
python3 skills/symphony-linear-orchestrator/scripts/release_manager.py \
  --workflow .orchestration/release-manager.WORKFLOW.md \
  --apply \
  --json
```

The script:

1. Acquires a local lock so only one writer touches `main`.
2. When `release_manager.mode` is `github-merge-queue`, verifies a merge queue is actually enabled on the base branch first. It warns by default, or stops before mutating anything when `require_merge_queue: true`.
3. Finds Linear issues in the ready release states **and** the queued state, so a re-run can finalize anything that merged since the last pass.
4. Requires configured labels such as `release:ready`.
5. Extracts the newest PR URL from `<!-- symphony-outcome -->` or comments.
6. Inspects the PR with `gh pr view` and verifies it targets `release_manager.base_branch`.
7. Moves already-merged issues to `Done`, and conflicted (`DIRTY`) or closed-unmerged PRs to the blocked state for repair. A missing or renamed target state is reported as `misconfigured` (a hard failure that exits non-zero), not silently skipped.
8. Treats an issue already sitting in the queued state with an open PR as **in flight** and never re-enqueues it. This is the idempotency anchor: gh 2.x cannot read `mergeQueueEntry`, so the Linear queued state is the reliable "already handed to the queue" signal — which is why a working `queued_state` is required and a failed move to it is a hard error.
9. Enqueues a fresh ready PR once with `gh pr merge --auto --match-head-commit <sha>` and moves its issue to the queued state.
10. Reconciles already-queued issues every pass (finalize/block) **outside** the `max_per_run` budget so closeout never starves under inflow; surfaces ready work beyond `max_per_run` as `deferred`; marks a failed enqueue `retry`; and reports a machine-readable `drained` flag plus per-status `counts` so a scheduler knows when the burst is fully landed.

For branches requiring GitHub Merge Queue, `gh pr merge --auto` adds passing PRs to the queue or enables auto-merge while checks finish. For branches without a merge queue, this uses GitHub's normal auto-merge behavior — which is serial, so a burst of PRs lands one CI cycle at a time. The next section is how to avoid that.

## Verify and enable GitHub Merge Queue

The merge queue validates a burst of ready PRs in parallel — speculative CI on temporary branches — and merges them in order, instead of you serially rebasing and re-running CI N times. The win is wall-clock and the elimination of the rebase storm, not necessarily fewer CI runs. Without a queue this lane still works but degrades to one-at-a-time auto-merge — so verify it explicitly rather than assuming.

### Availability (check this first)

GitHub merge queue is only available on **organization-owned** repositories:

- **Public** org repos: any plan, including GitHub Free.
- **Private** org repos: **GitHub Enterprise Cloud only** (not Team, not Free).
- **Personal-account repos cannot use a merge queue at all** — the rulesets API returns `403 Upgrade…` / `422 Invalid rule 'merge_queue'`.

If a merge queue is not available for your repo, this lane still runs but lands a burst serially (one CI cycle per PR). Either move the repo under an eligible org or accept serial mode — and let the lane's `--check-merge-queue` gate tell you which one you are in rather than finding out under load.

### Verify

```bash
python3 skills/symphony-linear-orchestrator/scripts/release_manager.py \
  --check-merge-queue --repo OWNER/REPO --base-branch main --json
```

Exit code is `0` only when a queue is enabled. When no queue is found, the check also reports `owner_type`, `private`, `strict_required_checks`, and a one-line `diagnosis` that tells you *which* problem you have:

- **Personal-account repo** (`owner_type: User`) — a merge queue is impossible here, not merely unconfigured. Don't wait for one; move the repo under an org or run serial auto-merge with an explicit `merge_method`.
- **Private org repo** (`owner_type: Organization`, `private: true`) — a merge queue needs **GitHub Enterprise Cloud**; on Team/Free it is unavailable. Make the repo public, upgrade, or accept serial auto-merge.
- **Public org repo, no queue configured** — enable one with the ruleset below.
- **Strict required status checks and no queue** (`strict_required_checks: true`) — the dangerous case: a burst serializes into a **rebase storm**, one PR per CI cycle — the exact failure this lane exists to prevent. Enable a queue (where available), or drop the strict requirement (accepting that PRs then merge without being tested against each other).

Strict detection reads **both** repository rulesets (`/rules/branches`, read-only) and **classic branch protection** (`/branches/.../protection`, needs admin) — classic protection is invisible to the rulesets endpoint, so both are consulted. If neither can be confirmed (e.g. a low-scope token), `strict_required_checks` is `null` and the diagnosis stays conservative: it tells you the fallback could be *either* serial or a rebase storm and to verify the branch rules yourself, rather than asserting the benign outcome.

`preflight.py` runs the same check automatically for `github-merge-queue` workflows and surfaces this `diagnosis` in `release_manager_merge_queue`. Set `require_merge_queue: true` in the workflow to make `--apply` refuse to run until a queue exists.

### Enable (one-time, needs repo admin)

A merge queue is configured with a repository **ruleset** on the base branch. Replace `OWNER/REPO` and the check name, then:

```bash
gh api -X POST repos/OWNER/REPO/rulesets --input - <<'JSON'
{
  "name": "merge-queue-main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": [
    {
      "type": "merge_queue",
      "parameters": {
        "merge_method": "SQUASH",
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 5,
        "min_entries_to_merge": 1,
        "max_entries_to_merge": 5,
        "min_entries_to_merge_wait_minutes": 5,
        "check_response_timeout_minutes": 60
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [ { "context": "ci" } ]
      }
    }
  ]
}
JSON
```

The two knobs that decide burst throughput:

- `max_entries_to_merge` / `max_entries_to_build` — how many queued PRs GitHub validates **together**. With `ALLGREEN` grouping and this set at or above your burst (e.g. `8`), a clean group is validated on one temporary branch and lands in roughly one CI run; a failing group is bisected into more runs to isolate the bad PR. With the default `max_entries_to_merge: 1`, each PR gets its own speculative run — those run in parallel, so wall-clock still drops even though the run count does not.
- `min_entries_to_merge` + `min_entries_to_merge_wait_minutes` — let the queue gather a batch before building, instead of building each arrival alone.

### Make CI run for the queue

Merge-queue batches build on temporary `gh-readonly-queue/*` branches and emit `merge_group` events. Your CI must trigger on them or the queue will wait forever:

```yaml
on:
  pull_request:
    branches: [main]
  merge_group:
```

The `context` in `required_status_checks` above must match the check name your CI reports. If you prefer the UI: **Settings → Rules → Rulesets → New branch ruleset**, target `main`, add **Require merge queue** and **Require status checks to pass**.

## Re-running the lane (drain loop)

One pass is not the whole story for a burst. The lane **enqueues** and exits; GitHub's queue then builds and merges asynchronously. Re-run the same command to:

- finalize PRs that merged since the last pass (the queued-state scan moves them to `Done`),
- pick up issues deferred beyond `max_per_run`,
- move newly-conflicted (`DIRTY`) or closed PRs to the blocked state for repair.

Re-running is safe and idempotent: an issue already in the queued state with an open PR is reported `in_flight` and never re-enqueued. Keep the issue's `release:ready` label until it reaches `Done` so the finalize scan can still match it.

**Stop condition.** Each pass returns `"drained": true` when nothing is left in flight (no queued, in-flight, deferred, or retry items). A scheduler should re-run until `drained` is true, then stop. Exit code is non-zero on a hard failure (required queue missing, or a misconfigured Linear state) or when a pass made zero forward progress; a single PR error does not fail a pass that queued others. Note that exit `0` does **not** mean every PR merged — it means the pass itself was healthy; use `drained` and `counts` for completion.

**Schedule it** on one host or one CI job (the lock is per-host). A minimal drain loop:

```bash
while true; do
  out=$(python3 skills/symphony-linear-orchestrator/scripts/release_manager.py \
          --workflow .orchestration/release-manager.WORKFLOW.md --apply --json)
  rc=$?
  drained=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("drained"))')
  [ "$rc" -ne 0 ] && { echo "release-manager pass unhealthy (exit $rc); stopping for inspection"; break; }
  [ "$drained" = "True" ] && { echo "burst drained"; break; }
  sleep 60
done
```

Stop on a non-zero exit (a hard failure or a pass that made no progress) as well as on `drained` — do not key the loop on `drained` alone, or a persistently failing pass would either spin or, worse, be discarded.

**Eviction.** If the queue evicts a PR because its checks failed, the lane leaves it `in_flight` (it cannot distinguish "still building" from "evicted" via gh) — the PR author fixes CI and the queue retries it. Only merge conflicts and closed PRs are routed to the blocked state. Watch the `in_flight` count across passes: if it stops dropping, a PR is stuck — inspect the queue on GitHub.

If a repository does not use a GitHub Merge Queue, the lane **requires** `release_manager.merge_method` (`merge`, `squash`, or `rebase`); otherwise `gh pr merge --auto` has no method to apply and fails non-interactively in CI.

Set `release_manager.comment_mode` to control Linear comments:

- `minimal` posts only a short status and PR URL.
- `none` suppresses release comments entirely.
- `verbose` posts the fuller operational messages.

## Benefits

- Prevents parallel agents from repeatedly invalidating each other's base commit.
- Keeps coding parallel while making `main` single-writer.
- Turns merge conflicts into repair tickets instead of blocking the whole queue.
- Makes deploy behavior auditable in Linear and GitHub.
- Lets the orchestrator scale worker count without scaling merge contention.

## Tradeoffs

- `main` is still serialized. If CI takes two minutes and every PR needs unique CI, eight PRs cannot all land in two minutes unless your merge queue batches (raise `max_entries_to_merge`) or your CI is faster.
- GitHub Merge Queue needs a ruleset and CI wired to `merge_group` events (see "Verify and enable" above). The lane verifies this and warns — or blocks, with `require_merge_queue: true` — when it is missing, so the degradation is never silent.
- Batching improves throughput but makes a failed batch harder to isolate.
- The Release Manager lane becomes critical infrastructure, so keep it small, deterministic, and dry-run friendly.
- Broad PRs touching shared files still create real integration bottlenecks; split or serialize those issues with Linear blockers.

## Guardrails

- Keep `max_concurrent_agents: 1` for Release Manager workflows.
- Run the lane from one place. The lock is per-host, so a second machine running `--apply` against the same project would be a second writer. Schedule it on one host or one CI job.
- Use `--apply` only in trusted local or CI contexts with known `gh` auth.
- Prefer private repos for first live tests.
- Never let implementation workers run the Release Manager command.
- Workers must not force-push or amend a PR branch after marking it `release:ready`. A force-push to a PR already in the queue can land stale CI, and the lane cannot detect it once it is enqueued.
- A blocked issue stays out of the queue until a worker repairs it and moves it back to a ready state. The lane never auto-rebases or auto-repairs a conflicted PR.
- A `queued_state` distinct from your ready states is required for idempotency; the lane refuses to start if they overlap, and hard-fails (`misconfigured`) if it cannot move an issue into the configured done/blocked/queued state. If you run more than one Release Manager campaign against the same Linear project, give each a distinct `queued_state` — the lane reconciles every issue in its queued state regardless of label.
- Keep branch protection enabled; do not use `gh pr merge --admin` in automation.
