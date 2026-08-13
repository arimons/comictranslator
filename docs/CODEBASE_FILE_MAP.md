# 코드베이스 파일 맵

> 마지막 확인: 2026-08-14. Gradio `main.py` 폐기, Step 4 모델 선택 추가 반영.
> 표준 진입점은 `main_v2.py`(편집기)와 `run_pipeline.py`(CLI)다.

작성일: 2026-05-05 / 최종 갱신: 2026-07-03

`archive/`, 출력물, 이미지, 가상환경은 제외하고 현재 실행 경로의 소스만 정리했다.

현재 연결 상태와 다음 작업은 [PIPELINE_STATUS.md](PIPELINE_STATUS.md) 참고.
FloodFill 필터 체계 상세는 [FLOODFILL_MASKING_PROGRESS.md](FLOODFILL_MASKING_PROGRESS.md) 참고.

## 현재 구조

```text
NEW_COMIC_TRANSLATOR/
├── main.py                    # (Deprecated) Gradio UI — main_v2.py로 대체됨
├── main_v2.py                  # FastAPI 기반 리뷰 UI (현재 메인 UI)
├── ui_v2/                      # main_v2.py 정적 자산 (index.html, styles.css, app.bundle.js)
├── frontend/                    # ui_v2 프론트엔드 소스 + esbuild 빌드 설정
│   ├── package.json
│   └── src/
│       ├── main.jsx            # React 엔트리포인트
│       ├── App.jsx              # 세션/이미지 목록/워크플로우 탭 전체 상태
│       ├── EditorCanvas.jsx      # Step 3 말풍선 분석 캔버스 (Konva)
│       ├── OverlayCanvas.jsx     # Step 6 텍스트 오버레이 편집 캔버스 (Konva)
│       └── lib.js                # 공용 순수 헬퍼 함수 + 상수
├── run_pipeline.py             # CLI 진입점
├── run_debug_pipeline.py       # Debug 이미지 일괄 실행 wrapper
├── run_test_pipeline.py        # test_image 범위 실행 wrapper
├── run_floodfill_batch.py      # FloodFill 배치 실행 wrapper
├── visualize_debug.py          # Debug bbox 시각화
├── proof/                      # 검증용 스크립트 및 출력물
│   └── run_ocr_hint_experiments.py  # OCR 힌트 변형 비교 실험
├── batch/                      # Gemini Batch API 보존 영역 (레퍼런스)
│   ├── submit_batch_folder.py
│   ├── receive_batch_result.py
│   └── check_all_batches.py
└── pipeline/
    ├── pipeline_runner.py      # 6단계 파이프라인 실행 엔진
    ├── cloud_vision_api.py
    ├── text_group_builder.py
    ├── floodfill_anchor_probe.py
    ├── flood_split.py          # shared flood region split 후보 평가
    ├── gemini_ocr_translate_v5.py
    ├── apply_masking.py
    ├── render_text_overlay.py
    ├── difficulty_scorer.py    # flood_vis.json → 난이도 점수 (0.0~1.0)
    ├── gemini_image_redraw.py  # 컬러 표지/일러스트 Gemini 재생성
    ├── bubble_detector.py
    ├── vis/                    # pipeline 진단 시각화 도구
    ├── experimental/           # pipeline 승격 후보 실험 모듈
    └── __init__.py
```

## 표준 파이프라인

```text
입력 이미지
  ↓
Step 1: Cloud Vision API (3x3 grid, languageHints=["ja","en"])
  → {work_dir}/{name}_cv.json
  ↓
Step 2: Cluster CV V5
  → {work_dir}/{name}_text_groups.json
  ↓
Step 3: FloodFill (소프트 — 실패해도 계속)
  → {work_dir}/{name}_flood_vis.json
  ↓
Step 4: Gemini OCR + 번역 + integrated JSON 생성
  → {work_dir}/{name}_integrated.json
  ↓
Step 5: White Masking
  → {work_dir}/{name}_masked.jpg
  ↓
Step 6: Text Overlay (flood_vis.json 있으면 bbox 선택에 활용)
  → output/{subfolder}/{name}_final.jpg
```

