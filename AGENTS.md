# Comic Translator — Agent 작업 가이드

## Rules

- 응답 언어: 한국어
- 코드 주석: 한국어 허용
- API 키: `.env` 파일로 분리, 하드코딩 금지
- 가상환경: `.venv`
- 현상/결과 판정: 시각화, CV 분류, split line, 경계 기반/텍스트 기반/rescue 여부처럼 사람 눈의 도메인 판단이 필요한 경우 에이전트가 단정하지 않는다. 관찰값과 가능한 해석만 정리하고, 코드 변경이나 기준 확정 전에 반드시 사용자에게 더블체크한다.

용어 정의(Glossary)와 파이프라인 흐름 요약은 `CLAUDE.md` 참고.

영역 처리 기준의 현재 용어·변수명·기존 JSON/캐시 호환 정책은 [변경 기록](docs/REGION_PROCESSING_BASIS_CHANGELOG.md)을 따른다.

에이전트의 프로젝트·페이지·영역 조회와 미적용 검토 초안은 [편집기 검토 MCP](docs/editor_mcp.md)를 사용한다. 진입점은 `scripts/run_editor_mcp.py`이며 UI 서버 없이 동작한다.

편집기 UI/UX와 MCP 후속 작업은 [편집기 할 일](docs/EDITOR_TODO.md)에서 관리한다.

---

## 현재 실행 구조

```text
NEW_COMIC_TRANSLATOR/
├── app.py                   # FastAPI 기반 리뷰 UI (현재 메인 UI, 상세는 docs/CODEBASE_FILE_MAP.md)
├── web/                       # app.py 정적 자산 (frontend/ 빌드 결과)
├── frontend/                    # React 프론트엔드 소스
├── run_pipeline.py             # CLI 진입점
├── scripts/run_debug_pipeline.py       # Debug 이미지 일괄 실행
├── scripts/visualize_debug.py          # Debug bbox 시각화
├── tests/                      # pytest (pytest.ini가 이 폴더만 수집)
├── batch/                      # Gemini Batch API 보존 영역
│   ├── submit_batch_folder.py
│   ├── receive_batch_result.py
│   └── check_all_batches.py
└── pipeline/
    ├── pipeline_runner.py      # 5단계 subprocess 실행 엔진
    ├── cloud_vision_api.py     # Step 1
    ├── text_group_builder_v2.py  # Step 2
    ├── dialogue_extractor.py   # Step 3 진입점
    ├── bubble_detector.py      # 말풍선 FloodFill 검출 (구 floodfill_anchor_probe.py)
    ├── flood_split.py          # shared flood region 분리
    ├── gemini_ocr_translate_v5.py  # Step 4
    ├── font_style_analyzer.py  # render_hint CV 보정
    ├── render_text_overlay.py  # Step 5
    ├── overlay_anchor.py       # 글자 입히기 수동값 위치 기준 재매칭
    ├── background_restore.py   # 배경 복원 엔진 (LaMa, 확정 패치 합성). 편집기 탭은 제거, AI 글자 지우기가 사용
    ├── manual_guidance.py      # 편집기 guidance → 유효 TG
    └── openai_image_redraw.py  # 렌더 생략 페이지 OpenAI 이미지 재생성
```

Step 3 보조 모듈(`edge_barrier_flood_rescue.py`, `unboxed_*.py`, `run_masked_vis.py` 등)과 전체 목록은 `docs/CODEBASE_FILE_MAP.md` 참고.

---

## 표준 파이프라인 (5단계)

```text
Step 1: Cloud Vision API        이미지 → work_dir/json/{name}_cv.json
Step 2: Text Grouping           → work_dir/json/{name}_text_groups.json
Step 3: 대사 추출               → work_dir/json/{name}_regions.json
                                → work_dir/masked/{name}_masked.jpg
                                → work_dir/sheets/{name}_indexed_sheet.jpg
                                → work_dir/vis/{name}_bubble_vis.jpg
Step 4: OCR+번역                indexed OCR sheet → work_dir/json/{name}_integrated.json
Step 5: Text Overlay            → output_dir/final/{name}_final.jpg
```

- 각 단계는 subprocess로 실행되며, 어느 단계든 실패하면 그 이미지는 실패로 끝난다
- Step 3이 경계 기반 마스킹, 텍스트 기반 글자 지우기, indexed OCR sheet 생성을 한 번에 한다
- Step 4는 indexed OCR sheet + `regions.json`을 입력으로 쓰고, `--ocr-model`로 모델을 고를 수 있다
- Step 5는 `regions.json`의 bbox·safe rect·mask centroid로 배치한다
- 검증 실행에서 `--work-dir proof`를 쓰면 보통 `--output proof`를 사용

표준 산출물: `*_cv.json`, `*_text_groups.json`, `*_regions.json`, `*_indexed_sheet.jpg`, `*_integrated.json`, `*_masked.jpg`, `*_final.jpg`

레거시 (파이프라인 외): `*_ocr_v5.json`, `*_integrated_translated.json`, `*_flood_vis.json`, `*_flood_regions.json`

### JSON 최상위 구조 (헷갈리기 쉬움)

`regions.json`과 `integrated.json`은 비슷해 보이지만 배열 이름과 단위가 다르다. 코드·스크립트에서 영역을 조회할 때 이 표를 기준으로 한다.

