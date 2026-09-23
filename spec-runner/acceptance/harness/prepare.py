"""Prepare and inspect same-repository acceptance namespaces.

This harness never advances a Runner stage or creates a GitHub resource.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REMOTE = "github.com/williamxhero/skills"
MARKER = re.compile(r"^SRAC-[0-9]{8}-[a-f0-9]{12}$")


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", os.fspath(repository), *arguments], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout.strip()


def repository_identity(repository: Path) -> tuple[Path, str, str]:
    canonical = repository.resolve(strict=True)
    top = Path(git(canonical, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if canonical != top or ROOT.parent.parent.resolve() != top:
        raise ValueError("acceptance preparation requires this skills repository root")
    remote = git(top, "remote", "get-url", "origin")
    normalized = remote.removesuffix(".git").replace("git@github.com:", "https://github.com/")
    parsed = urlparse(normalized)
    if parsed.hostname != "github.com" or parsed.path.strip("/") != "williamxhero/skills":
        raise ValueError("origin must be williamxhero/skills")
    return top, remote, git(top, "rev-parse", "HEAD")


def prepare(repository: Path, run_id: str | None) -> dict[str, object]:
    top, remote, base = repository_identity(repository)
    marker = run_id or f"SRAC-{time.strftime('%Y%m%d', time.gmtime())}-{uuid.uuid4().hex[:12]}"
    if MARKER.fullmatch(marker) is None:
        raise ValueError("run marker must be SRAC-YYYYMMDD-12hex")
    runtime = ROOT / ".runtime" / marker
    manifest = runtime / "manifest.json"
    if manifest.exists():
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        if recorded.get("run_marker") != marker or recorded.get("repository_path") != os.fspath(top):
            raise ValueError("existing run marker belongs to different scope")
        return {"created": False, "manifest": os.fspath(manifest), "run": recorded}
    runtime.mkdir(parents=True, exist_ok=False)
    record: dict[str, object] = {
        "schema_version": "spec-runner-acceptance-manifest/v1",
        "run_marker": marker,
        "repository": "williamxhero/skills",
        "repository_path": os.fspath(top),
        "remote": remote,
        "base_sha": base,
        "fixture_prefix": f"spec-runner/acceptance/fixture-app/{marker}/",
        "created_at_ns": time.time_ns(),
        "resources": {"issues": [], "branches": [], "pull_requests": [], "threads": [], "worktrees": []},
    }
    manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return {"created": True, "manifest": os.fspath(manifest), "run": record}


def scope(manifest: Path, workspace: Path, base: str) -> dict[str, object]:
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema_version") != "spec-runner-acceptance-manifest/v1":
        raise ValueError("invalid acceptance manifest")
    marker = record.get("run_marker")
    if not isinstance(marker, str) or MARKER.fullmatch(marker) is None or manifest.resolve() != (ROOT / ".runtime" / marker / "manifest.json").resolve():
        raise ValueError("manifest is outside its run namespace")
    canonical = workspace.resolve(strict=True)
    if Path(git(canonical, "rev-parse", "--show-toplevel")).resolve() != canonical:
        raise ValueError("workspace must be a worktree root")
    if (ROOT / ".runtime" / marker).resolve() not in canonical.parents:
        raise ValueError("workspace must be inside its acceptance runtime namespace")
    if git(canonical, "remote", "get-url", "origin") != record["remote"]:
        raise ValueError("workspace origin differs from prepared repository")
    if base != record["base_sha"]:
        raise ValueError("base revision differs from prepared run")
    changed = set(_git_paths(canonical, "diff", "--name-only", "-z", f"{base}..HEAD"))
    changed.update(_git_paths(canonical, "diff", "--name-only", "-z", "HEAD"))
    changed.update(_git_paths(canonical, "ls-files", "--others", "--exclude-standard", "-z"))
    prefix = str(record["fixture_prefix"])
    rejected = sorted(path for path in changed if not path.startswith(prefix))
    return {"run_marker": marker, "changed": sorted(changed), "rejected": rejected, "accepted": not rejected}


def _git_paths(repository: Path, *arguments: str) -> list[str]:
    result = subprocess.run(["git", "-C", os.fspath(repository), *arguments], check=True, capture_output=True)
    return [path.decode("utf-8", errors="surrogateescape") for path in result.stdout.split(b"\0") if path]


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--repository", required=True, type=Path)
    prepare_parser.add_argument("--run-id")
    scope_parser = commands.add_parser("scope")
    scope_parser.add_argument("--manifest", required=True, type=Path)
    scope_parser.add_argument("--workspace", required=True, type=Path)
    scope_parser.add_argument("--base", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.repository, args.run_id) if args.command == "prepare" else scope(args.manifest, args.workspace, args.base)
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("accepted", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
