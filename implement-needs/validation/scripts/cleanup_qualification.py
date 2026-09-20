"""Safely clean resources explicitly owned by one qualification run."""
from __future__ import annotations

import argparse
import json
import shutil
import stat
import subprocess
from pathlib import Path


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value


def _safe_paths(manifest: dict, run_id: str) -> list[Path]:
    result = []
    for raw in manifest.get("temporary_paths", []):
        path = Path(raw).resolve()
        if run_id not in path.name and run_id not in str(path):
            raise ValueError(f"temporary path is not owned by run: {path}")
        if path == Path(path.anchor) or len(path.parts) < 3:
            raise ValueError(f"refusing broad cleanup target: {path}")
        result.append(path)
    return result


def _run_git(repo: Path, args: list[str]) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, text=True,
                               capture_output=True, check=True)
    return completed.stdout.strip()


def _git_ref_exists(repo: Path, ref: str) -> bool:
    completed = subprocess.run(["git", "show-ref", "--verify", "--quiet", ref],
                               cwd=repo, text=True, capture_output=True, check=False)
    return completed.returncode == 0


def _remote_ref_exists(repo: Path, branch: str) -> bool:
    completed = subprocess.run(["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"],
                               cwd=repo, text=True, capture_output=True, check=False)
    return completed.returncode == 0


def _remove_path(path: Path) -> None:
    def on_error(function, failed_path, error):
        Path(failed_path).chmod(stat.S_IWRITE)
        function(failed_path)

    if path.is_dir():
        shutil.rmtree(path, onerror=on_error)
    else:
        try:
            path.unlink()
        except PermissionError:
            path.chmod(stat.S_IWRITE)
            path.unlink()


def _execute_cleanup(paths: list[Path], repository: Path | None, branches: list[str]) -> None:
    # Remove branches while the temporary repository still exists.
    if repository and repository.exists():
        for branch in branches:
            if _git_ref_exists(repository, f"refs/heads/{branch}"):
                if _run_git(repository, ["branch", "--show-current"]) == branch:
                    _run_git(repository, ["checkout", "--quiet", "master"])
                _run_git(repository, ["branch", "-D", branch])
            if _remote_ref_exists(repository, branch):
                _run_git(repository, ["push", "origin", "--delete", branch])
    for path in paths:
        if path.exists():
            _remove_path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    manifest = _load(args.manifest)
    if manifest.get("run_id") != args.run_id:
        raise SystemExit("manifest run_id does not match --run-id")
    paths = _safe_paths(manifest, args.run_id)
    branches = [branch for branch in manifest.get("temporary_branches", [])
                if isinstance(branch, str) and branch.startswith(f"in-validation/{args.run_id}/")]
    if len(branches) != len(manifest.get("temporary_branches", [])):
        raise SystemExit("manifest contains a branch without the exact run marker")
    receipt = {"run_id": args.run_id, "complete": True, "executed": args.execute,
               "paths": [str(path) for path in paths], "branches": branches}
    if args.execute:
        repo = manifest.get("repository")
        _execute_cleanup(paths, Path(repo).resolve() if repo else None, branches)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
