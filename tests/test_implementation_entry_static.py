"""STATIC shipped document/packaging contracts; never model behavior evidence."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "implement-spec-to-done"


class ImplementationEntryStaticTests(unittest.TestCase):
    def test_static_current_spec_delivery_contract_and_local_phase_adaptation(self):
        entry = (ENTRY / "SKILL.md").read_text(encoding="utf-8")
        for stage in range(1, 13):
            self.assertRegex(entry, rf"(?m)^## B{stage} ")
        for required in (
            "../to-spec-to-done/references/engineering-quality.md",
            "../to-spec-to-done/references/auto-grilling.md",
            "references/pi-execution.md",
            "LOCAL PHASE ADAPTATION",
        ):
            self.assertIn(required, entry)
        contracts = {
            "singleton including later tickets": r"下一 SPEC.{0,80}拆票.{0,100}(关闭|CLOSED)",
            "mandatory before tickets": r"B3 .*每个当前 SPEC.*必.*grilling.*## B4 .*to-tickets",
            "real native graph reuse": r"B4 .*复用.*原生父子.*原生阻塞.*回读.*不编码",
            "simplify then capture then original review": r"B6.*B7.*原第 7 步.*原第 8.{0,10}9 步.*B11",
            "pinned independent review": r"B8 .*固定基点.*候选.*Standards.*Spec.*并行.*新上下文.*过期",
            "full delivery before close": r"B10 .*主干.*合并后.*清理.*push.*远端.*## B11 .*回读.*关闭 tickets.*关闭当前 SPEC.*CLOSED",
        }
        for topic, pattern in contracts.items():
            with self.subTest(topic=topic):
                self.assertRegex(entry, re.compile(pattern, re.S))
        preflight = entry.split("## B2 ", 1)[1].split("## B3 ", 1)[0]
        self.assertRegex(
            preflight,
            r"(?s)完整闭包.*\]\(references/prerequisite-delivery\.md#[^)]+\).*受阻动作",
            "B2 preflight must route known holds to the action-scoped contract",
        )
        self.assertRegex(preflight, r"(?s)必经.*tracker.*角色.*工作目录.*按触发预检.*只在实际进入.*未触发 Full 不探测")
        self.assertRegex(preflight, r"(?s)纯说明.*源码核对.*Agent 决策.*文本场景.*可执行行为.*公开 seam.*回归.*混合差异")
        grilling = entry.split("## B3 ", 1)[1].split("## B4 ", 1)[0]
        self.assertRegex(
            grilling,
            r"\]\(\.\./to-spec-to-done/references/engineering-quality\.md#codebase-design\)时，"
            r"直接读取并应用实际 `codebase-design`",
        )
        implementation = entry.split("## B5 ", 1)[1].split("## B6 ", 1)[0]
        self.assertRegex(implementation, r"(?s)新上下文 implementer.*独立工作树.*提交前.*新的 \*\*merger\*\* 上下文")
        self.assertRegex(implementation, r"纯说明／链接：.*语义、引用、格式和差异.*不制造行为 RED")
        self.assertRegex(implementation, r"改变 Agent 决策.*具体输入.*动作、证据和停止点.*不是运行证明")
        self.assertRegex(implementation, r"可执行行为：.*`tdd`.*公开 seam.*真实 RED.*最小实现.*GREEN.*回归")
        pi = (ENTRY / "references/pi-execution.md").read_text(encoding="utf-8")
        self.assertRegex(pi, r"(?s)已通过探针.*环境／cwd.*工具／版本.*权限及角色要求.*仅同条件复用.*变化刷新.*实际角色仍须 fresh")
        self.assertRegex(pi, r"可执行行为才.*实际 tdd.*不为纯文字做行为 RED")
        for name in ("verification-before-completion", "ce-simplify-code", "ce-compound"):
            self.assertIn(f"references/upstream/{name}/adapter.md", entry)
        metadata = (ENTRY / "agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn("grilling", metadata)
        self.assertIn("简化", metadata)

    def test_static_ticket_review_flows_to_authorized_publication(self):
        entry = (ENTRY / "SKILL.md").read_text(encoding="utf-8")
        tickets = entry.split("## B4 ", 1)[1].split("## B5 ", 1)[0]
        self.assertRegex(tickets, r"原第 1–3 步.*原模板.*tracer-bullet")
        self.assertRegex(tickets, r"原第 4 步.*粒度.*阻塞方向.*合并／再拆.*采纳.*不向用户发问")
        self.assertRegex(tickets, r"原第 5 步.*复用.*有建票授权时才.*缺权限按 B2.*单独调用原 `to-tickets` 不变")
        self.assertRegex(tickets, r"(?s)创建前搜索并回读.*原生父子关系.*原生阻塞关系.*回读完整正文.*不编码")

    def test_static_adapters_override_all_references_without_extra_public_commands(self):
        upstream = ENTRY / "references/upstream"
        compound = (upstream / "ce-compound/adapter.md").read_text(encoding="utf-8")
        contracts = {
            "adapter first and global": r"先完整读取本适配.*贯穿所有.*references.*subagent prompts",
            "explicit mode token, no silent downgrade": r"显式传一个深度 token.*默认 Full.*Full 受阻时不得静默降级",
            "lightweight losses and retained checks":
                r"depth:lightweight.*不派子 Agent；披露省略语义查重.*历史研究、独立语义 grounding.*当前源码.*精确路径冲突.*写入边界.*缺陷形状 fail.*机械 claims 和 frontmatter",
            "full and final review retained": r"depth:full.*实际新上下文.*两种深度都不替代外层最终独立审查",
            "capture despite free text": r"自由文本.*refresh.*capture",
            "mandatory judgment may write nothing": r"必须调用技能判断.*no-write",
            "authority replaces every effect": r"每一处 CONCEPTS.md.*读取.*写入.*grounding.*discoverability.*report.*替换",
            "lazy no parallel vocabulary": r"真实新术语.*domain-modeling.*懒创建.*无术语就无新文件",
            "historian scratch-only exception": r"Historian 唯一写入差异.*session-history.md.*不写任何 tracked",
            "full actual contexts": r"Related Docs Finder.*history synthesis.*semantic grounding.*实际新上下文",
            "no public specialist command claim": r"不是公开技能命令.*建议.*外层",
        }
        for topic, pattern in contracts.items():
            with self.subTest(topic=topic):
                self.assertRegex(compound, re.compile(pattern, re.S))
        vocabulary = compound.split("## 权威词汇替换", 1)[1].split("\n## ", 1)[0]
        full = re.search(r"(?:只有|仅)\s*Full\b([^。；;\n]+)", vocabulary)
        lightweight = re.search(r"\bLightweight\b([^。；;\n]+)", vocabulary)
        self.assertIsNotNone(full, "missing Full-only vocabulary obligations")
        self.assertIsNotNone(lightweight, "missing Lightweight vocabulary obligations")
        for mode, clause, checks in (
            ("Full", full[1], {
                "independent grounding": r"独立.{0,12}grounding",
                "fresh context": r"新上下文",
                "receives entries": r"接收|传入",
                "verifies entries": r"核[验实对]|验证",
            }),
            ("Lightweight", lightweight[1], {
                "no subagents": r"(?:不|禁止).{0,8}(派|调用).{0,4}子\s*Agent",
                "orchestrator verifies against current source":
                    r"orchestrator.{0,20}当前源码.{0,12}(核[验实对]|验证)",
            }),
        ):
            for topic, pattern in checks.items():
                with self.subTest(mode=mode, topic=topic):
                    self.assertRegex(clause, pattern)
            with self.subTest(mode=mode, topic="new and modified authority entries"):
                self.assertRegex(clause, r"新增\s*[/／、和及与]\s*修改.{0,8}权威词汇条目")
        simplify = (upstream / "ce-simplify-code/adapter.md").read_text(encoding="utf-8")
        self.assertRegex(simplify, r"no-code.*prose")
        self.assertRegex(simplify, r"证明测试保持原样")
        # Check maintained local links, not example links in immutable upstream templates.
        local_docs = [ENTRY / "SKILL.md", ENTRY / "references/pi-execution.md",
                      *upstream.glob("*/adapter.md")]
        for path in local_docs:
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                self.assertTrue((path.parent / target.split("#", 1)[0]).is_file(), f"{path}: {target}")

    def test_static_selected_bundle_preserves_pinned_specialist_resources(self):
        # Independent source inventory: verification 1, simplify 12, compound 39.
        expected = {"verification-before-completion": 1, "ce-simplify-code": 12,
                    "ce-compound": 39}
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / ENTRY.name
            shutil.copytree(ENTRY, copied)
            upstream = copied / "references/upstream"
            manifest = json.loads((upstream / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(set(manifest["sources"]), set(expected))
            for name, count in expected.items():
                with self.subTest(skill=name):
                    source = manifest["sources"][name]
                    self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")
                    self.assertEqual(len(source["files"]), count)
                    self.assertTrue((upstream / name / "adapter.md").is_file())
                    license_bytes = (upstream / name / "LICENSE").read_bytes()
                    self.assertIn(b"MIT License", license_bytes)
                    self.assertEqual(hashlib.sha256(license_bytes).hexdigest().upper(), source["license_sha256"])
                    actual_files = {str(p.relative_to(upstream / name)).replace("\\", "/")
                                    for p in (upstream / name).rglob("*") if p.is_file()}
                    self.assertEqual(actual_files, {f["bundled_path"] for f in source["files"]}
                                     | {"LICENSE", "adapter.md"})
                    for resource in source["files"]:
                        original = resource["source_path"]
                        target = resource["bundled_path"]
                        self.assertEqual(target, "instructions.md" if original == "SKILL.md" else original)
                        content = (upstream / name / target).read_bytes()
                        self.assertEqual(hashlib.sha256(content).hexdigest().upper(), resource["sha256"])
                    self.assertEqual(source["modifications"], ["SKILL.md renamed to instructions.md; bytes unchanged"])
            self.assertEqual(manifest["sources"]["verification-before-completion"]["files"][0]["sha256"],
                             "2BEFE7FC55BCADAA3D97DD9E8EFEB633D2561C0EBE74C5A8B17C4D9E7E4520B3")
        # Check the repository inventory after removing the selected copy, even with in-tree TEMP.
        self.assertEqual(sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in ROOT.rglob("SKILL.md")),
                         ["commit-n-push/SKILL.md", "delete-threads/SKILL.md",
                          "implement-spec-to-done/SKILL.md", "improve-codebase-architecture-to-done/SKILL.md",
                          "merge-upstream-parents/SKILL.md", "to-spec-to-done/SKILL.md", "what-now/SKILL.md"])


if __name__ == "__main__":
    unittest.main()
