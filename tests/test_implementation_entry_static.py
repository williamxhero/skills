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
            "complete capability preflight": r"B2 .*验收.*技能.*tracker.*角色.*工作目录.*blocked",
            "mandatory before tickets": r"B3 .*每个当前 SPEC.*必.*grilling.*## B4 .*to-tickets",
            "real native graph reuse": r"B4 .*复用.*原生父子.*原生阻塞.*回读.*不编码",
            "isolated tdd and merger": r"B5 .*新上下文.*独立工作树.*tdd.*merger",
            "simplify then capture then original review": r"B6.*B7.*原第 7 步.*原第 8.{0,10}9 步.*B11",
            "pinned independent review": r"B8 .*固定基点.*候选.*Standards.*Spec.*并行.*新上下文.*过期",
            "full delivery before close": r"B10 .*主干.*合并后.*清理.*push.*远端.*## B11 .*回读.*关闭 tickets.*关闭当前 SPEC.*CLOSED",
        }
        for topic, pattern in contracts.items():
            with self.subTest(topic=topic):
                self.assertRegex(entry, re.compile(pattern, re.S))
        for name in ("verification-before-completion", "ce-simplify-code", "ce-compound"):
            self.assertIn(f"references/upstream/{name}/adapter.md", entry)
        metadata = (ENTRY / "agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn("grilling", metadata)
        self.assertIn("简化", metadata)

    def test_static_adapters_override_all_references_without_extra_public_commands(self):
        upstream = ENTRY / "references/upstream"
        compound = (upstream / "ce-compound/adapter.md").read_text(encoding="utf-8")
        contracts = {
            "adapter first and global": r"先完整读取本适配.*贯穿所有.*references.*subagent prompts",
            "full default without silent downgrade": r"depth:full.*默认.*Full.*不得.*静默降级",
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
        simplify = (upstream / "ce-simplify-code/adapter.md").read_text(encoding="utf-8")
        self.assertRegex(simplify, r"no-code.*prose")
        self.assertRegex(simplify, r"证明测试保持原样")
        # Check maintained local links, not example links in immutable upstream templates.
        local_docs = [ENTRY / "SKILL.md", ENTRY / "references/pi-execution.md",
                      *upstream.glob("*/adapter.md")]
        for path in local_docs:
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                self.assertTrue((path.parent / target).is_file(), f"{path}: {target}")

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
            self.assertEqual(sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in ROOT.rglob("SKILL.md")),
                             ["implement-spec-to-done/SKILL.md", "improve-codebase-architecture-to-done/SKILL.md",
                              "to-spec-to-done/SKILL.md"])


if __name__ == "__main__":
    unittest.main()
