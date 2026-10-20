import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
"""프로젝트 로컬 Codex/Claude MCP 설정만 추가한다. 기존 다른 서버·전역 설정은 보존한다."""
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
NAME = "comic-translator-review"
START = "# BEGIN comic-translator-review (scripts/configure_editor_mcp.py)"
END = "# END comic-translator-review"


def configure(root: Path = ROOT) -> list[Path]:
    root = root.resolve()
    python = root / ".venv/bin/python"
    server = root / "scripts/run_editor_mcp.py"
    if not python.is_file() or not server.is_file():
        raise ValueError("프로젝트 .venv와 scripts/run_editor_mcp.py를 먼저 준비하세요")
    codex_path, claude_path = root / ".codex/config.toml", root / ".mcp.json"
    for path in (codex_path, claude_path):
        if not path.resolve().is_relative_to(root):
            raise ValueError("로컬 설정 경로가 프로젝트 밖을 가리킵니다")
    old_toml = codex_path.read_text() if codex_path.exists() else ""
    parsed = tomllib.loads(old_toml)
    block = f'''{START}
[mcp_servers.{NAME}]
command = {json.dumps(str(python))}
args = [{json.dumps(str(server))}]
cwd = {json.dumps(str(root))}
startup_timeout_sec = 15
tool_timeout_sec = 60
enabled_tools = ["list_projects", "inspect_page", "view_region", "propose_review"]
{END}'''
    pattern = re.escape(START) + r".*?" + re.escape(END)
    if re.search(pattern, old_toml, re.S):
        new_toml = re.sub(pattern, lambda _: block, old_toml, flags=re.S)
    elif NAME in parsed.get("mcp_servers", {}):
        raise ValueError("같은 이름의 수동 Codex 설정이 있어 덮어쓰지 않았습니다")
    else:
        new_toml = old_toml.rstrip() + "\n\n" + block + "\n"
    tomllib.loads(new_toml)
    old_json = json.loads(claude_path.read_text()) if claude_path.exists() else {}
    command = {"type": "stdio", "command": str(python), "args": [str(server)]}
    existing = old_json.get("mcpServers", {}).get(NAME)
    if existing is not None and existing != command:
        raise ValueError("같은 이름의 다른 Claude 설정이 있어 덮어쓰지 않았습니다")
    new_json = {**old_json, "mcpServers": {**old_json.get("mcpServers", {}), NAME: command}}
    codex_path.parent.mkdir(parents=True, exist_ok=True)
    # 같은 설정으로 재실행할 때 파일 시각을 바꾸지 않는다.
    for path, text in ((codex_path, new_toml), (claude_path, json.dumps(new_json, ensure_ascii=False, indent=2) + "\n")):
        if not path.exists() or path.read_text() != text:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    return [codex_path, claude_path]


if __name__ == "__main__":
    for path in configure():
        print(path)
