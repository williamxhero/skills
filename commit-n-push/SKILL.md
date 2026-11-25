---
name: "Commit n Push"
description: Commit every current change in a Git project and every project-owned nested Git repository, synchronize branches, and push with local/remote equality verification. When the user explicitly asks to consolidate branches, merge every eligible branch into the repository default branch and delete only branches proven fully merged. Use when the user asks to commit and push a whole project, pull-merge-push, synchronize all subrepositories, include changes from other threads, or says commit n push.
---

# Commit, consolidate, and push the whole project

When called by `implement-needs`, return structured repository results containing the
selected repository, preflight, fetch, merge, push, post-push readback, and local/remote
equality evidence. The controller records these observations before completing release.

Treat the requested project tree as the scope. Commit all current changes in every selected repository, regardless of which task or author created them. Synchronize and push every repository with an unambiguous remote.

## 1. Discover repositories

1. Resolve the project root from the active workspace or the path named by the user.
2. Find the root repository, if present, and every nested Git repository beneath it. Detect both `.git` directories and `.git` files.
3. Canonicalize each repository root and process it once. Treat linked worktrees as additional checkouts of their owning repository, not automatically as separate subprojects.
4. Classify candidates before writing. Include project-owned source repositories. Exclude dependency mirrors, package caches, generated deployment copies, temporary clones, and runtime worktrees unless the user explicitly includes them or project instructions identify them as source repositories.
5. Read applicable `AGENTS.md` or equivalent repository instructions for every selected repository.

Report the selected and excluded repositories before staging. Continue without asking when classification is unambiguous.

If the request explicitly asks to merge all branches into the main/default branch or to delete merged branches, enter **branch-consolidation mode**. A plain commit-and-push request keeps the current-branch/upstream workflow and does not delete branches.

## 2. Preflight each repository

For every selected repository, record:

- current branch or detached-HEAD state;
- complete porcelain status, including untracked files and deletions;
- remotes and upstream tracking;
- worktree list;
- repository default branch, all local branches, and remote-tracking branch tips;
- branches checked out by another worktree and any available protection/open-PR information;
- unresolved merge, rebase, cherry-pick, or conflict state.

Stop that repository before committing when it has unresolved conflicts, an in-progress history operation, or detached HEAD. Continue with independent repositories and report the blocker. Never force-push or rewrite history as part of this skill.

In branch-consolidation mode, resolve the default branch from the selected remote's symbolic `HEAD` first. Fall back to `main` or `master` only when exactly one exists and no remote default is available. Stop when the default branch is ambiguous, protected in a way that prevents the requested operation, or checked out by another worktree that cannot be safely used.

Inspect untracked directories large enough to plausibly be generated output, secrets, dependencies, caches, or runtime state. Apply repository instructions and ignore rules. If committing such material would be unsafe or clearly outside source ownership, stop that repository and request direction instead of silently omitting part of the user's requested changes.

## 3. Commit all changes

For each selected repository with changes:

1. Review the full diff and untracked-file inventory sufficiently to write an accurate commit message and detect credentials or obviously accidental generated artifacts.
2. Stage every intended current change: tracked edits, deletions, renames, and untracked files. The unit is the repository's complete working state, not this thread's diff.
3. Follow repository staging rules. Use all-change staging only when allowed; when exact-path staging is required, enumerate every changed path explicitly and include deletions with an appropriate exact-path command.
4. Run `git diff --cached --name-only` and compare it with the preflight status. Account for every change. A partial staged set is not success.
5. Commit once with a concise message that describes the combined change. If the changes are genuinely unrelated but the user requested one whole-state operation, prefer a neutral snapshot-style message over inventing a false theme.
6. If hooks modify files or reject the commit, inspect the resulting state, stage all resulting in-scope changes, rerun the relevant checks, and retry once. Stop on repeated failure and preserve the working tree for recovery.

Repositories with no changes require no empty commit.

## 4. Fetch and merge branches

For every selected repository that has at least one remote:

1. Resolve the canonical remote. Prefer the remote that tracks the current branch; otherwise use the only configured remote. Stop when multiple candidate remotes or destinations remain ambiguous. Fetch every configured remote and inspect local/upstream merge bases.
2. In branch-consolidation mode, switch to the resolved default branch after the current working state has been committed. Process every eligible non-default local branch and every remote-tracking branch that has no local counterpart. A branch is eligible only when it is not the default branch, is not checked out by another worktree, and is not protected or blocked by an available provider check.
3. For each eligible branch whose tip is not already an ancestor of the default branch, merge that branch tip into the default branch with a normal non-rebase merge. A fast-forward is valid; divergence creates a merge commit. Never use a strategy that discards either side.
4. When a branch tip is already an ancestor, record it as merge-complete. If a local branch tracks a remote branch, require both tips to be accounted for before marking the remote branch merge-complete; do not discard commits that exist only on the remote.
5. On conflicts, preserve both sides and apply `resolving-merge-conflicts` when available. Resolve only when repository intent is discoverable and tests can verify the result; otherwise stop that repository with the conflict state intact for recovery.
6. Run the relevant validation after each content-bearing merge. Account for hook or merge-generated changes and commit them before push.

