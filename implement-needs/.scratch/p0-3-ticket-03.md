## Parent

Part of #43 (SPEC P0-3: 授权范围、候选冻结与最终同步一致性)

## What to build

实现安全的 run-scoped 最终同步与最终回读：默认只处理本运行授权范围内的改动，保留无关用户改动；显式 full-project 权限走独立路径；同步前后都核对候选身份、分支与远端 HEAD，公共 CLI 在拒绝或失效时返回结构化恢复动作。

## Acceptance criteria

- [ ] 脏工作区中的无关改动不会被普通同步提交或覆盖，并被记录为 preserved/unrelated。
- [ ] 未授权 full-project、分支、路径、部署或远端 merge 操作在副作用前被拒绝。
- [ ] 显式 full-project 授权与普通 run-scoped 同步路径可区分、可审计。
- [ ] 同步完成必须有 candidate-bound readback，证明目标分支与 local/remote HEAD 一致且等于冻结 candidate。
- [ ] 公共 CLI 覆盖稳定候选、候选漂移、远端推进和授权拒绝；失败不能留下 completed 状态。

## Blocked by

- #68：候选冻结与证据一致性