예전 `integrate_ocr_v3.py` 단계는 메인 파이프라인에서 제거됐다. 현재는 `pipeline/gemini_ocr_translate_v5.py`가 Gemini 응답을 바로 masking/overlay가 읽는 integrated JSON 구조로 저장한다.

## Root Wrapper

### `main.py` — **Deprecated**

Gradio UI. `pipeline_runner.run_batch_images()`를 사용한다. `main_v2.py` 리뷰 UI로 대체되어 더 이상 사용하지 않는다. 아래 `main_v2.py` 항목 참고.

- 단일/폴더 이미지 업로드 → `run_batch_images()`로 6단계 파이프라인 실행
- 결과 갤러리에 `difficulty_scorer.score_label()`로 난이도 캡션 표시
- 컬러 이미지 감지 시 `gemini_image_redraw.redraw_image()` 경로 사용

### `main_v2.py` (현재 메인 UI)

FastAPI 기반 로컬 리뷰 웹앱이다. TG(Anchor Text Group) 수동 보정, Step 3 flood region 재판정, OCR/번역, 텍스트 오버레이 배치까지 한 화면에서 다루는 캔버스 편집기를 제공한다.

```bash
python3 main_v2.py            # http://127.0.0.1:7870
python3 main_v2.py --no-browser --port 7870
```

**세션 구조** — 이미지를 불러오면 `temp_output/v2_sessions/{session_id}/`에 `input/`, `work/`, `output/` 하위 폴더가 생기고, `session.json`이 이미지 목록·guidance·프로젝트명을 기록한다.

**캐시 구조** — 이미지 `sha256` 해시를 키로 3단계 캐시를 둔다. 같은 이미지를 여러 세션에서 다시 불러와도 재계산하지 않는다.

| 캐시 | 위치 | 대상 |
| --- | --- | --- |
| 입력 캐시 | `temp_output/v2_input_cache/` | 원본 이미지 파일 (심볼릭 링크로 세션에 연결) |
| CV 캐시 | `temp_output/v2_cv_cache/` | Step 1 Cloud Vision 결과 (`*_cv.json`) |
| 분석 캐시 | `temp_output/v2_analysis_cache/` | Step 3까지 결과 (`text_groups`, `regions`, `indexed_sheet`, `masked`, `bubble_vis`) + manual guidance 결과 |

guidance(수동 보정)가 있는 이미지는 `guidance_digest`(guidance 내용의 sha256)로 별도 하위 캐시를 둬서, 같은 guidance 조합이면 Step 3 재실행 없이 그대로 재사용한다.

**주요 API 엔드포인트**

| 엔드포인트 | 역할 |
| --- | --- |
| `POST /api/import-path`, `/api/upload` | 이미지 폴더/파일을 세션에 추가 |
| `POST /api/analyze(-stream)` | Step 1~3 실행 (pre_ocr까지), 캐시 우선 재사용 |
| `POST /api/guidance`, `/api/guidance/clear` | 수동 보정(TG 삭제/split, edge barrier, manual region 등) 반영 후 Step 3 재실행 |
| `POST /api/run-final(-stream)` | Step 4(Gemini OCR/번역) + Step 6(오버레이) 실행 |
| `POST /api/overlay/apply` | 오버레이 위치/폰트만 다시 렌더링 (OCR 재실행 없음) |
| `GET /api/session/{id}/export`, `POST /api/export/generate-missing` | 결과 ZIP 내보내기, 실패 이미지 OpenAI 이미지 생성 fallback |

**프론트엔드** — `ui_v2/index.html`이 로드하는 `ui_v2/app.bundle.js`는 `frontend/src/`를 esbuild로 번들링한 결과물이다. 소스는 다음처럼 나뉜다.

