# Comic Translator — Agent 작업 가이드

## Rules

- 응답 언어: 한국어
- 코드 주석: 한국어 허용
- API 키: `.env` 파일로 분리, 하드코딩 금지
- 가상환경: `.venv`
- 현상/결과 판정: 시각화, CV 분류, split line, bubble/U/rescue 여부처럼 사람 눈의 도메인 판단이 필요한 경우 에이전트가 단정하지 않는다. 관찰값과 가능한 해석만 정리하고, 코드 변경이나 기준 확정 전에 반드시 사용자에게 더블체크한다.

용어 정의(Glossary)와 파이프라인 흐름 요약은 `CLAUDE.md` 참고.

---

## 현재 실행 구조

```text
NEW_COMIC_TRANSLATOR/
├── main.py                     # (Deprecated) Gradio UI — main_v2.py로 대체됨
├── main_v2.py                   # FastAPI 기반 리뷰 UI (현재 메인 UI, 상세는 docs/CODEBASE_FILE_MAP.md)
├── ui_v2/, frontend/             # main_v2.py 정적 자산 + React 프론트엔드 소스
├── run_pipeline.py             # CLI 진입점
├── run_debug_pipeline.py       # Debug 이미지 일괄 실행
├── run_test_pipeline.py        # test_image 범위 실행
├── run_floodfill_batch.py      # FloodFill 배치 실행
├── visualize_debug.py          # Debug bbox 시각화
├── batch/                      # Gemini Batch API 보존 영역
│   ├── submit_batch_folder.py
│   ├── receive_batch_result.py
│   └── check_all_batches.py
└── pipeline/
    ├── pipeline_runner.py      # 6단계 subprocess 실행 엔진
    ├── cloud_vision_api.py
    ├── text_group_builder.py
    ├── floodfill_anchor_probe.py
    ├── gemini_ocr_translate_v5.py
    ├── apply_masking.py
    ├── render_text_overlay.py
    ├── difficulty_scorer.py    # flood_vis.json → 난이도 점수
    └── gemini_image_redraw.py  # 컬러 표지 Gemini 재생성
```

---

## 표준 파이프라인 (6단계)

```text
Step 1: Cloud Vision API        이미지 → work_dir/json/{name}_cv.json
Step 2: Text Grouping           → work_dir/json/{name}_text_groups.json
Step 3: FloodFill               → work_dir/json/{name}_flood_vis.json          (overlay용)
         └─ 3-B split/OCR sheet → work_dir/json/{name}_flood_regions.json
                                → work_dir/ocr_sheets/{name}_flood_indexed_sheet.jpg
         └─ 3-C masking         → work_dir/masked/{name}_flood_pre_masked.jpg   (masking용)
Step 4: Gemini OCR+번역         indexed OCR sheet → work_dir/json/{name}_integrated.json
Step 5: White Masking           → work_dir/masked/{name}_masked.jpg
Step 6: Text Overlay            → output_dir/{name}_final.jpg
```

- Step 3은 소프트 단계 — 실패해도 Step 4 계속 진행
- Step 4는 `flood_indexed_sheet + flood_regions.json`이 있으면 OCR sheet를 Gemini 입력으로 사용
- Step 5는 `flood_pre_masked.jpg`가 있으면 이를 그대로 승격하고, 없을 때만 bbox fallback masking 사용
- Step 6은 `flood_regions.json`을 overlay bbox 1순위로 사용하고 `flood_vis.json`은 보조 정보로 활용
- 검증 실행에서 `--work-dir proof`를 쓰면 보통 `--output proof`를 사용

표준 산출물: `*_cv.json`, `*_text_groups.json`, `*_flood_regions.json`, `*_flood_indexed_sheet.jpg`, `*_integrated.json`, `*_masked.jpg`, `*_final.jpg`

레거시 (파이프라인 외): `*_ocr_v5.json`, `*_integrated_translated.json`

---

## 핵심 파일

