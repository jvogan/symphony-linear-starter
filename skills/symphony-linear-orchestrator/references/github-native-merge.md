# GitHub-native auto-merge (no Linear)

The Release Manager lane is Linear-driven: it reads Linear issues, moves them
through states, and finalizes closeout. If you are **not** using Linear (or
Symphony) and only want hands-off, batched merging on plain GitHub, you do not
need the lane at all -- GitHub's own auto-merge plus a merge queue is enough.

A ready-to-copy sample lives at
`assets/examples/auto-merge-on-label.yml`.

## When to use this instead of the lane

| You want… | Use |
|---|---|
| Issue tracking, orchestration, audit trail, multi-step closeout | the **Release Manager lane** (`release_manager.py`, Linear-driven) |
| Just "label a PR, land it when green," no Linear | the **GitHub-native sample** below |

This path trades away everything Linear gives you: there is no issue closeout, no
`Done` transition, no drain reconciliation, and no audit record beyond GitHub
itself. In exchange it is ~20 lines and fully event-driven.

## How it works

`on: pull_request: [labeled]` fires when a PR gets the `release:ready` label. The
job runs `gh pr merge --auto`, which enables auto-merge on that PR -- so it joins
the repository's **merge queue** (if one is configured) and lands when its checks
are green. The queue validates a burst of labeled PRs together and merges them in
order, exactly as it does for the lane.

Because it only flips auto-merge on **one PR at a time**, it has **no
single-writer concern** and needs no concurrency lock: enabling auto-merge is
idempotent, and the merge queue does the serialization. This is the structural
reason it is simpler than the lane.

## Prerequisites

1. **Allow auto-merge**: Settings → General → "Allow auto-merge".
2. **A merge queue** (recommended) so a burst batches instead of serializing --
   see [release-manager-lane.md](release-manager-lane.md), "Verify and enable
   GitHub Merge Queue". Without a queue this still works, but lands serially (one
   CI cycle per PR). **If the queue has required checks on `merge_group`, also set
   an `AUTOMERGE_PAT` secret** (see "Token cascade" in Caveats) or queued PRs never
   merge.
3. **A `release:ready` label** on the repo (this is a *GitHub PR label* here, not
   a Linear label -- a different thing from the lane's `release:ready`).
4. **The merge method the sample uses must be enabled.** The sample runs
   `gh pr merge --auto --squash`; if squash merging is disabled (Settings →
   General → "Allow squash merging"), the first labeled PR errors red. Match the
   `--auto --<method>` flag to a method your repo allows.

Then copy `assets/examples/auto-merge-on-label.yml` to
`.github/workflows/auto-merge-on-label.yml` and commit it.

## Caveats

- **Label after the PR is ready for review.** The sample skips drafts, because
  auto-merge rejects a draft PR. A PR labeled while draft will not be picked up
  again automatically; re-apply the label once it is ready.
- **Merge method must match repo settings.** The sample uses `--squash`; change
  it to a method your repo allows (`merge`, `squash`, or `rebase`). With a merge
  queue, the queue's configured method applies regardless.
- **Token cascade -- this one bites with a merge queue.** Events from the default
  `GITHUB_TOKEN` do not cascade, so an enqueue done with `GITHUB_TOKEN` does **not**
  trigger the queue's `merge_group` CI. If your queue has required checks that run
  on `merge_group`, those checks never start and the PRs sit in the queue until it
  times out -- they never merge. Enqueue with a **PAT** instead: set a
  repo or fine-grained PAT secret with contents + pull-requests write named
  `AUTOMERGE_PAT`, and the sample uses it automatically
  (`secrets.AUTOMERGE_PAT || secrets.GITHUB_TOKEN`). Plain `GITHUB_TOKEN` is fine
  only with no merge queue (the serial fallback), where there is no `merge_group`
  CI to trigger.
- **No closeout.** Nothing updates an external tracker. If you later adopt Linear,
  switch to the Release Manager lane for issue state and audit.