Never use rebase, force push, history rewriting, or a strategy that discards either side. Fetch failure, unresolved conflict, failed validation, ambiguous upstream, or an unsafe branch checkout blocks that repository.

## 5. Push synchronized repositories

1. Push the default branch normally to its resolved upstream. In ordinary mode, push the current branch instead. If the upstream did not exist, use the one unambiguous remote and establish tracking.
2. In branch-consolidation mode, push any retained non-default local branches to their resolved upstreams before cleanup. Do not push a branch that is scheduled for deletion unless doing so is required to publish the final merged tip and the deletion is then verified.
3. If push is rejected because the remote advanced, fetch and perform the same normal merge loop again, then revalidate and retry once. Route persistent network, permission, protection, or conflict failures as blockers.
4. Fetch once more after push and resolve the remote-tracking refs. Record the default-branch `HEAD`, every retained branch tip, merge bases, and evidence.
5. Require each pushed local branch and its remote-tracking SHA to be identical. “Push command exited zero” without this equality check is not synchronized success.

For repositories without remotes, keep the local commit and report `committed, not pushed: no remote`.

## 6. Delete proven merged branches

Run this section only in branch-consolidation mode and only after the default branch has been pushed successfully.

1. Re-check every candidate branch with `git merge-base --is-ancestor <branch-tip> <default-branch>`. Treat the check as the deletion gate; a branch with any unique commit remains and is reported.
2. Never delete the default branch, a branch checked out by another worktree, a protected branch, or a branch with an unresolved provider/open-PR blocker. Never use `git branch -D`.
3. Delete each proven merged local branch with safe deletion (`git branch -d`). For a corresponding remote branch, delete it with `git push <remote> --delete <branch>` only after verifying that the remote tip is also an ancestor of the pushed default branch. A rejected protected-branch deletion is a blocker, not permission to force it.
4. Fetch the affected remote refs again and verify that deleted local and remote branch refs are absent, while the default branch and retained branches still point to the recorded SHAs.
5. Report every skipped, retained, deleted-local, and deleted-remote branch with its merge-proof result and reason.

## 7. Verify completion

After all attempts, re-read every selected repository:

- `git status --short --branch` shows no unaccounted changes; repository-approved ignored files do not count;
- each created commit is visible at `HEAD`;
- each repository with a remote has identical local and remote-tracking HEADs after the final fetch;
- in branch-consolidation mode, the default branch is pushed and every deleted branch has an explicit ancestor proof plus a verified absent ref;
- repositories without remotes retain their new local commits;
- excluded candidates and blocked repositories are listed with reasons.

Report per repository: path, default/current branch, commit SHA/message or `no changes`, upstream, branch merge results, deleted/retained branches, local HEAD, remote-tracking HEAD, push result, and residual status. Emit product Git commit/push directives only for actions that actually succeeded.

## Safety boundaries

- Commit all changes means all changes inside each selected project repository; it does not convert dependency caches or temporary clones into project repositories.
- Preserve repository policy even when it requires exact-path staging or forbids particular generated or sensitive paths.
- Never expose or commit detected credentials. Stop the affected repository and identify the path without printing the secret.
- Branch deletion is allowed only in explicit branch-consolidation mode, after a fresh ancestor proof and the checks in Section 6. Use safe local deletion and normal remote deletion; never force deletion.
- Never use destructive cleanup, reset, checkout discard, recursive deletion, rebase, force push, or history rewriting.
- Never infer branch-consolidation mode from an ordinary commit/push request, and never delete an unmerged, protected, checked-out, or ambiguous branch.
- Do not claim whole-project success while any selected repository has uncommitted changes, an unpushed commit despite having an unambiguous remote, or an unresolved blocker.

## After-use review: keep only reusable lessons

After the per-repository report is delivered, spend a few lines on this run: what the user corrected, which step was misread or skipped, which gate failed to catch something, where work was redone, which inputs should have been asked for first.

- **Qualify before writing.** The lesson must recur on a similar task and fit one actionable "when you see X, do Y" constraint. Skip one-off environment failures, facts specific to this run, unverified guesses, and anything an existing rule already covers.
- **Write it into this skill.** Prefer merging it into an existing step, gate, or trigger over adding another bullet; if the same lesson shows up again, restructure instead of appending more sentences. Delete requirements this run disproved; leave no contradictory bullet behind.
- **Edit only the copy actually loaded this run** — its `SKILL.md` or the references shipped in this directory. When another tool manages that copy (for example a CC Switch managed copy), edit its maintenance source and state that the change is not installed; never overwrite the managed copy. Touch pinned upstream or specialist assets only through their adapter; do not import another skill's rules, create a second source of truth, or write secrets and private paths.
- **Do not change this run's outcome.** The review grants no extra authorization: no commit, push, install, deploy, or rewritten evidence.
- **Otherwise pass.** No file edit, no changelog entry, no version bump. At most one closing line — "sedimented: …" or "nothing to sediment" — and do not replay the review.
