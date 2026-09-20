## Parent

Part of #43 (SPEC P0-3: 授权范围、候选冻结与最终同步一致性)

## What to build

在运行创建时持久化不可变的授权快照：仓库身份、目标分支/ref、允许路径与任务范围、允许的外部动作、部署环境以及是否允许整项目提交。后续自动决策必须只能在该快照内发生，超出范围的分支、路径、环境、部署或整项目动作必须在副作用前被拒绝。

## Acceptance criteria

- [ ] 运行创建必须带完整授权快照，快照写入后不可被普通更新覆盖。
- [ ] 授权快照绑定 run、仓库、目标 ref、允许路径/任务、动作 allow-list、部署目标与 full-project 权限。
- [ ] 缺少必要授权或身份不匹配时 fail closed，并返回可行动的 authorization error。
- [ ] 普通 run-scoped 模式默认不允许整项目提交、越界路径、未授权分支或未授权部署。
- [ ] 授权判断不执行外部副作用，且拒绝保持状态、事件与业务版本不变。

## Blocked by

- None (can start immediately)
