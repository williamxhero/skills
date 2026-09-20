# P0-2.2: 将统一 Gate 接入终态状态变更

## Parent

SPEC P0-2: https://github.com/williamxhero/skills/issues/42

## What to build

让 Action 成功、Ticket 关闭、SPEC 关闭和线程归档等终态写入都先经过同一个 Gate，再在一个事务中写入状态、证据引用、业务事件和版本。调用方的成功声明只能作为待验证输入，Gate 未通过不得改变状态。

## Acceptance criteria

- [ ] Ticket 关闭独立要求匹配当前 SPEC/候选的 commit 与 test 证据，不能用一类证据替代另一类。
- [ ] Action 成功、SPEC 关闭、线程归档分别要求适用的目标身份、可信回读和归档回读条件。
- [ ] 任一 Gate 拒绝均不会改变目标状态、证据引用、事件日志或业务版本。

## Blocked by

- P0-2.1: 统一可信证据 Gate 与动作契约