| 파일 | 역할 |
| --- | --- |
| `frontend/src/lib.js` | `api()`/`apiStream()` fetch 헬퍼, TG·region·guidance·overlay 관련 순수 함수와 상수 |
| `frontend/src/EditorCanvas.jsx` | Step 3 캔버스 — TG on/off, edge barrier, manual bubble/ubox, bubble seed 등 guidance 편집 |
| `frontend/src/OverlayCanvas.jsx` | Step 6 캔버스 — 오버레이 안전 영역 이동/리사이즈/회전, eraser rect 편집 |
| `frontend/src/App.jsx` | 세션 관리, 이미지 목록, 워크플로우 탭(분석/OCR/오버레이/내보내기) 전체 상태 |
| `frontend/src/main.jsx` | React 엔트리포인트 |

프론트엔드 코드를 수정하면 `frontend/`에서 `npm install`(최초 1회) 후 `npm run build`로 `ui_v2/app.bundle.js`를 다시 생성해야 한다. `ui_v2/app.bundle.js`는 결과물이므로 커밋 대상이며, 앱을 실행만 할 때는 Node.js가 필요 없다.

### `run_pipeline.py`

CLI 진입점. 단일 이미지 또는 폴더 배치 처리.

```bash
python run_pipeline.py --input image.jpg --output output/ [--force] [--workers 4]
```

### `run_debug_pipeline.py`

Debug 이미지를 `pipeline_runner.run_single_image()`로 처리한다.

- 최종 결과와 중간 파일을 모두 `proof/Debug/`에 모은다.
- 처리 후 `visualize_debug.py`를 호출해 `{번호}_debug.jpg`를 만든다.

### `run_test_pipeline.py`

`test_image/00020.jpg`부터 `00035.jpg`까지를 현재 메인 파이프라인으로 처리한다.

- 예전 CV Grid → cluster → OCR → integration 개별 subprocess 테스트 경로는 제거했다.
- 출력은 `proof/test_pipeline/`에 모은다.

### `visualize_debug.py`

Debug 산출물 시각화 도구다.

- 입력: 원본 이미지, `{번호}_text_groups.json`, `{번호}_integrated.json`
- 출력: `proof/Debug/{번호}_debug.jpg`
- 빨간색: sentence/union bbox
- 파란색: matched cluster bbox
- 초록색: line bbox

## Proof 영역

proof/ 폴더는 파이프라인 검증 전용 임시 실행 영역이다.

```text
proof/
  json/              *_cv.json, *_text_groups.json, *_flood_vis.json,
                     *_fallback_sheet.json, *_flood_ocr.json, *_flood_integrated.json
  flood_vis/         *_flood_vis.jpg
  masked/            *_masked.jpg
  ocr_sheets/        *_fallback_sheet.jpg, *_fallback_sheet_indexed.jpg, *_text_overlay.jpg
  misc/              *_regions_only.jpg
  NNNNN_merge.jpg    최종 검증용 (flood_vis 좌 / text_overlay 우)
```

### `pipeline/vis/visualize_inflection_candidates.py` — shared flood split 후보 시각화

실제 구현은 `pipeline/vis/visualize_inflection_candidates.py`에 있다.
`proof/src` 파일은 기존 실행 경로를 보존하는 얇은 wrapper다.

### inpaint 후보 검증 (현재 리포지토리에 wrapper 없음)

실제 구현은 `pipeline/experimental/inpaint_candidate.py`에 있다.
`proof/src` 파일은 검증 실행 진입점만 담당한다.

## Batch 영역

### `batch/submit_batch_folder.py`, `receive_batch_result.py`, `check_all_batches.py` — Gemini Batch API

대용량 Gemini 이미지 Batch API 실험/운영용이다. Free tier에서 실제 대용량 작업이 막히는 문제가 확인되어 메인 파이프라인과 분리했다. 현재는 레퍼런스 목적으로 보존한다.

## Pipeline 파일

### `pipeline/cloud_vision_api.py`

Google Cloud Vision OCR wrapper다.

