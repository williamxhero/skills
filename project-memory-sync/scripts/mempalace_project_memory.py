from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from mempalace.config import MempalaceConfig, sanitize_content, sanitize_name
from mempalace.palace import get_collection
from mempalace.searcher import search_memories


PROJECT_MAP_DIR = Path.home() / ".codex" / "mempalace-project-memory"
PROJECT_MAP_FILE = PROJECT_MAP_DIR / "projects.json"
DEFAULT_RESULTS = 5


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    options: dict[str, str]
    flags: set[str]


@dataclass(frozen=True)
class ProjectContext:
    wing: str
    project_root: str
    project_key: str


@dataclass(frozen=True)
class RememberRequest:
    context: ProjectContext
    room: str
    title: str
    keywords: str
    content: str


def main() -> int:
    try:
        command = parse_command(sys.argv[1:])
        if command.name == "wing":
            return handle_wing(command)
        if command.name == "remember":
            return handle_remember(command)
        if command.name == "search":
            return handle_search(command)
        print_json({"success": False, "error": f"不支持的命令: {command.name}"})
        return 1
    except Exception as exc:
        print_json({"success": False, "error": str(exc)})
        return 1


def parse_command(argv: list[str]) -> ParsedCommand:
    if not argv:
        raise ValueError("缺少命令")
    name = argv[0]
    options: dict[str, str] = {}
    flags: set[str] = set()
    index = 1
    while index < len(argv):
        part = argv[index]
        if not part.startswith("--"):
            raise ValueError(f"无法识别的参数: {part}")
        key = part[2:]
        if key == "":
            raise ValueError("空参数名")
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            flags.add(key)
            index += 1
            continue
        options[key] = argv[index + 1]
        index += 2
    return ParsedCommand(name=name, options=options, flags=flags)


def handle_wing(command: ParsedCommand) -> int:
    context = resolve_project_context(command.options, command.flags, require_project=False)
    print_json(
        {
            "success": True,
            "wing": context.wing,
            "project_root": context.project_root,
            "project_key": context.project_key,
        }
    )
    return 0


def handle_remember(command: ParsedCommand) -> int:
    context = resolve_project_context(command.options, command.flags, require_project=False)
    room = normalize_room_name(require_option(command.options, "room"))
    title = require_option(command.options, "title").strip()
    if title == "":
        raise ValueError("title 不能为空")
    keywords = normalize_keywords(command.options.get("keywords", ""))
    content = read_content(command)
    request = RememberRequest(
        context=context,
        room=room,
        title=title,
        keywords=keywords,
        content=content,
    )
    result = upsert_memory(request)
    print_json(result)
    return 0 if result["success"] else 1


def handle_search(command: ParsedCommand) -> int:
    query = require_option(command.options, "query")
    results_text = command.options.get("results", str(DEFAULT_RESULTS))
    results = parse_positive_int(results_text, "results")
    room = ""
    if "room" in command.options:
        room = normalize_room_name(command.options["room"])
    wing = ""
    project_root = ""
    project_key = ""
    if "global" not in command.flags:
        context = resolve_project_context(command.options, command.flags, require_project=False)
        wing = context.wing
        project_root = context.project_root
        project_key = context.project_key
    config = MempalaceConfig()
    search_result = search_memories(
        query=query,
        palace_path=config.palace_path,
        wing=wing or None,
        room=room or None,
        n_results=results,
    )
    payload = {
        "success": "error" not in search_result,
        "query": query,
        "wing": wing,
        "room": room,
        "project_root": project_root,
        "project_key": project_key,
        "results": search_result.get("results", []),
    }
    if "error" in search_result:
        payload["error"] = search_result["error"]
        if "hint" in search_result:
            payload["hint"] = search_result["hint"]
    print_json(payload)
    return 0 if payload["success"] else 1


def resolve_project_context(
    options: dict[str, str], flags: set[str], require_project: bool
) -> ProjectContext:
    if "shared" in flags:
        return ProjectContext(
            wing="coding",
            project_root="",
            project_key="name::coding",
        )
    project_root = normalize_project_root(options.get("project-root", ""))
    explicit_wing = options.get("wing", "")
    explicit_project_name = options.get("project-name", "")
    project_key = build_project_key(project_root, explicit_project_name)
    registry = load_project_registry()

    wing = ""
    if explicit_wing != "":
        wing = normalize_wing_name(explicit_wing)
    elif project_key != "" and project_key in registry:
        wing = registry[project_key]["wing"]
    elif explicit_project_name != "":
        wing = normalize_wing_name(explicit_project_name)
    elif project_root != "":
        wing = normalize_wing_name(Path(project_root).name)

    if wing == "":
        if require_project:
            raise ValueError("缺少项目信息，请提供 --project-root、--project-name 或 --wing")
        current_root = normalize_project_root(str(Path.cwd()))
        if current_root != "":
            project_root = current_root
            project_key = build_project_key(project_root, explicit_project_name)
            wing = normalize_wing_name(Path(project_root).name)

    if wing == "":
        raise ValueError("无法确定项目 wing，请提供 --project-root、--project-name 或 --wing")

    if project_key != "":
        registry[project_key] = {
            "wing": wing,
            "project_root": project_root,
        }
        save_project_registry(registry)

    return ProjectContext(
        wing=wing,
        project_root=project_root,
        project_key=project_key,
    )


