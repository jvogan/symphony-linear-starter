# Release Manager Lane

The Release Manager lane solves a specific failure mode: many agents finish at once, then all try to merge, rebase, and deploy against a moving `main` branch.

The fix is not more deploy-capable workers. The fix is a single writer for `main`.

## Roles

- Implementation workers write code on branches and open PRs.
- Implementation workers never push, rebase, merge, or deploy `main`.
- Workers mark an issue ready by attaching a GitHub PR URL and adding `release:ready`.
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

1. Acquires a local lock.
2. Finds Linear issues in ready release states.
3. Requires configured labels such as `release:ready`.
4. Extracts the newest PR URL from `<!-- symphony-outcome -->` or comments.
5. Inspects the PR with `gh pr view`.
6. Verifies the PR targets the configured `release_manager.base_branch`.
7. Uses `gh pr merge --auto --match-head-commit <sha>`.
8. Moves already-merged issues to `Done`.
9. Returns conflicted or closed-unmerged PRs to the configured blocked state.

For branches requiring GitHub Merge Queue, `gh pr merge --auto` adds passing PRs to the queue or enables auto-merge while checks finish. For branches without a merge queue, this uses GitHub's normal auto-merge behavior.

If a repository does not use a GitHub Merge Queue and its settings require an explicit merge strategy, set `release_manager.merge_method` to `merge`, `squash`, or `rebase`.

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

- `main` is still serialized. If CI takes two minutes and every PR needs unique CI, eight PRs cannot all land in two minutes unless your merge queue batches or your CI is faster.
- GitHub Merge Queue needs branch protection and CI configured for merge-queue events.
- Batching improves throughput but makes a failed batch harder to isolate.
- The Release Manager lane becomes critical infrastructure, so keep it small, deterministic, and dry-run friendly.
- Broad PRs touching shared files still create real integration bottlenecks; split or serialize those issues with Linear blockers.

## Guardrails

- Keep `max_concurrent_agents: 1` for Release Manager workflows.
- Use `--apply` only in trusted local or CI contexts with known `gh` auth.
- Prefer private repos for first live tests.
- Never let implementation workers run the Release Manager command.
- Keep branch protection enabled; do not use `gh pr merge --admin` in automation.