- `CloudVisionAPI`가 credential과 `vision.ImageAnnotatorClient`를 관리한다.
- `detect_text_grid()`: 입력 이미지를 3x3 고정 chunk로 분할해 1회 호출한다. 9장 초과 시 9장씩 반복.
- languageHints `["ja", "en"]`이 REST/client 양쪽에 적용된다.
- 출력은 이후 `text_group_builder.py`가 읽는 Vision JSON이다.

**OCR 타입 변경 이력**: `TEXT_DETECTION` → `DOCUMENT_TEXT_DETECTION` 으로 전환했다.
그리드 내부(`_detect_text_grid_chunk`)와 단일 이미지 fallback 모두 `DOCUMENT_TEXT_DETECTION`을 사용한다.
`TEXT_DETECTION`은 짧은 텍스트 조각에 유리하지만 세로쓰기 만화 말풍선처럼 문맥이 있는 레이아웃에서는 `DOCUMENT_TEXT_DETECTION`이 행/단 구분 정확도가 높아 clustering 품질이 개선됐다. 특히 큰 말풍선의 텍스트 블록 인식률이 향상됐다.

### `pipeline/text_group_builder.py`

Cloud Vision OCR 결과를 Text Fragment → Text Line → Text Group 3단계로 병합한다.

> 파일명/변수명의 `cluster`는 개념상 **Text Group**에 해당한다. 실제 파일명(`_text_groups.json`)과 코드 내 변수명은 유지한다.

- `pass1_create_lines()`: Text Fragment → Text Line, 같은 세로줄 기준 BFS 병합
- `filter_furigana_lines()`: 후리가나로 보이는 작은 Text Line 제거
- `pass2_create_clusters()`: Text Line → Text Group, 4-stage rule 병합
- `sort_clusters_reading_order()`: 일본어 세로쓰기 기준 오른쪽→왼쪽, 위→아래 정렬

Text Group이 FloodFill의 **Anchor Text Group** seed 후보로 사용된다.

### `pipeline/floodfill_anchor_probe.py`

Anchor Text Group 중심점 기반 FloodFill로 말풍선 내부 영역을 검출한다.

**공개 함수:**

- `visualize_anchor_floodfill()`: flood 결과 및 region_type 분류 시각화 → `flood_vis.json`, `flood_debug.jpg` (Step 3-A)
- `apply_floodfill_white_mask()`: flood mask 기반 흰색 마스킹 (단독 사용, 현재 표준 경로 미사용)
- `create_floodfill_fallback_outputs()`: flood 판정 + OCR sheet + 마스킹 통합 생성 → `flood_pre_masked.jpg`, `flood_sheet.jpg`, `flood_regions.json` (Step 3-B)
- `create_packed_ocr_sheet()`: flood region crop만 모아 OCR sheet 생성 (단독 레거시 경로)

**내부 주요 함수:**

- `_find_side_white_seed(gray, rect)`: 세로쓰기 anchor 좌우 바깥에서 먼저 흰 시드 탐색
- `_flood_with_fallback(image, gray, center, rect)`: 3단계 시드 탐색 — 좌우 바깥 → 중심 근처 → bbox 가장자리 4방향
- `_auto_split_flood_mask(mask, anchor_ids, anchor_centers)`: 단일/다중 anchor flood region에 inflection split 시도 (CV 미인식 버블 포함 목적)
- `_compute_flood_bimodality(mask, gray)`: flood mask 내부 흑/백 bimodality 계산 (0~1, 0.85 미만 → 획 파고들기 판정)
- `_is_bubble_shaped(mask, min_solidity)`: flood inner mask solidity로 말풍선 형태 판별
- `_is_dark_bg_text(gray, rect, threshold)`: bbox crop 평균 밝기로 검은 배경 흰 글자 판별
- `_classify_anchor_region(cluster, flood_rect, inner_area, touches_border, safe)`: region_type + quality_flags 반환

**`create_floodfill_fallback_outputs` 판정 흐름:**

