# P0-2.3: 公共 CLI 与控制器路径的证据 Gate 回归

## Parent

SPEC P0-2: https://github.com/williamxhero/skills/issues/42

## What to build

为直接 CLI 和控制器驱动路径建立同一套 Gate 回归，覆盖缺 owner、错误 identity、旧版本、错误 SHA、缺 commit、缺 test、空 expected、不可信来源和缺 archive readback 等已知绕过，并验证完整匹配的终态成功。

## Acceptance criteria

- [ ] 所有已知绕过在公共入口返回相同结构化拒绝语义，并保持业务状态与事件不变。
- [ ] 完整匹配的 Ticket/Action/线程终态能够成功写入并留下可回读证据。
- [ ] 直接 CLI 与 controller-driven 调用复用同一个 Gate 实现，测试覆盖两条路径。

## Blocked by

- P0-2.2: 将统一 Gate 接入终态状态变更

