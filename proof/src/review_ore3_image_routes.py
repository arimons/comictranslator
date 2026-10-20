import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path, manifest_files
from pipeline.manual_guidance import load_json
"""유료 처리 없이 3권 전체의 색·에지 관찰값과 검토용 축소 이미지를 만든다. 경로를 확정하지 않는다."""
from pathlib import Path
import base64
import html
import io
import json

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SID = "20261020_083312_pages_13ba97f6"
OUT = PROOF_DIR / 'misc/ore3-route-review'

# 사용자가 이 책의 개별 페이지에 확인한 선택이다. 범용 자동 분류 기준이나 생성 실행 승인이 아니다.
PROPOSALS = {
    1: ("필수", "사용자 확인: 표지는 원래 대상. 장식 제목과 그림의 겹침"),
    3: ("필수", "사용자 확인: 컬러+투명 말풍선으로 필수. 본문 폰트 일관성 검토 필요"),
    162: ("선택", "사용자 확인: 처리하고 싶은 후기이나 필수는 아님"),
    163: ("선택", "사용자 확인: 처리하고 싶은 후기이나 필수는 아님"),
    164: ("선택", "사용자 확인: 처리하고 싶은 광고이나 필수는 아님"),
    165: ("선택", "사용자 확인: 처리하고 싶은 광고이나 필수는 아님"),
    2: ("목차 선택", "사용자 확인: 번역한다면 일본어에 한정. 원본 유지도 허용"),
    5: ("일반 처리 우선", "사용자 확인: 텍스트 밀도가 매우 낮아 전체 재생성 불필요"),
    4: ("일반 처리 우선", "관찰 제안: 컬러지만 주요 글자가 흰 말풍선·여백에 있음"),
    154: ("일반 처리 우선", "관찰 제안: 컬러 풍경 자체에는 대상 글자가 없고 대사는 아래 흰 영역"),
}
USER_REVIEWED = {1, 2, 3, 5, 162, 163, 164, 165}


