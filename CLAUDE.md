# Comic Translator 작업 가이드 (Claude Code)

현재 프로젝트 기준은 `AGENTS.md`(파이프라인·파일 구조), `docs/CODEBASE_FILE_MAP.md`(파일별 상세), `docs/AGENT_WORKSPACE_RULES.md`(폴더 규칙)를 따른다.

현재 영역 용어는 **경계 기반 / 텍스트 기반**, 화면 ID는 **R 번호 + 타입 배지**다. 변수명과 기존 JSON·캐시 호환 정책은 [영역 처리 기준 변경 기록](docs/REGION_PROCESSING_BASIS_CHANGELOG.md)을 따른다. 저장된 `bubble/unboxed`와 좌표 키를 전역 치환하지 않는다.

로컬 MCP `comic-translator-review`는 조회 3개와 검토 초안 저장만 제공한다. [편집기 검토 MCP](docs/editor_mcp.md)의 순서대로 원본·산출물을 함께 보고 사용자에게 더블체크한다. 프로젝트 `.mcp.json`은 `python configure_editor_mcp.py`로 생성하며, 초안 저장은 실제 보정 적용이 아니다.

---

## 용어 정리 (Glossary)

| 용어 | 의미 |
|---|---|
| **ATG** (Anchor Text Group) | Cloud Vision이 감지한 텍스트 블록을 clustering한 단위. 말풍선 하나에 대응. `*_text_groups.json`의 각 항목. |
| **Anchor** | ATG의 대표 bounding box. flood fill 시드 포인트 출발점. |
| **bbox** | bounding box. Cloud Vision/clustering이 반환하는 직사각형 영역. |
| **processing_basis** | 처리 기준: `boundary_based`(경계 기반) / `text_based`(텍스트 기반). 기존 저장 `type: bubble/unboxed`를 호환 함수로 읽는다. Edge는 처리 타입이 아니라 검출 방법이다. |
| **flood region** | 하나의 ATG anchor에서 floodfill로 확장된 실제 픽셀 영역. 말풍선 내부 전체를 커버. |
| **flood_rect** | flood region의 최소 외접 직사각형. |
| **flood_pre_masked.jpg** | `create_floodfill_fallback_outputs()`가 만드는 flood 기반 마스킹 결과. Step 5 masking의 입력 베이스. |
| **region_type** | 검출기 내부 후보 분류(최종 `processing_basis`와 별개): `in_bubble` (말풍선 내부) / `narration_like` (나레이션 박스) / `noise_like` (효과음·손글씨). |
| **inflection split** | 하나의 flood region에 여러 ATG가 포함될 때 convexity defect 쌍으로 가르는 분리 방법. |
| **erosion split** | inflection split 실패 시 fallback. 점진적 erosion으로 CC가 분리될 때까지 침식 후 Voronoi 할당. |
| **integrated JSON** | `*_integrated.json`. Gemini OCR+번역 결과 + 각 ATG의 번역문·배치 정보를 담은 최종 JSON. |
| **flood_vis.json** | `visualize_anchor_floodfill()` 결과. overlay bbox 선택 + 난이도 점수 포함. Step 6 Text Overlay 입력. |

---

## 파이프라인 흐름

```
Step 1  Cloud Vision          이미지 → *_cv.json
           ↓ (배치: 9장 그리드 묶음)
Step 2  Text Grouping         *_cv.json → *_text_groups.json
           ↓
Step 3  FloodFill             *_text_groups.json → *_flood_vis.json (overlay용)
         └─ 3-B split/OCR sheet                     → *_flood_regions.json + *_flood_indexed_sheet.jpg
         └─ 3-C masking                             → *_flood_pre_masked.jpg (masking용)
           ↓
Step 4  Gemini OCR+번역       indexed OCR sheet + flood_regions → *_integrated.json
           ↓
Step 5  White Masking         flood_pre_masked.jpg 승격 (없으면 bbox fallback) → *_masked.jpg
           ↓
Step 6  Text Overlay          masked.jpg + integrated + flood_regions + flood_vis → *_final.jpg
```

표준 산출물: `*_cv.json`, `*_text_groups.json`, `*_integrated.json`, `*_masked.jpg`, `*_final.jpg`

레거시 (현재 파이프라인 외): `*_ocr_v5.json`, `*_integrated_translated.json`

아카이브된 파일 (`archive/`): `integrate_ocr_v3.py`, `gemini_api.py`, `bbox_utils.py`, `preprocess_for_cv.py`, `gemini_batch_api.py`, `cluster_cv_v5.py`

Gemini Batch API 관련은 `batch/` 폴더에 보존하며 메인 파이프라인과 분리.

---

## 작업 폴더 규칙

작업 폴더 규칙은 에이전트 공용 문서인 `docs/AGENT_WORKSPACE_RULES.md`를 따른다.
이 파일에는 폴더 규칙을 중복으로 추가하지 않는다.

---

## 실행 방법

```bash
# 환경 활성화
. .venv/bin/activate

# 단일 이미지 (work-dir=proof/, output=proof/)
python3 run_pipeline.py --input test_image/00031.jpg --output proof/ --work-dir proof/ --force --keep-cache

# 폴더 배치
python3 run_pipeline.py --input test_image/ --output proof/ --work-dir proof/ --force --workers 4

# Debug 실행
python3 run_debug_pipeline.py
```

파이프라인 단계 상세, 파일 구조, proof 스크립트는 `AGENTS.md` 참고.
