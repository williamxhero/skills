# SPEC P1-7: 控制器续接、线程清理、路由可见性与全面资格测试

## Problem Statement

`implement-needs` 的持久化状态能够在部分中断后推导出下一动作，但控制器在子 SPEC 完成、父 turn 因模型容量失败或宿主返回空 turn 后，可能没有把下一动作写入运行状态，也没有自动重新唤醒。因此运行会停留在 `active / implementing`，`current_action` 为空，用户只能手动纠正流程。

同一现场还暴露出线程生命周期缺口：Grill、planning 或 probe 线程在阶段已经离开后仍可能保持 `working`、`idle` 或 `notLoaded`，而没有统一执行 archive operation 和 archive readback。测试覆盖了单独的 bootstrap、route 和 archive 合同，却没有模拟“前一 SPEC 关闭后控制器进程中断、外层模型容量失败、残留 planning 线程存在、下一 SPEC 可执行”的完整状态组合。

另外，规划流程已经为每个 SPEC 预测和验证 model + effort，但这些信息主要存在于内部 planning record、route receipt 和 thread registry，SPEC 摘要本身没有可读的 route summary。用户无法确认某个 SPEC 将使用什么实际路由，测试也没有强制验证该信息从 SPEC 分解一路传递到创建任务、应用路由、状态展示、恢复和最终报告。

## Solution

建立一个可恢复、可观察、可资格验证的控制器闭环。每个 active run 都必须拥有一个持久化的 current action、recovery action 或 terminal result；控制器 turn 在任意位置中断后，下一次启动会通过 reconciliation 自动恢复，而不是依赖隐式继续消息。

所有 run-owned 的 Grill、planning、probe、implementation 和 repair 线程都进入统一生命周期收尾。阶段结束、任务完成、任务取消、异常和进程中断都必须执行 archive operation，并独立读取归档状态；`idle`、`completed`、`notLoaded` 或最后消息不能被当作已归档。

规划阶段为每个 SPEC 生成可读的 route summary，包括预测 model、effort、选择依据、已批准 fallback 和 route evidence。创建 implementation task 后，controller 必须读取实际 applied route，并把它与 SPEC route summary、route receipt、thread registry、controller status 和 qualification report 交叉验证。ticket 继承所属 SPEC 的 route，不创建独立的 ticket route。

增加从规划到最终释放的多 SPEC qualification 测试，覆盖正常流程、宿主中断、模型容量失败、空 turn、通知延迟、历史暂未落盘、残留线程、重复创建、route drift、archive readback 缺失、恢复预算耗尽和最终报告完整性。

## User Stories

