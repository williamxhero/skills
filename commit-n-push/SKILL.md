---
name: "IN: Commit n Push"
description: Commit every current change in a Git project and every project-owned nested Git repository, synchronize each branch by fetching and normally merging its upstream, then push and verify local/remote equality. Use when the user asks to commit and push a whole project, pull-merge-push, synchronize all subrepositories, include changes from other threads, or says commit n push.
---

# Commit, synchronize, and push the whole project

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

## 2. Preflight each repository

For every selected repository, record:

- current branch or detached-HEAD state;
- complete porcelain status, including untracked files and deletions;
- remotes and upstream tracking;
- worktree list;
- unresolved merge, rebase, cherry-pick, or conflict state.

Stop that repository before committing when it has unresolved conflicts, an in-progress history operation, or detached HEAD. Continue with independent repositories and report the blocker. Never force-push or rewrite history as part of this skill.

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

## 4. Fetch and merge upstream

For every selected repository that has at least one remote:

1. Resolve the current branch's configured upstream. If none exists and exactly one suitable remote exists, select its same-named branch when present; otherwise establish the upstream on the first push. Stop when multiple destinations remain ambiguous.
2. Fetch the selected remote and inspect the local/upstream merge bases. If an upstream branch exists, merge it into the committed local branch with a normal non-rebase merge. A fast-forward is valid; divergence creates a merge commit.
3. On conflicts, preserve both sides and apply `resolving-merge-conflicts` when available. Resolve only when repository intent is discoverable and tests can verify the result; otherwise stop that repository with the conflict state intact for recovery.
4. Run the relevant validation after a content-bearing merge. Account for hook or merge-generated changes and commit them before push.

Never use rebase, force push, history rewriting, or a strategy that discards either side. Fetch failure, unresolved conflict, failed validation, or ambiguous upstream blocks that repository.

## 5. Push synchronized repositories

1. Push the current branch normally to its resolved upstream. If the upstream did not exist, use the one unambiguous remote and establish tracking.
2. If push is rejected because the remote advanced, fetch and perform the same normal merge loop again, then revalidate and retry once. Route persistent network, permission, protection, or conflict failures as blockers.
3. Fetch once more after push and resolve the remote-tracking ref. Record the local `HEAD`, remote-tracking SHA, merge base, and evidence.
4. Require local `HEAD` and the remote-tracking SHA to be identical. “Push command exited zero” without this equality check is not synchronized success.

For repositories without remotes, keep the local commit and report `committed, not pushed: no remote`.

## 6. Verify completion

After all attempts, re-read every selected repository:

- `git status --short --branch` shows no unaccounted changes; repository-approved ignored files do not count;
- each created commit is visible at `HEAD`;
- each repository with a remote has identical local and remote-tracking HEADs after the final fetch;
- repositories without remotes retain their new local commits;
- excluded candidates and blocked repositories are listed with reasons.

Report per repository: path, branch, commit SHA/message or `no changes`, upstream, merge result, local HEAD, remote-tracking HEAD, push result, and residual status. Emit product Git commit/push directives only for actions that actually succeeded.

## Safety boundaries

- Commit all changes means all changes inside each selected project repository; it does not convert dependency caches or temporary clones into project repositories.
- Preserve repository policy even when it requires exact-path staging or forbids particular generated or sensitive paths.
- Never expose or commit detected credentials. Stop the affected repository and identify the path without printing the secret.
- Never use destructive cleanup, reset, checkout discard, branch deletion, recursive deletion, rebase, force push, or history rewriting.
- Do not claim whole-project success while any selected repository has uncommitted changes, an unpushed commit despite having an unambiguous remote, or an unresolved blocker.
