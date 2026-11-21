#!/usr/bin/env python3
"""Preview or permanently remove local Codex threads by literal display-title rule."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def display_title(raw: str) -> str:
    """Return the title shown in the Codex thread list for a stored title."""
    lines = (raw or "").splitlines()
    first = lines[0] if lines else ""
    prefix = "Automation: "
    return first[len(prefix):] if first.startswith(prefix) else first


def resolve_inside(path: Path, roots: Iterable[Path]) -> bool:
    try:
        candidate = path.resolve(strict=True)
    except FileNotFoundError:
        return False
    for root in roots:
        try:
            candidate.relative_to(root.resolve(strict=True))
            return True
        except ValueError:
            continue
    return False


def atomic_write_text(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def quote_marks(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--title-prefix")
    modes.add_argument("--title-contains")
    modes.add_argument("--title-exact")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--project-root", type=Path,
                        default=Path.cwd())
    parser.add_argument("--exclude-thread-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    codex_home = args.codex_home.expanduser().resolve()
    db = codex_home / "state_5.sqlite"
    sessions = codex_home / "sessions"
    archived = codex_home / "archived_sessions"
    index = codex_home / "session_index.jsonl"
    if not db.is_file():
        raise SystemExit(f"Codex state database not found: {db}")

    if args.title_prefix is not None:
        mode, needle = "prefix", args.title_prefix
    elif args.title_contains is not None:
        mode, needle = "contains", args.title_contains
    else:
        mode, needle = "exact", args.title_exact
    if not needle:
        raise SystemExit("The title rule must not be empty.")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, title, rollout_path, archived, updated_at "
            "FROM threads ORDER BY updated_at ASC"
        ).fetchall()
    finally:
        con.close()

    def matches(title: str) -> bool:
        visible = display_title(title)
        if mode == "prefix":
            return visible.startswith(needle)
        if mode == "contains":
            return needle in visible
        return visible == needle

    candidates = []
    for row in rows:
        if not matches(row["title"]):
            continue
        if args.exclude_thread_id and row["id"] == args.exclude_thread_id:
            continue
        rollout = Path(row["rollout_path"])
        if not rollout.is_file():
            raise SystemExit(f"Refusing to proceed: transcript missing for {row['id']}: {rollout}")
        if not resolve_inside(rollout, (sessions, archived)):
            raise SystemExit(f"Refusing to proceed: transcript escapes allowed roots: {rollout}")
        candidates.append({
            "id": row["id"],
            "display_title": display_title(row["title"]),
            "archived": bool(row["archived"]),
            "rollout_path": str(rollout),
            "updated_at": row["updated_at"],
        })

    result = {
        "mode": mode,
        "needle": needle,
        "count": len(candidates),
        "candidates": candidates,
        "codex_home": str(codex_home),
    }
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if candidates else 2
    if not candidates:
        raise SystemExit("No matching threads found; refusing to mutate anything.")

    project_root = args.project_root.expanduser().resolve()
    backup_dir = project_root / ".local" / "delete-threads" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rule": {"mode": mode, "needle": needle},
        "candidate_ids": [item["id"] for item in candidates],
        "transcripts": [],
        "index_backup": None,
        "database_backup": str(backup_dir / "state_5.sqlite"),
    }

    # Make a consistent database restore point before any mutation.
    backup_db = sqlite3.connect(backup_dir / "state_5.sqlite")
    source_db = sqlite3.connect(db)
    try:
        source_db.backup(backup_db)
    finally:
        backup_db.close()
        source_db.close()

    for item in candidates:
        src = Path(item["rollout_path"])
        destination = backup_dir / "transcripts" / src.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, destination)
        manifest["transcripts"].append({"id": item["id"], "path": str(src), "backup": str(destination)})
    if index.is_file():
        index_backup = backup_dir / "session_index.jsonl"
        shutil.copy2(index, index_backup)
        manifest["index_backup"] = str(index_backup)
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    ids = [item["id"] for item in candidates]
    marks = ",".join("?" for _ in ids)
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        for table in ("guardian_review_feedback", "thread_attachments",
                      "thread_dynamic_tools", "thread_read_receipts"):
            con.execute(f"DELETE FROM {table} WHERE thread_id IN ({marks})", ids)
        con.execute(
            f"DELETE FROM thread_spawn_edges WHERE parent_thread_id IN ({marks}) "
            f"OR child_thread_id IN ({marks})", ids + ids
        )
        deleted = con.execute(f"DELETE FROM threads WHERE id IN ({marks})", ids).rowcount
        if deleted != len(ids):
            con.rollback()
            raise RuntimeError(f"Expected to delete {len(ids)} thread rows, deleted {deleted}")
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise
    finally:
        if con:
            con.close()

    # Delete only the exact transcript paths discovered above.
    for item in candidates:
        Path(item["rollout_path"]).unlink()

    if index.is_file():
        original = index.read_text(encoding="utf-8")
        kept = "".join(line for line in original.splitlines(keepends=True)
                        if not any(thread_id in line for thread_id in ids))
        atomic_write_text(index, kept)

    check = sqlite3.connect(db)
    try:
        remaining = check.execute(
            "SELECT COUNT(*) FROM threads WHERE id IN (" + marks + ")", ids
        ).fetchone()[0]
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if remaining != 0 or integrity != "ok":
            raise RuntimeError(f"Postcondition failed: remaining={remaining}, integrity={integrity}")
    finally:
        check.close()
    remaining_files = [item["rollout_path"] for item in candidates
                       if Path(item["rollout_path"]).exists()]
    if remaining_files:
        raise RuntimeError("Transcript files remain: " + ", ".join(remaining_files))
    if index.is_file():
        index_text = index.read_text(encoding="utf-8")
        leftovers = [thread_id for thread_id in ids if thread_id in index_text]
        if leftovers:
            raise RuntimeError("Session-index IDs remain: " + ", ".join(leftovers))

    result.update({
        "deleted_count": len(candidates),
        "deleted_transcripts": len(candidates),
        "restore_point": str(backup_dir),
        "integrity_check": "ok",
        "session_index_clean": True,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
