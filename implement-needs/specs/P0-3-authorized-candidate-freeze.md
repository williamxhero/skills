# SPEC P0-3: 授权范围、候选冻结与最终同步一致性

## Problem Statement

自动决策和最终同步没有共同的安全边界。现有同步能力可能提交当前工作区所有改动、合并新的远端内容，且发布发生在同步之前，导致测试和部署 SHA-A、最终推送 SHA-B 而完成结论仍沿用 SHA-A。不同任务的改动也可能被意外纳入当前交付。

## Solution

在运行启动时冻结仓库身份、目标分支、允许动作、部署环境、文件/任务范围和整项目提交授权。先完成所有会改变候选内容的提交与合并，再冻结候选 SHA；测试、打包、部署、同步和最终报告都必须绑定同一候选。任何候选变化都使旧验证失效并重新产生验证义务。默认只同步本运行授权范围内的改动。

## User Stories

1. As a user, I want the run to record its repository and branch scope, so that later automation cannot silently switch targets.
2. As a controller, I want allowed actions and deployment environments frozen at startup, so that autonomous choices remain within authorization.
3. As a maintainer, I want ordinary implementation delivery separated from full-project snapshot submission, so that broad side effects require explicit authorization.
4. As a release verifier, I want one candidate SHA shared by tests, package, deployment, and synchronization, so that the released artifact is the verified artifact.
5. As a user, I want unrelated uncommitted changes preserved, so that another task's work is not committed under my run.
6. As a supervisor, I want remote advancement or merge-created commits to invalidate the old candidate, so that stale release evidence cannot be reused.
7. As a controller, I want candidate changes to produce a new validation obligation automatically, so that the workflow cannot close around a moving target.
8. As an auditor, I want the final report to show scope, candidate SHA, merge SHA, deployment identity, and synchronization result, so that delivery can be independently reconstructed.
9. As an operator, I want unauthorized scope expansion to block with a clear authorization error, so that it is not misclassified as a technical repair.
10. As a reviewer, I want the final sync check to confirm both repository equality and candidate identity, so that equality alone is not treated as release proof.

## Implementation Decisions

- Add an immutable run authorization record containing repository identity, branch/ref, allowed path/task scope, permitted external actions, deployment target, and full-project submission permission.
- Make automatic decisions valid only when they preserve scope, acceptance conditions, credentials, and destructive-action boundaries; record the decision source and rationale.
- Split normal run-scoped synchronization from explicitly authorized full-project commit-and-push mode.
- Define candidate freeze after all candidate-changing commits, merges, and generated artifacts are complete.
- Bind every required test, package, deployment, push, and final readback receipt to the frozen candidate SHA and run authorization.
- Detect any post-freeze content change, remote merge, or artifact mismatch and return the run to validation rather than preserving the old completion result.
- Keep user changes outside scope untouched and report them as preserved/unrelated rather than silently including them.

## Testing Decisions

- Construct worktrees containing unrelated modifications and assert they are not committed in normal mode.
- Construct a remote-advanced and merge-created-new-SHA scenario and assert that old test/deployment evidence is rejected.
- Verify that a stable candidate produces matching test, package, deployment, sync, and final report identities.
- Verify unauthorized branch, environment, path, deployment, and full-project operations are blocked before side effects.
- Test explicit full-project authorization separately from the default run-scoped path.
- Assert the final repository-equality check is insufficient when candidate identity differs.
- Use scope/release tests and commit-and-push contract tests as prior art without mutating a real production target.

## Out of Scope

- Implementing the full external action lifecycle and fencing, which is P1-2.
- Changing repository hosting or branch strategy.
- Granting new credentials or deployment permissions.

## Further Notes

The required invariant is: verified candidate SHA = package source SHA = deployed candidate SHA = final synchronized delivery SHA. If any equality fails, the run must be non-terminal until a new candidate is validated. The implementation must remain safe in a dirty worktree and on a remote branch that advances during the run.
