# Implement Needs model policy

[`model-policy.json`](model-policy.json) is the sole machine-readable allow-list for Implement Needs. Its rank order follows host capability tiers: fast/economical `gpt-5.6-luna` < balanced `gpt-5.6-terra` < reliable-workhorse `gpt-5.6-sol`; reasoning effort remains `medium` < `high` < `xhigh`. A fallback must use a different, allow-listed pair that is no weaker on either rank and remains owned by the same SPEC task.

## Chinese controller status template

Use this template after every validated SPEC dispatch or recovery. Substitute the persisted values; do not describe a requested pair as applied.

```text
SPEC {spec_id} 当前由任务 {task_id} 执行；实际模型：{model}；推理强度：{thinking}；路由：{selection}。
可选模型仅为：gpt-5.6-sol、gpt-5.6-terra、gpt-5.6-luna；可选推理强度仅为：medium、high、xhigh。
下一步：{next_action}
```