1. As a project owner, I want an active run to always have a persisted next action or recovery action, so that a controller failure cannot leave the workflow silently idle.
2. As a controller, I want to reconstruct the next action after a process or model turn failure, so that recovery does not require a user to restate the request.
3. As a controller, I want a stale active run with an empty current action to become an explicit `controller_interrupted` recovery state, so that the condition is observable and testable.
4. As a controller, I want the next action to be written atomically with the child completion observation, so that a completed SPEC cannot lose the wake-up for the next SPEC.
5. As an operator, I want recovery to use the persisted run and thread identities, so that it cannot create duplicate implementation tasks.
6. As an operator, I want model capacity failures to create a bounded recovery action with a verified fallback route, so that the run either resumes or reports a precise blocker.
7. As an operator, I want transport failures and empty turn responses to be distinguishable from terminal child failures, so that the controller chooses continue, reconcile, replacement, or repair correctly.
8. As a controller, I want every run-owned Grill thread to be finalized when planning ends, so that planning work cannot remain active during implementation.
9. As a controller, I want every run-owned probe thread to be archived in a finally cleanup path, so that a capability qualification cannot leak a live thread.
10. As a controller, I want an idle or not-loaded thread to remain unarchived until archive readback succeeds, so that lifecycle state reflects evidence rather than appearance.
11. As a controller, I want cleanup failure to become an explicit next action, so that a failed archive cannot disappear from the workflow.
12. As a controller, I want reconciliation to detect registry threads absent from the host inventory and host threads absent from the registry, so that both orphan directions are handled.
13. As a planner, I want each SPEC to have a predicted model and effort before implementation task creation, so that route selection is part of the planning contract.
14. As a planner, I want route prediction to use whole-SPEC difficulty, risk, coupling, ambiguity and verification burden, so that ticket count does not replace implementation complexity.
15. As a project owner, I want the SPEC summary to show its predicted model, effort, selection, fallback policy and evidence reference, so that the planned route is visible before execution.
16. As a controller, I want the created SPEC task's applied model and effort read back independently, so that requested settings are never presented as actual settings.
17. As a controller, I want route drift to block assignment and recovery, so that a task cannot run under an unverified route.
18. As an auditor, I want the route summary, route receipt, applied readback and execution evidence stored separately, so that requested, configured and executed values are not conflated.
19. As a ticket owner, I want tickets to inherit their SPEC route, so that ticket-level route changes cannot create an untracked implementation boundary.
20. As a user, I want the controller status at SPEC entry and recovery to show the actual applied model, effort and next action, so that progress is understandable.
21. As a test author, I want a regression for `SPEC closed + next SPEC ready + current_action empty`, so that the observed lost wake-up cannot return.
22. As a test author, I want a regression for a residual Grill thread in `working`, `idle` and `notLoaded` states, so that cleanup behavior is verified at the controller seam.
23. As a test author, I want a regression for model capacity failure followed by a verified fallback attempt, so that replacement ordering and archive evidence are enforced.
24. As a test author, I want a regression for a controller turn ending before its receipt is written, so that restart recovery can identify the incomplete action.
25. As a test author, I want a regression for a completed turn whose persisted history is initially empty and later appears, so that temporary persistence races do not cause premature failure.
26. As a test author, I want a regression for history that remains empty after bounded retries, so that the controller fails explicitly instead of waiting forever.
27. As a test author, I want a multi-SPEC flow with dependencies, route summaries, implementation handoffs, archives and release evidence, so that unit tests cannot hide orchestration gaps.
28. As a test author, I want qualification to reject missing route summaries, missing applied route readbacks and mismatched route identities, so that reports cannot claim complete coverage.
29. As a test author, I want qualification to reject missing cleanup receipts and orphan run-owned threads, so that lifecycle leaks are visible before normal use.
30. As a project owner, I want test output to identify the exact failed invariant, state, thread, route or receipt, so that fixing a failure does not require manual conversation debugging.
31. As a release owner, I want the final qualification report to include controller recovery, thread cleanup, route visibility, model/effort readbacks and multi-SPEC progression, so that a green report means the whole workflow was exercised.
32. As a maintainer, I want the tests to use the highest reusable controller seam, so that new backends do not require duplicating every orchestration scenario.

## Implementation Decisions

- Treat the controller kernel and its persisted SQLite state as the highest testing seam. Backend protocol tests remain contract tests; qualification tests exercise the normalized controller boundary.
- Introduce an explicit controller continuation state for an active run with no current action. Reconciliation must convert it into a durable recovery action before any new external mutation.
- Compute and persist the next action after every terminal child observation in the same business-versioned state transition. A run may not return to the outer workflow with an active state and no action.
- Add a stale-controller watchdog based on persisted `updated_at`, business version, current action, recovery action and terminal result. The watchdog must be bounded and idempotent.
- Make thread cleanup phase-aware. Leaving grilling or planning requires finalizing all run-owned planning helpers; qualification-only probes and abandoned bootstrap tasks use the same cleanup path.
- Define cleanup as `archive operation → archive readback → registry transition`. A missing or false readback remains a repair action and cannot be treated as successful cleanup.
- Extend reconciliation to classify orphan, stale-working, not-loaded, idle, completed-but-unarchived and absent-host states. Each classification has one deterministic next action.
- Preserve formal thread identity, host identity, task identity, run identity and attempt identity. URL, request ID, title token and message text remain candidate indexes only.
- Add a route summary to the SPEC planning artifact. It contains the whole-SPEC prediction, selected model, selected effort, approved fallbacks, route selection and evidence references.
- Keep route planning and route execution separate. The planner predicts and locks an allowed route; task creation reads the applied route; controller status and qualification use the applied readback.
- Make the route summary mandatory for implementation dispatch. Missing, stale, mismatched or ticket-level-only route evidence blocks dispatch.
- Tickets inherit the route of their owning SPEC. A ticket cannot mint an independent model, effort, task, thread or route receipt in whole-SPEC mode.
- Integrate managed turn failure classification with the outer controller action surface. Capacity, stream disconnect, uncertain side effect, terminal failure and blocked identity each produce a bounded recovery action.
- Preserve the existing same-thread checkpoint continue rule for stream and uncertain failures. Completed turns remain non-replayable.
- Keep fallback replacement ordering strict: archive old attempt, verify archive readback, verify fallback applied route, create a new attempt, then send assignment.
- Add an explicit report section for route visibility and lifecycle cleanup. The report must distinguish requested route, applied configured route and turn-level execution evidence.
- Do not infer completion from an agent final, an idle status, an empty turn list, or an archive operation response without readback.

