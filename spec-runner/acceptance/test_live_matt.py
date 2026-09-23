"""SF-01 local Skill behavior using disposable installed-skill copies."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.errors import RunnerError
from spec_runner.codex_adapter import CodexAdapter
from spec_runner.diagnostics import canonical_release_subject
from spec_runner.matt import render_prompt, resolve_local_skill


def _config(root: Path, name: str, *, path: str | None = None, resources: list[str] | None = None) -> Path:
    config = root / "matt.json"
    mapping: dict[str, object] = {"name": name, "resources": resources or []}
    if path is not None:
        mapping["path"] = path
    config.write_text(json.dumps({"schema_version": "spec-runner-live-skills/v1", "roots": [str(root)], "skills": {"grill": mapping}}), encoding="utf-8")
    return config


def test_current_local_skill_updates_in_same_run_without_pin_or_git(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "controlled-grill"
    skill_dir.mkdir(parents=True)
    source = skill_dir / "SKILL.md"
    config = _config(root, "controlled-grill")
    source.write_text("# Method A\nAsk about A.\n", encoding="utf-8")
    first = resolve_local_skill("grill", config_file=config)
    first_prompt = render_prompt(phase="grill", lock=first, trusted={}, untrusted={}, schema={})
    source.write_text("# Method B\nAsk about B.\n", encoding="utf-8")
    second = resolve_local_skill("grill", config_file=config)
    second_prompt = render_prompt(phase="grill", lock=second, trusted={}, untrusted={}, schema={})
    assert "Ask about A." in str(first_prompt["prompt"])
    assert "Ask about B." in str(second_prompt["prompt"])
    assert "Ask about A." not in str(second_prompt["prompt"])
    assert first.sha256 != second.sha256
    assert first_prompt["skill_source"] == second_prompt["skill_source"]


def test_missing_skill_blocks_only_requested_phase_and_recovers(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    config = _config(root, "controlled-grill")
    with pytest.raises(RunnerError) as missing:
        resolve_local_skill("grill", config_file=config)
    assert missing.value.code == "skill_missing"
    source = root / "controlled-grill" / "SKILL.md"
    source.parent.mkdir()
    source.write_text("# Restored\n", encoding="utf-8")
    assert resolve_local_skill("grill", config_file=config).content.splitlines() == ["# Restored"]


def test_explicit_move_resources_and_escape_guard(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    moved = root / "renamed"
    moved.mkdir(parents=True)
    (moved / "SKILL.md").write_text("# Moved\n", encoding="utf-8")
    (moved / "guide.md").write_text("Current resource B\n", encoding="utf-8")
    config = _config(root, "controlled-grill", path="renamed/SKILL.md", resources=["guide.md"])
    rendered = render_prompt(phase="grill", lock=resolve_local_skill("grill", config_file=config), trusted={}, untrusted={}, schema={})
    assert "Current resource B" in str(rendered["prompt"])
    outside = tmp_path / "outside.md"
    outside.write_text("out of scope", encoding="utf-8")
    _config(root, "controlled-grill", path="../outside.md")
    with pytest.raises(RunnerError) as escaped:
        resolve_local_skill("grill", config_file=config)
    assert escaped.value.code == "skill_path_unauthorized"


def test_sdk_turn_receives_updated_body_on_resumed_thread(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    source = root / "controlled-grill" / "SKILL.md"
    source.parent.mkdir(parents=True)
    config = _config(root, "controlled-grill")
    prompts: list[str] = []
    sandboxes: list[str] = []
    resumed: list[str] = []
    skill_refs: list[tuple[str, str]] = []
    output_schemas: list[object] = []

    class TextInput:
        def __init__(self, text: str):
            self.text = text

    class SkillInput:
        def __init__(self, name: str, path: str):
            self.name = name
            self.path = path

    class Thread:
        id = "same-thread"

        def run(self, prompt: list[object], **kwargs: object) -> object:
            assert isinstance(prompt[0], TextInput)
            assert isinstance(prompt[1], SkillInput)
            prompts.append(prompt[0].text)
            skill_refs.append((prompt[1].name, prompt[1].path))
            sandboxes.append(str(kwargs["sandbox"]))
            output_schemas.append(kwargs.get("output_schema"))
            return types.SimpleNamespace(id=f"turn-{len(prompts)}", status="completed", error=None, final_response="{}", items=[], started_at=1, completed_at=2)

    class Codex:
        def __enter__(self) -> "Codex":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def thread_start(self, **kwargs: object) -> Thread:
            sandboxes.append(str(kwargs["sandbox"]))
            return Thread()

        def thread_resume(self, thread_id: str, **kwargs: object) -> Thread:
            resumed.append(thread_id)
            sandboxes.append(str(kwargs["sandbox"]))
            return Thread()

    sdk = types.SimpleNamespace(
        CodexConfig=lambda **kwargs: kwargs,
        Codex=Codex,
        Sandbox=types.SimpleNamespace(read_only="read-only", workspace_write="workspace-write"),
        ApprovalMode=types.SimpleNamespace(deny_all="deny_all"),
        TextInput=TextInput,
        SkillInput=SkillInput,
    )
    adapter = CodexAdapter(codex_factory=lambda config: Codex(), sdk_module=sdk)
    schema = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"], "additionalProperties": False}
    source.write_text("# Skill A\nSay alpha\n", encoding="utf-8")
    first = adapter.run_semantic(phase="grill", repository_path=tmp_path, model="configured-model", effort="high", trusted={}, untrusted={}, schema=schema, skill_config=config)
    source.write_text("# Skill B\nSay beta\n", encoding="utf-8")
    second = adapter.run_semantic(phase="grill", repository_path=tmp_path, model="configured-model", effort="high", trusted={}, untrusted={}, schema=schema, skill_config=config, thread_id=first.thread_id)
    assert resumed == ["same-thread"]
    assert "Say alpha" in prompts[0]
    assert "Say beta" in prompts[1]
    assert "Say alpha" not in prompts[1]
    assert sandboxes == ["read-only"] * 4
    assert skill_refs[0] == skill_refs[1]
    assert output_schemas == [schema, schema]
    assert first.skill_observation["source_digest"] != second.skill_observation["source_digest"]


def test_matt_observation_does_not_change_release_subject() -> None:
    stable = {
        "runner_version": "0.1.0",
        "build_digest": "build-under-test",
        "config_contract": "spec-runner-config/v1",
        "sdk_runtime": {"package": "openai-codex", "version": "0.155.1"},
        "contract_digests": {"prompt_templates": "p", "schemas": "s", "validators": "v"},
        "os": "windows",
        "trust_mode": "deny_all",
        "scenario_version": "sf-01/v1",
    }
    first = canonical_release_subject({**stable, "matt_lock_digest": "A"}, expected_runner_version="0.1.0")
    second = canonical_release_subject({**stable, "matt_lock_digest": "B"}, expected_runner_version="0.1.0")
    assert first == second
