from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import RunnerError

TRACKER_SCHEMA = "spec-runner-tracker/v1"
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


@dataclass(frozen=True)
class IssueRecord:
    key: str
    source: str
    repository: str
    path: str
    external_id: str
    kind: str
    title: str
    body: str
    comments: tuple[str, ...]
    parent: str | None
    blocked_by: tuple[str, ...]
    revision: str
    digest: str

    def public(self) -> dict[str, object]:
        value = asdict(self)
        value["comments"] = list(self.comments)
        value["blocked_by"] = list(self.blocked_by)
        return value


@dataclass(frozen=True)
class PlanSnapshot:
    schema_version: str
    source: str
    root: str
    relation_mode: str
    records: tuple[IssueRecord, ...]
    digest: str

    def public(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "root": self.root,
            "relation_mode": self.relation_mode,
            "digest": self.digest,
            "records": [record.public() for record in self.records],
        }


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_root(root: Path) -> Path:
    root = root.expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise RunnerError("invalid_tracker_root", "tracker root must be an existing non-symlink directory")
    return root


def _parse_value(value: str, field: str) -> Any:
    value = value.strip()
    if value.startswith("!") or "&" in value or "*" in value:
        raise RunnerError("invalid_tracker_frontmatter", f"unsafe YAML feature in {field}")
    if value in {"null", "~", ""}:
        return None if value != "" else ""
    if value.startswith("[") or value.startswith("{") or value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise RunnerError("invalid_tracker_frontmatter", f"{field} must use JSON-style list/string syntax") from exc
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _frontmatter(path: Path, raw: bytes) -> tuple[dict[str, Any], str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RunnerError("invalid_tracker_encoding", f"tracker file is not UTF-8: {path}") from exc
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise RunnerError("invalid_tracker_frontmatter", f"missing frontmatter: {path}")
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise RunnerError("invalid_tracker_frontmatter", f"unterminated frontmatter: {path}")
    metadata: dict[str, Any] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise RunnerError("invalid_tracker_frontmatter", f"invalid metadata line in {path}")
        field, value = line.split(":", 1)
        field = field.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field) or field in metadata:
            raise RunnerError("invalid_tracker_frontmatter", f"invalid or duplicate metadata field: {field}")
        metadata[field] = _parse_value(value, field)
    return metadata, "".join(lines[end + 1 :])


def _record(root: Path, path: Path) -> IssueRecord:
    if path.is_symlink():
        raise RunnerError("tracker_path_escape", f"tracker file is a symbolic link: {path.name}")
    raw = path.read_bytes()
    metadata, body = _frontmatter(path, raw)
    required = {"key", "kind", "title", "revision"}
    missing = sorted(required - metadata.keys())
    if missing:
        raise RunnerError("invalid_tracker_frontmatter", f"missing fields: {', '.join(missing)}")
    key = metadata["key"]
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise RunnerError("invalid_tracker_key", f"invalid issue key: {key!r}")
    kind = metadata["kind"]
    title = metadata["title"]
    revision = metadata["revision"]
    if not all(isinstance(value, str) and value.strip() for value in (kind, title, revision)):
        raise RunnerError("invalid_tracker_frontmatter", "kind, title, and revision must be non-empty strings")
    parent = metadata.get("parent")
    if parent is not None and not isinstance(parent, str):
        raise RunnerError("invalid_tracker_relation", "parent must be a key or null")
    blocked_by = metadata.get("blocked_by", [])
    if not isinstance(blocked_by, list) or any(not isinstance(value, str) for value in blocked_by):
        raise RunnerError("invalid_tracker_relation", "blocked_by must be a JSON-style list of keys")
    comments = metadata.get("comments", [])
    if not isinstance(comments, list) or any(not isinstance(value, str) for value in comments):
        raise RunnerError("invalid_tracker_frontmatter", "comments must be a JSON-style list of strings")
    relative = path.relative_to(root).as_posix()
    return IssueRecord(
        key=key,
        source="local",
        repository=str(root),
        path=relative,
        external_id=f"local:{key}",
        kind=kind,
        title=title,
        body=body,
        comments=tuple(comments),
        parent=parent,
        blocked_by=tuple(blocked_by),
        revision=revision,
        digest=_digest(raw),
    )