## Testing Decisions

- Test external controller behavior through persisted inputs and outputs: next action, lifecycle, receipts, route summaries, recovery records and qualification decisions.
- Add unit tests for the continuation invariant, stale-controller detection, deterministic orphan classification, phase-aware cleanup, route-summary validation and route inheritance.
- Add state-machine tests for every interruption boundary: before action claim, after claim, after external operation, after child completion, before next-action persistence and after archive request.
- Add integration tests using a fake normalized backend that can emit model capacity failures, transport disconnects, delayed notifications, empty history, late history, route drift, absent threads and archive readback failures.
- Add a multi-SPEC scenario with at least three SPECs, dependency edges, different predicted routes, one capacity fallback, one controller interruption, one residual planning thread and a final release gate.
- Assert that every recovery action is idempotent, bounded and tied to the original formal identity. Assert that no duplicate task or thread is created while the original is recoverable.
- Assert that requested route, configured route and execution evidence remain separate and that a requested pair cannot satisfy an applied-route gate.
- Assert that a SPEC without a route summary cannot dispatch, a ticket cannot override its SPEC route, and a route mismatch blocks assignment.
- Assert that `working`, `idle`, `completed` and `notLoaded` are all unarchived observations until archive readback confirms `archived: true`.
- Assert that an active run with no current action is never reported as waiting or successful; it must yield `controller_interrupted` or a concrete recovery action.
- Add qualification tests for complete reports and negative reports missing route summary, cleanup receipt, recovery result, task census, archive readback or repository evidence.
- Add a live lifecycle probe only in qualification mode. Normal capability selection must remain dry-run and must not create a capability-probe thread.
- Reuse the existing controller lifecycle, task backend, app-server bridge, managed recovery, validation and test-release-train test suites as prior art.
- Run fast controller and backend tests on every change, the full implement-needs test suite before merge, and the whole-spec qualification harness after the integrated scenario passes.

## Out of Scope

- Changing the Codex app-server protocol or Desktop task API.
- Moving the whole workflow to subagent backend as a prerequisite.
- Changing the route-codex-task policy's approved model or effort allow-list.
- Giving tickets independent implementation tasks or routes in whole-SPEC mode.
- Replacing the controller kernel with a business-runtime scheduler.
- Inferring project identity from URLs, request IDs, titles or message content.
- Treating a backend capability receipt as a substitute for the complete qualification report.

## Further Notes

The existing workflow already intends to select a route for each child SPEC before task creation. The missing user-facing field is a route summary in the SPEC planning artifact and controller status, not permission for the SPEC text to invent an execution model. The authoritative value remains the post-create applied route readback.

The observed recovery point is deterministic: after cleaning the residual Grill thread and recording its archive readback, the next action is `advance_spec(#90)`. The implementation must encode that recovery path as a regression fixture so that a future model-capacity failure cannot leave the run silently active.