| 파일 | 역할 |
|------|------|
| `pipeline/pipeline_runner.py` | `run_single_image()` / `run_batch_images()` — 전 단계 subprocess 통일 |
| `pipeline/cloud_vision_api.py` | Cloud Vision OCR (3x3 그리드 묶음) |
| `pipeline/text_group_builder.py` | CV block → Text Line → Text Group 병합 (v2: Repair + Consolidate) |
| `pipeline/floodfill_anchor_probe.py` | Anchor 기반 FloodFill, region_type 분류, flood mask 생성 |
| `pipeline/gemini_ocr_translate_v5.py` | indexed OCR sheet 기반 Gemini OCR/번역/integrated JSON 생성 |
| `pipeline/apply_masking.py` | flood mask 실패 시 bbox fallback 흰색 마스킹 |
| `pipeline/render_text_overlay.py` | split flood region 우선 bbox + DP 줄바꿈 + 페이지 폰트 조화 텍스트 렌더링 |
| `pipeline/difficulty_scorer.py` | flood_vis.json → 0.0~1.0 난이도 점수 |
| `pipeline/gemini_image_redraw.py` | 컬러 표지 Gemini image model 재생성 |
| `visualize_debug.py` | union/cluster/line bbox 시각화 |

---

## Debug 실행

```bash
. .venv/bin/activate
python3 run_debug_pipeline.py
```

`proof/`를 `work_dir`로 사용하며 산출물은 타입별 하위 폴더에 저장됨:

```text
proof/json/{name}_cv.json
proof/json/{name}_text_groups.json
proof/json/{name}_integrated.json
proof/masked/{name}_masked.jpg
proof/merge/{name}_merge.jpg
proof/{name}_final.jpg
```

---

## proof/ 검증 스크립트

검증 스크립트는 `proof/src/`에 두고 삭제하지 않는다.

| 스크립트 | 역할 |
|----------|------|
| `proof/src/run_full_batch.py` | 레거시 전체 배치 검증. 메인 실행은 `run_pipeline.py` 우선 |
| `proof/src/run_masked_batch.py` | FloodFill + masking 단독 검증 |
| `proof/src/run_gemini_ocr.py` | OCR sheet 기반 Gemini 검증. 현재 흐름은 메인 파이프라인으로 승격됨 |
| `proof/src/run_inpaint_test.py` | inpaint 실험 보존용. 현재 표준 masking 흐름에서는 기본 사용하지 않음 |
| `proof/src/visualize_inflection_candidates.py` | 변곡점 후보 시각화 디버그 |
| `proof/src/render_overlay_branch.py` | overlay branch 승격 전 검증 스크립트. 현재는 보존용 |

`run_inpaint_test.py` 흐름:

```text
anchor bbox ROI 추출 (BBOX_PAD=24px)
  → grayscale threshold(160) → 텍스트 획 마스크
  → dilate 3px → cv2.inpaint(TELEA, radius=7)
  → proof/inpaint/{name}_A{id}_{method}_inpaint.jpg 저장
```

---

## Batch API

`batch/` 폴더는 Gemini Async Batch API 보존 영역. Free tier 제한으로 메인 파이프라인과 분리.

```bash
python3 batch/submit_batch_folder.py input/some_folder
python3 batch/receive_batch_result.py
```

---

## 작업 폴더 규칙

에이전트 공용 작업 폴더 규칙은 `docs/AGENT_WORKSPACE_RULES.md`를 따른다.

---

## 주의사항

- `archive/`는 현재 실행 경로가 아니다.
- `integrate_ocr_v3.py`, `gemini_api.py`, `bbox_utils.py`, `preprocess_for_cv.py`, `gemini_batch_api.py`는 archive에 있다.
- `*_ocr_v5.json`, `*_integrated_translated.json`은 현재 표준 산출물이 아니다.
- `*_integrated.json`이 masking/overlay의 기준 JSON이다.
