"""Ore_3 커버의 MCP 준비/등록 왕복. 생성 호출은 대화의 내장 도구에서 별도로 수행한다."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SESSION = "20261020_083312_pages_13ba97f6"
PAGE = "00001_001_p01_C"
REPORT = ROOT / "proof/misc/ore3-cover-generation"


def hashes(session):
    base = ROOT / "temp_output/v2_sessions" / session
    # 생성 요청/후보를 제외한 원본 프로젝트와 입력·기존 최종 출력의 내용 보존 검사.
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in base.rglob("*") if p.is_file() and "image_generation" not in p.parts}


async def main(source=None):
    before = {sid: hashes(sid) for sid in (SESSION, "20260531_153718")}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "scripts/run_editor_mcp.py")], cwd=str(ROOT))
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as client:
            await client.initialize()
            async def call(name, **args):
                result = await client.call_tool(name, {"session_id": SESSION, "image_id": PAGE, **args})
                if result.isError:
                    raise RuntimeError(str(result.content))
                return result.structuredContent
            if source:
                request = json.loads((REPORT / "request.json").read_text())
                result = await call("register_generated_image", request_id=request["request_id"], source_path=str(Path(source).resolve()))
                (REPORT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print(json.dumps(result, ensure_ascii=False))
            else:
                page = await call("inspect_page")
                await call("recommend_image_generation", base_revision=page["page_revision"],
                           reason="사용자가 확인한 표지 페이지. 컬러 그림과 제목 장식이 겹쳐 전체 이미지 번역 후보로 권장. 자동 실행하지 않음.")
                request = await call("prepare_image_generation", base_revision=page["page_revision"],
                    additional_prompt="표지 1장만 편집합니다. 제목은 한국어 초안 ‘내 사망 플래그가 그칠 줄 모른다’로 배치하세요. 권수 3은 유지합니다. 크레딧의 직함은 만화 / 원작 / 캐릭터 원안으로 바꾸고, 인명은 확정 용어집이 없으므로 원문을 유지하세요. 영문 로마자 제목과 세 인물 및 노란 배경, 검은 제목 띠, 붉고 흰 제목의 인상을 보존하세요. 한국어 제목의 폰트는 원본과 비슷한 장식적 서체로 하되 새 로고·문구는 추가하지 마세요.")
                REPORT.mkdir(parents=True, exist_ok=True)
                (REPORT / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2))
                print(json.dumps(request, ensure_ascii=False))
            status = await call("inspect_image_generation")
            (REPORT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2))
    assert before == {sid: hashes(sid) for sid in before}, "원본 또는 기존 산출물이 변경됨"
    print("원본 프로젝트·입력·기존 출력 보존 확인")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source")
    args = parser.parse_args()
    asyncio.run(main(args.source))
