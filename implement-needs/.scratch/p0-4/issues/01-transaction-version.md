# P0-4.1: 原子业务事务与乐观业务版本

## Parent

SPEC P0-4: https://github.com/williamxhero/skills/issues/44

## What to build

让控制器的状态、审计事件和业务版本在同一个显式事务中提交；任一写入失败都完整回滚。状态写入接受读取时的业务版本，旧版本必须返回可识别的 `stale_state` 错误并拒绝覆盖新状态。

## Acceptance criteria

- [ ] 所有控制器业务写入使用统一显式事务边界，失败时状态、事件和版本都不变。
- [ ] 业务版本可读取且每次成功业务变更单调递增；旧版本写入被拒绝并返回 `stale_state`。
- [ ] 状态、事件和版本的回滚与顺序写入有公共 API 测试覆盖。

## Blocked by

- None (can start immediately)