def normalize_project_root(raw_value: str) -> str:
    value = raw_value.strip()
    if value == "":
        return ""
    return str(Path(value).resolve())


def build_project_key(project_root: str, explicit_project_name: str) -> str:
    if project_root != "":
        return project_root.lower()
    if explicit_project_name != "":
        return f"name::{normalize_wing_name(explicit_project_name)}"
    return ""


def normalize_wing_name(raw_value: str) -> str:
    value = normalize_name(raw_value, "wing")
    return sanitize_name(value, "wing")


def normalize_room_name(raw_value: str) -> str:
    value = normalize_name(raw_value, "room")
    return sanitize_name(value, "room")


def normalize_name(raw_value: str, field_name: str) -> str:
    value = raw_value.strip().lower().replace("_", "-")
    value = re.sub(r"[^\w.\- ]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    value = value.strip("-. ")
    if value == "":
        raise ValueError(f"{field_name} 不能为空")
    return value


def normalize_keywords(raw_value: str) -> str:
    if raw_value.strip() == "":
        return ""
    values: list[str] = []
    for part in raw_value.split(","):
        keyword = part.strip()
        if keyword == "":
            continue
        values.append(keyword)
    return ", ".join(values)


def read_content(command: ParsedCommand) -> str:
    has_stdin = "stdin" in command.flags
    has_inline = "content" in command.options
    has_file = "content-file" in command.options
    modes = int(has_stdin) + int(has_inline) + int(has_file)
    if modes != 1:
        raise ValueError("remember 必须且只能使用一种内容输入：--stdin、--content 或 --content-file")
    if has_stdin:
        return sanitize_content(sys.stdin.read().strip())
    if has_inline:
        return sanitize_content(command.options["content"].strip())
    file_path = Path(command.options["content-file"]).resolve()
    return sanitize_content(file_path.read_text(encoding="utf-8").strip())


def upsert_memory(request: RememberRequest) -> dict[str, object]:
    config = MempalaceConfig()
    collection = get_collection(config.palace_path, create=True)
    now_text = datetime.now().isoformat(timespec="seconds")
    drawer_id = build_drawer_id(request)
    document = build_document(request, now_text)
    source_file = build_source_file(request)
    collection.upsert(
        ids=[drawer_id],
        documents=[document],
        metadatas=[
            {
                "wing": request.context.wing,
                "room": request.room,
                "source_file": source_file,
                "chunk_index": 0,
                "added_by": "codex-project-memory-sync",
                "filed_at": now_text,
                "title": request.title,
                "keywords": request.keywords,
            }
        ],
    )
    return {
        "success": True,
        "drawer_id": drawer_id,
        "wing": request.context.wing,
        "room": request.room,
        "title": request.title,
        "keywords": request.keywords,
        "project_root": request.context.project_root,
        "source_file": source_file,
    }


def build_drawer_id(request: RememberRequest) -> str:
    text = f"{request.context.wing}|{request.room}|{request.title}"
    suffix = sha256(text.encode("utf-8")).hexdigest()[:24]
    return f"drawer_manual_{suffix}"


def build_source_file(request: RememberRequest) -> str:
    title_slug = normalize_name(request.title, "title")
    if request.context.project_root != "":
        return (
            f"{request.context.project_root}/.codex/mempalace-memory/"
            f"{request.room}/{title_slug}.md"
        )
    return f"virtual://{request.context.wing}/{request.room}/{title_slug}.md"


def build_document(request: RememberRequest, now_text: str) -> str:
    project_root = request.context.project_root if request.context.project_root != "" else "(未绑定路径)"
    keywords = request.keywords if request.keywords != "" else "(无)"
    return "\n".join(
        [
            f"标题: {request.title}",
            f"项目: {request.context.wing}",
            f"分类: {request.room}",
            f"关键词: {keywords}",
            f"项目路径: {project_root}",
            f"更新时间: {now_text}",
            "",
            "内容:",
            request.content,
        ]
    )


def load_project_registry() -> dict[str, dict[str, str]]:
    if not PROJECT_MAP_FILE.exists():
        return {}
    content = PROJECT_MAP_FILE.read_text(encoding="utf-8").strip()
    if content == "":
        return {}
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("projects.json 格式错误")
    result: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, dict):
            wing = str(value.get("wing", ""))
            project_root = str(value.get("project_root", ""))
            result[key] = {
                "wing": wing,
                "project_root": project_root,
            }
    return result


def save_project_registry(registry: dict[str, dict[str, str]]) -> None:
    PROJECT_MAP_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_MAP_FILE.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def require_option(options: dict[str, str], key: str) -> str:
    value = options.get(key, "").strip()
    if value == "":
        raise ValueError(f"缺少参数 --{key}")
    return value


def parse_positive_int(raw_value: str, field_name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是整数") from exc
    if value <= 0:
        raise ValueError(f"{field_name} 必须大于 0")
    return value


def print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