```
dark_bg_text 제외 → symbol_only/short_non_jp_noise pre-skip
  → flood 시도 (_flood_with_fallback)
  → 수락 조건: flood_area_ratio ≤ 0.12 AND 1.0 ≤ ratio ≤ 12.0 AND center_inside_flood_rect
    → text_noise AND not bubble_shaped → noise_like_skip
    → bimodality < 0.85 → low_bimodal_flood skip
    → 수락: method=flood
  → ratio > 12 AND area OK AND solidity ≥ 0.65 AND bimodality ≥ 0.85 → high_ratio_bubble_rescued
  → 나머지 ratio 초과: cluster_actual_flood_rect 기록 (cross-split/combined-rescue용)
  → union_bbox_fallback: 비활성화 (diagnostics만 기록, OCR sheet 제외)
  ↓ (전체 loop 후)
cross-split: ratio 초과 anchor가 flood 성공 anchor와 동일 flood_rect 공유 → flood group 합류
combined_anchor_rescue: ratio 초과 anchor 2개 이상이 동일 flood_rect 공유 + 합산 ratio 통과 → flood group 승격
  ↓
split: 2개 이상 anchor 공유 → inflection line (1차) → erosion CC (2차, disjoint bbox 조건)
```

region_type 분류 (`visualize_anchor_floodfill` 기준): `in_bubble`, `narration_like`, `noise_like`, `dark_bg_text`, `unsafe_flood`

> 필터 체계 상세 → [FLOODFILL_MASKING_PROGRESS.md](FLOODFILL_MASKING_PROGRESS.md)
> 표준 경로 연결 상태 → [PIPELINE_STATUS.md](PIPELINE_STATUS.md)

### `pipeline/flood_split.py`

같은 flood region을 공유하는 Anchor Text Group을 독립 region으로 나눌지 판단한다.

- `collect_inflection_candidates(mask, anchor_centers, min_defect_depth, anchor_rects)`: convexity defect pair 전체를 평가해 후보 목록 반환. `anchor_rects` 지정 시 split line이 anchor center로부터 `min(bbox_w, bbox_h) * 0.25` 이상 떨어져야 유효 (`bboxes_clear` 플래그). 메인 로직과 시각화 공유.
- `split_mask_by_inflection_line(mask, anchor_ids, anchor_centers, anchor_rects)`: `collect_inflection_candidates` 결과로 split line 선택. ATG가 선 양쪽에 갈리고 각 sub-mask 안에 center가 포함되어야 수락.
- `split_mask_by_erosion_components(mask, anchor_ids, anchor_centers)`: inflection 실패 후 점진적 erosion으로 CC가 분리될 때까지 침식 후 Voronoi 할당. `all_anchor_rects_are_disjoint` 통과 시에만 호출 (bbox 겹침 있는 anchor는 erosion split 시 Voronoi 오배정 위험).
- `all_anchor_rects_are_disjoint(anchor_rects)`: anchor bbox가 모두 비겹침인지 확인 — erosion split 사전 조건.
- `_line_segment_intersects_rect(p1, p2, rect)`: Liang-Barsky 알고리즘으로 선분-직사각형 교차 판단.

### `pipeline/vis/visualize_inflection_candidates.py`

`pipeline.flood_split.collect_inflection_candidates()`를 사용해 shared flood split 후보를 시각화한다.

- 출력: `proof/overlay-branch/_debug/inflection_lines/`
- 기존 proof/src wrapper는 제거됐고 이 모듈을 직접 사용한다.

### `pipeline/experimental/inpaint_candidate.py`

flood 실패 후보에 TELEA inpaint를 적용하는 승격 후보 실험 모듈이다.

- 입력: `proof/json/{num}_flood_vis.json`, `test_image/{num}.jpg`
- 출력: `proof/inpaint/`
- 기존 inpaint 실험 스크립트는 이 모듈로 흡수됐다.
- 검증 실행 wrapper는 현재 없다. 모듈을 직접 호출한다.