def _validate_graph(records: tuple[IssueRecord, ...]) -> None:
    by_key = {record.key: record for record in records}
    if len(by_key) != len(records):
        raise RunnerError("duplicate_tracker_key", "tracker contains duplicate issue keys")
    edges: dict[str, set[str]] = {key: set() for key in by_key}
    for record in records:
        dependencies = list(record.blocked_by) + ([record.parent] if record.parent else [])
        for dependency in dependencies:
            if dependency not in by_key:
                raise RunnerError("unknown_tracker_dependency", f"{record.key} references unknown {dependency}")
            if dependency == record.key:
                raise RunnerError("tracker_cycle", f"{record.key} depends on itself")
            edges[record.key].add(dependency)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise RunnerError("tracker_cycle", f"dependency cycle contains {key}")
        if key in visited:
            return
        visiting.add(key)
        for dependency in edges[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in by_key:
        visit(key)


def read_local(root: Path, *, keys: set[str] | None = None) -> PlanSnapshot:
    root = _safe_root(root)
    files: list[Path] = []
    seen_casefold: set[str] = set()
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if any(part == ".." or Path(part).is_absolute() for part in relative.parts):
            raise RunnerError("tracker_path_escape", "tracker path escapes its root")
        if path.is_symlink() or any(component.is_symlink() for component in [root, *path.parents]):
            raise RunnerError("tracker_path_escape", f"tracker path follows a symbolic link: {relative}")
        folded = relative.as_posix().casefold()
        if folded in seen_casefold:
            raise RunnerError("tracker_case_collision", f"case-insensitive path collision: {relative}")
        seen_casefold.add(folded)
        files.append(path)
    records = tuple(sorted((_record(root, path) for path in files), key=lambda record: record.key))
    if keys is not None:
        records = tuple(record for record in records if record.key in keys)
        if {record.key for record in records} != keys:
            raise RunnerError("tracker_close_evidence_missing", "publication source records are missing")
    _validate_graph(records)
    digest = _digest("\n".join(f"{record.key}:{record.digest}" for record in records).encode("utf-8"))
    return PlanSnapshot(TRACKER_SCHEMA, "local", os.fspath(root), "local", records, digest)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def publish_local(
    snapshot: PlanSnapshot, target_root: Path, *, operation_id: str,
    adopt_matching_revision: bool = False,
) -> dict[str, object]:
    target_root = target_root.expanduser()
    if target_root.exists() and target_root.is_symlink():
        raise RunnerError("tracker_path_escape", "publish target cannot be a symbolic link")
    target_root = target_root.resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    receipt_path = target_root / ".spec-runner-tracker-receipts.json"
    if receipt_path.is_symlink():
        raise RunnerError("tracker_path_escape", "tracker receipt file cannot be a symbolic link")
    receipts: dict[str, Any] = {}
    if receipt_path.exists():
        try:
            receipts = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError("tracker_receipt_corrupt", "tracker receipt file is not valid JSON") from exc
    if not isinstance(receipts, dict):
        raise RunnerError("tracker_receipt_corrupt", "tracker receipt root must be an object")
    previous = receipts.get(operation_id)
    if previous is not None and not isinstance(previous, dict):
        raise RunnerError("tracker_receipt_corrupt", "tracker operation receipt must be an object")
    if previous is not None:
        if not isinstance(previous.get("snapshot_digest"), str):
            raise RunnerError("tracker_receipt_corrupt", "tracker operation receipt has no snapshot digest")
        if previous["snapshot_digest"] != snapshot.digest:
            raise RunnerError("tracker_operation_conflict", "operation_id was reused with a different snapshot")
        items = previous.get("records")
        if (previous.get("relation_mode") != "local" or not isinstance(items, list)
                or len(items) != len(snapshot.records)):
            raise RunnerError("tracker_receipt_corrupt", "tracker operation receipt has invalid records")
        for record, item in zip(snapshot.records, items):
            filename = re.sub(r"[^A-Za-z0-9._-]+", "-", record.key).strip("-") + ".md"
            if (not isinstance(item, dict) or item.get("key") != record.key
                    or item.get("path") != filename or not isinstance(item.get("digest"), str)
                    or not isinstance(item.get("adopted"), bool)):
                raise RunnerError("tracker_receipt_corrupt", "tracker operation receipt changed identity")
            destination = target_root / filename
            if not destination.is_file() or _record(target_root, destination).digest != item["digest"]:
                raise RunnerError("tracker_revision_conflict", f"published file changed since receipt: {record.key}")
        return {"created": False, "operation_id": operation_id, "receipt": previous, "relation_mode": "local"}

    published: list[dict[str, object]] = []
    for record in snapshot.records:
        filename = re.sub(r"[^A-Za-z0-9._-]+", "-", record.key).strip("-") + ".md"
        destination = target_root / filename
        if destination.exists():
            existing = _record(target_root, destination)
            matching_revision = adopt_matching_revision and all((
                existing.key == record.key,
                existing.kind == record.kind,
                existing.title == record.title,
                existing.body == record.body,
                existing.parent == record.parent,
                existing.blocked_by == record.blocked_by,
                existing.revision == record.revision,
            ))
            if existing.digest != record.digest and not matching_revision:
                raise RunnerError("tracker_revision_conflict", f"existing file conflicts with {record.key}")
            published.append({"key": record.key, "path": destination.name, "digest": existing.digest, "adopted": True})
            continue
        source_path = Path(record.repository) / record.path
        _atomic_write(destination, source_path.read_bytes())
        published.append({"key": record.key, "path": destination.name, "digest": record.digest, "adopted": False})
    receipt = {"snapshot_digest": snapshot.digest, "records": published, "relation_mode": "local"}
    receipts[operation_id] = receipt
    _atomic_write(receipt_path, (json.dumps(receipts, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"created": True, "operation_id": operation_id, "receipt": receipt, "relation_mode": "local"}


def read_local_states(target_root: Path) -> dict[str, dict[str, object]]:
    """Read lifecycle state without changing immutable published plan files."""
    target_root = target_root.expanduser()
    if target_root.is_symlink():
        raise RunnerError("tracker_path_escape", "state target cannot be a symbolic link")
    target_root = _safe_root(target_root)
    path = target_root / ".spec-runner-tracker-states.json"
    document = _local_state_document(path)
    states = document["states"]
    for key, state in states.items():
        if (not isinstance(state, dict) or state.get("key") != key
                or state.get("state") not in {"open", "closed"}
                or not isinstance(state.get("path"), str)
                or not state["path"] or Path(state["path"]).name != state["path"]):
            raise RunnerError("tracker_state_corrupt", "local issue state has invalid identity")
        try:
            record = _record(target_root, target_root / state["path"])
        except OSError as exc:
            raise RunnerError("tracker_revision_conflict", f"closed record is unreadable: {key}") from exc
        if record.key != key or record.digest != state.get("digest") or record.revision != state.get("revision"):
            raise RunnerError("tracker_revision_conflict", f"issue state no longer matches published record: {key}")
    return states


def _local_state_document(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise RunnerError("tracker_path_escape", "local state file cannot be a symbolic link")
    if not path.exists():
        return {"schema_version": "spec-runner-local-states/v1", "states": {}, "operations": {}}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("tracker_state_corrupt", "local state file is not readable JSON") from exc
    if (not isinstance(document, dict) or document.get("schema_version") != "spec-runner-local-states/v1"
            or not isinstance(document.get("states"), dict) or not isinstance(document.get("operations"), dict)):
        raise RunnerError("tracker_state_corrupt", "local state file has an invalid schema")
    return document


def close_local(snapshot: PlanSnapshot, target_root: Path, *, publication_operation_id: str,
                operation_id: str, delivery_digest: str, keys: set[str] | None = None) -> dict[str, object]:
    """Atomically close one published SPEC and its tickets, then read it back."""
    if not snapshot.records or not operation_id or not delivery_digest:
        raise RunnerError("tracker_close_evidence_missing", "local closure requires published records and delivery identity")
    target_root = target_root.expanduser()
    if target_root.is_symlink():
        raise RunnerError("tracker_path_escape", "closure target cannot be a symbolic link")
    target_root = _safe_root(target_root)
    publication_path = target_root / ".spec-runner-tracker-receipts.json"
    if publication_path.is_symlink():
        raise RunnerError("tracker_path_escape", "publication receipt cannot be a symbolic link")
    try:
        publications = json.loads(publication_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("tracker_close_evidence_missing", "local closure has no readable publication receipt") from exc
    if not isinstance(publications, dict) or publication_operation_id not in publications:
        raise RunnerError("tracker_close_evidence_missing", "local closure has no matching publication operation")
    publication = publish_local(snapshot, target_root, operation_id=publication_operation_id)["receipt"]
    if keys is not None and (not keys or not keys <= {record.key for record in snapshot.records}):
        raise RunnerError("tracker_close_evidence_missing", "closure scope is not part of the published plan")
    records = [
        {"key": record.key, "path": item["path"], "digest": item["digest"],
         "revision": record.revision, "state": "closed"}
        for record, item in zip(snapshot.records, publication["records"])
        if keys is None or record.key in keys
    ]
    receipt = {"complete": True, "relation_mode": "local", "operation_id": operation_id,
               "publication_operation_id": publication_operation_id,
               "snapshot_digest": snapshot.digest, "delivery_digest": delivery_digest, "records": records}
    state_path = target_root / ".spec-runner-tracker-states.json"
    document = _local_state_document(state_path)
    previous = document["operations"].get(operation_id)
    if previous is not None and previous != receipt:
        raise RunnerError("tracker_operation_conflict", "local closure operation was reused with different evidence")
    states = read_local_states(target_root)
    for record in records:
        existing = states.get(record["key"])
        if existing is not None and existing != record:
            raise RunnerError("tracker_state_conflict", "published issue state changed before closure readback")
        if previous is not None and existing is None:
            raise RunnerError("tracker_state_conflict", "completed closure lost its issue state")
    if previous is None:
        document["operations"][operation_id] = receipt
        document["states"].update({record["key"]: record for record in records})
        try:
            _atomic_write(state_path, (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        except OSError as exc:
            raise RunnerError("tracker_close_unconfirmed", "local closure state could not be persisted") from exc
    readback = _local_state_document(state_path)
    states = read_local_states(target_root)
    if readback["operations"].get(operation_id) != receipt or any(states.get(record["key"]) != record for record in records):
        raise RunnerError("tracker_close_unconfirmed", "local closure did not return matching state readbacks")
    return receipt
