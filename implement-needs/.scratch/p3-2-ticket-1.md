## Parent

[#100](https://github.com/williamxhero/skills/issues/100)

## What to build

建立一个版本化、阶段化的增量上下文 envelope，能够识别未变化集合并返回稳定 digest、count、cursor，同时保留当前阶段必需事实和业务版本边界。旧 P3-1 envelope 继续可用，未完成迁移的调用者不得静默改变语义。

## Acceptance criteria

- [ ] phase、run identity、business version、event cursor 和当前动作必需字段有单一可执行契约。
- [ ] acceptance、direct dependencies、decisions、evidence、events、unresolved exceptions 的稳定集合可产生确定性 digest/summary。
- [ ] 相同版本和相同事实产生字节稳定的相同 digest；业务版本或内容变化会改变相应摘要。
- [ ] 旧 P3-1 调用路径保持兼容，且没有业务状态或外部副作用。
- [ ] 通过针对上下文契约与序列化的单元/公共边界测试。

## Blocked by

None (can start immediately)