def main():
    session = json.loads((manifest_path(PROJECTS_DIR / SID)).read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    pages = []
    thumbs = []
    for index, item in enumerate(session["images"], 1):
        with Image.open(item["input_path"]) as original:
            image = original.convert("RGB")
            image.thumbnail((600, 800))
        pixels = np.asarray(image)
        # 낮은 채도의 압축 잡음·옅은 워터마크와 컬러 페이지를 구분하기 위한 관찰값.
        spread = pixels.max(axis=2).astype(float) - pixels.min(axis=2)
        hsv = cv2.cvtColor(pixels, cv2.COLOR_RGB2HSV)
        color_ratio = float(((spread >= 24) & (hsv[:, :, 1] >= 45) & (hsv[:, :, 2] >= 40)).mean())
        gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
        edge_ratio = float((cv2.Canny(gray, 80, 180) > 0).mean())
        white_ratio = float((gray >= 242).mean())
        colored = color_ratio >= .08
        label = "컬러 검토 후보" if colored else "복잡한 흑백 비교군" if edge_ratio >= .15 else "일반 비교군"
        buffer = io.BytesIO()
        thumb = image.copy()
        thumb.thumbnail((220, 290))
        thumb.save(buffer, "JPEG", quality=85)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
        proposal, reason = PROPOSALS.get(index, ("일반 처리 우선", "전체 축소본에서 일반 본문 형태로 관찰. 실제 분석 성공 여부는 아직 미검증"))
        pages.append({"page": index, "image_id": item["id"], "source": item.get("parent_name"), "side": item.get("page_side"),
                      "color_ratio": round(color_ratio, 5), "edge_ratio": round(edge_ratio, 5), "white_ratio": round(white_ratio, 5),
                      "inspection_group": label, "route_proposal": proposal, "reason": reason,
                      "input_uri": Path(item["input_path"]).as_uri(), "verdict": "selection_confirmed_by_user" if index in USER_REVIEWED else "not_validated", "thumbnail": data_url})
        thumbs.append(thumb)
    for start in range(0, len(pages), 35):
        sheet = Image.new("RGB", (1100, 7 * 250), "#e9edf2")
        draw = ImageDraw.Draw(sheet)
        for offset, page in enumerate(pages[start:start + 35]):
            x, y = (offset % 5) * 220, (offset // 5) * 250
            thumb = thumbs[start + offset].copy()
            thumb.thumbnail((204, 212))
            sheet.paste(thumb, (x + (220 - thumb.width) // 2, y + 25))
            draw.text((x + 6, y + 5), f"P{page['page']:03d} {page['source']} {page['side']}", fill="black")
            draw.text((x + 6, y + 232), f"C {page['color_ratio']:.1%}  E {page['edge_ratio']:.1%}", fill="black")
        sheet.save(OUT / f"all_{start // 35 + 1}.jpg", quality=90)
    serial = [{key: value for key, value in page.items() if key != "thumbnail"} for page in pages]
    (OUT / "observations.json").write_text(json.dumps({"session_id": SID, "status": "user_reviewed_named_pages", "automatic_classifier_implemented": False, "generation_executed": False, "font_policy": "pending", "paid_calls": 0, "pages": serial}, ensure_ascii=False, indent=2))
    cards = []
    priority = {"필수": 0, "선택": 1, "목차 선택": 2, "일반 처리 우선": 3}
    for page in sorted(pages, key=lambda row: (priority[row["route_proposal"]], row["page"])):
        cards.append(f'<article data-group="{page["route_proposal"]}"><h3>P{page["page"]:03d} · {html.escape(page["source"] or "")} · {html.escape(page["side"] or "")}</h3><a href="{page["input_uri"]}" target="_blank"><img src="{page["thumbnail"]}" alt="P{page["page"]:03d} 원본"></a><p><strong>{page["route_proposal"]} </strong></p><p>{page["reason"]}</p><p>컬러 {page["color_ratio"]:.1%} · 에지 {page["edge_ratio"]:.1%} · 밝은 영역 {page["white_ratio"]:.1%}</p><small>{html.escape(page["image_id"])}</small></article>')
    document = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>3권 이미지 생성 후보 검토</title><style>body{font-family:system-ui;margin:28px;background:#f5f6f8;color:#17202c}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}article{background:white;border:1px solid #d8dce3;border-radius:8px;padding:14px}h3{font-size:14px}img{display:block;height:290px;max-width:100%;object-fit:contain;margin:auto}p{font-size:13px;line-height:1.6}small{overflow-wrap:anywhere;color:#667085}</style><h1>사망플래그 3권 · 이미지 생성 후보 검토</h1><p>실제 자동 선별이 적용된 결과가 아닙니다. 165장의 로컬 관찰값을 컬러 비율순으로 정렬한 비교 자료입니다. 유료 분석·이미지 생성·최종 경로 변경은 하지 않았습니다.</p><p>컬러 8% 기준은 탐색용입니다. 에지는 축소 이미지에서 관찰한 선의 밀도이며 번역 난이도나 재생성 필요성의 정답 점수가 아닙니다. 원본 크기의 내용과 글자/그림의 겹침을 함께 확인해야 합니다.</p><main>' + ''.join(cards) + '</main></html>'
    document = document.replace('컬러 비율순으로 정렬한 비교 자료', '필수 2장·선택 4장·목차 선택 1장·일반 처리 우선 158장으로 묶은 사용자 검토 자료. 개별 선택 확인과 생성 실행 승인은 별개이며, 나머지 일반 페이지의 처리 성공 여부는 아직 미검증')
    document = document.replace('<main>', '<label>보기 <select onchange="document.querySelectorAll(\'article\').forEach(card=>card.hidden=this.value!==\'전체\'&&card.dataset.group!==this.value)"><option>전체</option><option>필수</option><option>선택</option><option>목차 선택</option><option>일반 처리 우선</option></select></label><p>썸네일을 누르면 원본 파일을 엽니다. P 번호는 자동분할 프로젝트의 1부터 시작하는 이미지 순서입니다.</p><main>')
    (OUT / "index.html").write_text(document)
    order = [1, 3, 162, 163, 164, 165, 2, 5, 4, 154]
    sheet = Image.new("RGB", (1400, 800), "#eef1f5")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", 18)
    for offset, number in enumerate(order):
        x, y = offset % 5 * 280, offset // 5 * 400
        thumb = thumbs[number - 1].copy()
        thumb.thumbnail((254, 340))
        sheet.paste(thumb, (x + (280 - thumb.width) // 2, y + 40))
        group = pages[number - 1]["route_proposal"]
        draw.text((x + 12, y + 12), f"P{number:03d}  {group}", fill="#1647a5", font=font)
    sheet.save(OUT / "shortlist.jpg", quality=92)
    for page in sorted(serial, key=lambda row: -row["color_ratio"])[:18]:
        print(json.dumps(page, ensure_ascii=False))


if __name__ == "__main__":
    main()
