# Local adapter — verification-before-completion

**每次先读本适配，再完整读取并执行 [instructions.md](instructions.md)。** 支持上游完整验证流程，无行为／分支改变；只映射实际工具和文件名。`Read` 使用实际 read；命令用可用 shell 执行，记录完整命令、cwd、环境、候选、stdout／stderr 和 exit code。没有 Skill／hook API，读取后实际执行才是调用。

来源：obra/superpowers，commit `22f0b463e7a5e579904dd6211a520321035526f7`，`skills/verification-before-completion/SKILL.md`，SHA256 `2BEFE7FC55BCADAA3D97DD9E8EFEB633D2561C0EBE74C5A8B17C4D9E7E4520B3`。完整文件清单见 [provenance.json](../provenance.json)，MIT Jesse Vincent 2025 见 [LICENSE](LICENSE)。上游字节不变，仅主文件改名为 instructions.md。

逐条声明选择对应新鲜证据。STATIC 文档／包装检查只证明结构，不证明 Pi 模型行为、独立审查、部署、GitHub 或远端同步。ticket 验证不等于 SPEC 完成；外层关闭门禁保持有效。失败／未运行的必需项保留 pending／blocked，不能以别的检查通过补齐。