| 파일 | 만든 곳 | 최상위 키 | 항목 단위 |
|------|---------|-----------|-----------|
| `*_regions.json` | Step 3 | `regions`, `clusters`, `analysis_status`, `ocr_region_count` 등 | `regions[]` = R 영역 하나. `region_id`, `processing_basis`, `unboxed_rect`/`bubble_rect`, `anchor_ids`(TG 번호), `region_ref`, `region_origin` |
| `*_integrated.json` | Step 4 | `clusters`, `unmatched_clusters`, `model`, `ocr_source`, `region_ref_scheme` (재요청 실패 시 `missing_region_ids`) | `clusters[]` = 번역 단위 하나. `text`(원문), `text_translated`, `render_hint_final`, `source_region_id`(= `regions[].region_id`), `source_region_ref`, `source_segment_index` |

- `integrated.json`에는 `regions` 배열이 없다. R 번호로 번역을 찾으려면 `clusters[].source_region_id`를 본다.
- 한 R이 여러 줄·문장으로 나뉘면 cluster가 여러 개가 되고 `source_segment_index`로 구분한다.
- OCR 대상 R이 없으면 Step 4는 `status: skipped`만 담은 `integrated.json`을 만들고 렌더는 건너뛴다(`ocr_status.json`의 `message: no_ocr_regions`). 이때 `final.jpg`가 만들어지지 않는다.
- 확정한 AI 글자 지우기 패치는 `work/restore/<이미지 stem>/`에 영역 단위로 쌓이고, 오버레이 단계가 있어야 `final.jpg`에 합성된다.

---

## 핵심 파일

| 파일 | 역할 |
|------|------|
| `pipeline/pipeline_runner.py` | `run_single_image()` / `run_batch_images()` — 전 단계 subprocess 통일, `stream_subprocess()` 공용 실행기 |
| `pipeline/cloud_vision_api.py` | Cloud Vision OCR (이미지 단위, 실패 시 exit 1) |
| `pipeline/text_group_builder_v2.py` | CV block → Text Line → Text Group 병합 (Repair + X 확장) |
| `pipeline/dialogue_extractor.py` | Step 3 진입점. 경계 기반 검출 + 텍스트 기반 지우기 + indexed OCR sheet |
| `pipeline/bubble_detector.py` | Anchor 기반 FloodFill, region_type 분류, flood mask 생성 |
| `pipeline/gemini_ocr_translate_v5.py` | indexed OCR sheet 기반 OCR/번역/integrated JSON 생성 (Gemini/OpenAI) |
| `pipeline/render_text_overlay.py` | split flood region 우선 bbox + DP 줄바꿈 텍스트 렌더링 |
| `pipeline/manual_guidance.py` | 편집기 guidance 적용, atomic JSON 쓰기 |
| `pipeline/background_restore.py` | 배경 복원 엔진(편집기 탭은 제거됨, AI 글자 지우기가 사용): 문맥 영역·제거 마스크 분리, LaMa 복원, 마스크 내부만 합성한 패치를 Step 5 렌더 때 적용 |
| `pipeline/openai_image_redraw.py` | 렌더 생략 페이지 OpenAI image model 재생성 |
| `scripts/visualize_debug.py` | union/cluster/line bbox 시각화 |

---

## Debug 실행

```bash
. .venv/bin/activate
python3 scripts/run_debug_pipeline.py
```

`proof/Debug/`를 `work_dir`와 출력 폴더로 함께 사용하며 산출물은 타입별 하위 폴더에 저장됨:

```text
proof/Debug/json/{name}_cv.json
proof/Debug/json/{name}_text_groups.json
proof/Debug/json/{name}_integrated.json
proof/Debug/masked/{name}_masked.jpg
proof/Debug/merge/{name}_merge.jpg
proof/Debug/{name}_final.jpg
```

---

## proof/ 검증 스크립트

검증 스크립트는 `proof/src/`에 두고 삭제하지 않는다.

| 스크립트 | 역할 |
|----------|------|
| `proof/src/run_anchorless_bubble_rescue_experiment.py` | anchor 없는 말풍선 구제 실험 |
| `proof/src/run_ocr_hint_experiments.py` | OCR 힌트 변형 비교 실험 |
| `proof/src/visualize_anchor_contour_rescue.py` | anchor contour 구제 시각화 |
| `proof/src/visualize_interior_rescue_candidates.py` | 내부 구제 후보 시각화 |

메인 실행은 `run_pipeline.py`를 우선한다.

이전에 이 표에 있던 `run_full_batch.py`, `run_masked_batch.py`, `run_gemini_ocr.py`,
`run_inpaint_test.py`는 현재 리포지토리에 없다. 변곡점 시각화는
`pipeline/vis/visualize_inflection_candidates.py`로, overlay branch 검증은
`archive/render_overlay_branch.py`로 이동했다.

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

- `archive/`, `pipeline/archive/`는 현재 실행 경로가 아니다.
- `integrate_ocr_v3.py`, `gemini_api.py`, `bbox_utils.py`, `preprocess_for_cv.py`, `gemini_batch_api.py`는 archive에 있다.
- Gradio `main.py`, `run_test_pipeline.py`는 `archive/retired_gradio_ui/`, `floodfill_anchor_probe.py`, `apply_masking.py`, `difficulty_scorer.py`, `gemini_image_redraw.py`는 `pipeline/archive/`에 있다.
- `*_ocr_v5.json`, `*_integrated_translated.json`은 현재 표준 산출물이 아니다.
- `*_integrated.json`이 masking/overlay의 기준 JSON이다.

저장 경로(2026-10-20): 기본 데이터 홈은 `~/ComicTranslator`입니다. 프로젝트 JSON은 상대 경로를 저장하고, 읽을 때 기존 절대 경로와 `session.json`도 지원합니다.
