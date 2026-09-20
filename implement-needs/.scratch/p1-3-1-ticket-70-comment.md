P1-3.1 已实现并验证。

- 提交：`fb48813 feat(implement-needs): enforce startup contract`
- 启动契约：绑定 run、runtime、Skill root、目标仓库、tracker、host capabilities、权限与依赖解析结果。
- 依赖安全：canonical name/显式 alias 解析；path、digest、version、adapter 不匹配或依赖缺失/歧义均 fail closed。
- 持久化：契约 canonical JSON + SHA-256 摘要；相同契约幂等，不同契约不可覆盖；`startup-check` 可读回。
- 副作用边界：验证失败不增加 business_version、事件或契约记录。
- 验证：`python -m unittest discover -s tests -p 'test_p1_3_startup_contract.py'`（6 项通过）；`python -m unittest discover -s tests`（239 项通过）。