### `pipeline/gemini_ocr_translate_v5.py`

Gemini 한 번의 호출로 OCR, 번역, 문장 단위 Text Group 매칭을 수행하고 integrated JSON을 생성한다.

- 입력: 원본 이미지 + `*_text_groups.json`
- 출력: `*_integrated.json`
- 출력 구조: `clusters`(=Text Group), `unmatched_clusters`, `model`
- 각 항목은 `sentence_id`, `text`, `text_translated`, `matched_cluster`, `matched_cluster_details`, `union_bbox`, `bounding_rect`, `location`을 포함한다.

### `pipeline/apply_masking.py`

`*_integrated.json`의 bbox를 기준으로 원문 텍스트 영역을 흰색 마스킹한다.

- `union_bbox` 또는 `bounding_rect`를 사용한다.
- `location == "in_bubble"`이면 corner fade 마스킹을 적용한다.
- 그 외에는 반투명 흰색 마스킹을 적용한다.

### `pipeline/pipeline_runner.py`

6단계 파이프라인 실행 엔진이다.

- `run_single_image()`: 1장 처리. 각 단계를 subprocess로 순차 실행하며 중간 파일 캐시 활용.
- `run_batch_images()`: Step 1(CV)을 그리드로 일괄 처리 후 Steps 2-6을 ThreadPoolExecutor 병렬 실행.
- Step 3(FloodFill) 실패 시 경고만 출력하고 계속 진행.
- Step 6에서 `flood_vis.json` 존재 시 `--flood_vis_json` 인자로 전달.

### `pipeline/difficulty_scorer.py`

`*_flood_vis.json`의 `results[].region_type_guess`를 분석해 0.0~1.0 난이도 점수를 반환한다.

- `noise_like`, `symbol_only`, `None` 비율로 점수 계산
- `score_label()`: 점수 + 레이블 문자열 반환 (🔴/🟡/🟢)
- `flood_vis.json` 없으면 -1.0

### `pipeline/gemini_image_redraw.py`

컬러 표지/일러스트를 Gemini image model로 직접 번역 이미지 생성하는 별도 경로다.

- `redraw_image()`: `google.genai` SDK 기반 구현

### `pipeline/render_text_overlay.py`

마스킹된 이미지 위에 한국어 번역문을 렌더링한다.

- DP 기반 최적 줄바꿈(`wrap_tokens`)으로 토큰 단위 분배
- aspect-ratio 3단계(tall/wide/square) 기반 inner padding(`inner_rect_for`)
- 페이지 단위 폰트 크기 조화(`page_font_band`): 전체 클러스터 폰트 중앙값 ±7px 밴드
- `flood_vis.json`이 있으면 `build_flood_info_by_anchor`, `build_shared_flood_groups`로 flood_rect 기반 bbox 선택
- 구버전: `archive/render_text_overlay_legacy.py`

## Box 병합 책임 구분

| 문제 유형 | 우선 확인 파일 |
| --- | --- |
| Text Fragment가 Text Line으로 잘못 묶임 | `pipeline/text_group_builder.py` |
| 여러 Text Line이 Text Group으로 잘못 묶임 | `pipeline/text_group_builder.py` |
| Gemini가 Text Group id를 잘못 매칭함 | `pipeline/gemini_ocr_translate_v5.py` |
| union bbox가 너무 크거나 작음 | `pipeline/gemini_ocr_translate_v5.py`, `pipeline/render_text_overlay.py` |
| 마스킹이 말풍선 경계를 침범함 | `pipeline/apply_masking.py` |
| 번역 텍스트가 영역에 맞지 않음 | `pipeline/render_text_overlay.py` |
| flood가 배경으로 샘 / region 분류 오류 | `pipeline/floodfill_anchor_probe.py` |

## 검증 명령

```bash
. .venv/bin/activate
python3 -m compileall main.py run_debug_pipeline.py run_test_pipeline.py visualize_debug.py pipeline batch
```
