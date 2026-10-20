"""실제 저장소에 대해 stdio 조회를 점검한다. 제안·이미지·캐시는 저장하지 않는다."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]


async def run(session_id, image_id, region_id):
    parameters = StdioServerParameters(command=sys.executable, args=[str(ROOT / "scripts/run_editor_mcp.py")], cwd="/tmp")
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            names = [t.name for t in (await client.list_tools()).tools]
            projects = await client.call_tool("list_projects", {})
            page = await client.call_tool("inspect_page", {"session_id": session_id, "image_id": image_id})
            if page.isError:
                raise RuntimeError(str(page.content))
            result = page.structuredContent
            image = await client.call_tool("view_region", {"session_id": session_id, "image_id": image_id,
                "region_id": region_id, "expected_revision": result["page_revision"]})
            if image.isError:
                raise RuntimeError(str(image.content))
            return {"tools": names, "projects": projects.structuredContent["total"],
                    "session_id": session_id, "image_id": image_id,
                    "revision": result["page_revision"], "region_image": image.structuredContent,
                    "image_blocks": sum(c.type == "image" for c in image.content), "read_only": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("image_id")
    parser.add_argument("--region", type=int)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.session_id, args.image_id, args.region)), ensure_ascii=False, indent=2))
