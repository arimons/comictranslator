# 코드베이스 파일 맵

> 마지막 확인: 2026-10-06. 현재 구조 트리와 pipeline 파일 섹션을 실제 모듈 기준으로 정리하고, main_v2 안전성 보강(파일 서빙 제한, subprocess 공유, atomic JSON 쓰기 등)을 반영했다.
> 표준 진입점은 `main_v2.py`(편집기)와 `run_pipeline.py`(CLI)다.

작성일: 2026-05-05 / 최종 갱신: 2026-10-20

2026-10-20: 작품별 자동 용어집 저장소, 조사·검토 API, 편집기 패널과 번역 버전 기록을 추가했다. 사용 흐름과 자동 적용 기준은 [WORK_GLOSSARY.md](WORK_GLOSSARY.md) 참고.

영역 용어·호환 모듈 설명 갱신: 2026-10-19. 본문의 처리 분류는 경계 기반/텍스트 기반이며, 파일명·함수명·저장 키의 과거 이름은 호환을 위해 유지한다.

`archive/`, 출력물, 이미지, 가상환경은 제외하고 현재 실행 경로의 소스만 정리했다.

현재 연결 상태와 다음 작업은 [PIPELINE_STATUS.md](PIPELINE_STATUS.md) 참고.
단계별 내부 로직은 [pipeline_guide.html](pipeline_guide.html) 참고.
FloodFill 필터 체계 상세는 [FLOODFILL_MASKING_PROGRESS.md](FLOODFILL_MASKING_PROGRESS.md) 참고.

## 현재 구조

```text
NEW_COMIC_TRANSLATOR/
├── main_v2.py                  # FastAPI 기반 리뷰 UI (현재 메인 UI)
├── run_editor_mcp.py           # UI 독립 stdio MCP 진입점 (조회·검토 초안)
├── configure_editor_mcp.py     # 프로젝트 로컬 Codex/Claude MCP 설정 생성
├── ui_v2/                      # main_v2.py 정적 자산 (index.html, styles.css, app.bundle.js)
├── frontend/                    # ui_v2 프론트엔드 소스 + esbuild 빌드 설정
│   ├── package.json
│   └── src/
│       ├── main.jsx            # React 엔트리포인트
│       ├── App.jsx              # 세션/이미지 목록/워크플로우 탭 전체 상태
│       ├── StartScreen.jsx      # 홈: 파일·폴더 불러오기, 최근 프로젝트, 분석 방법
│       ├── PagePreparation.jsx  # 원본을 보존하는 페이지 분할 미리보기·별도 프로젝트 생성
│       ├── ServerStatus.jsx     # 서버 연결 상태등 (작업 진행률과 별개)
│       ├── ImagePreview.jsx     # 비교 이미지 확대·전체/너비 맞춤
│       ├── GlossaryPanel.jsx    # 프로젝트명 옆 작품 상태·CV 후보·검색어 재검색·용어 검토
│       ├── EditorCanvas.jsx      # Step 3 영역 분석 캔버스 (Konva)
│       ├── OverlayCanvas.jsx     # Step 5 텍스트 오버레이 편집 캔버스 (Konva)
│       ├── RestorePanel.jsx      # 배경 복원 탭 (마스크 브러시 + LaMa 복원)
│       ├── keymap.js             # 키보드·마우스 단축키 단일 정의
│       ├── viewportControls.js   # 두 캔버스 공용 확대/이동/맞춤
│       ├── ShortcutHelp.jsx      # ? 단축키 도움말
│       ├── regionTypes.js        # 경계 기반/텍스트 기반 용어 및 과거 타입 호환
│       ├── RegionTypeBadge.jsx   # 공통 처리 기준 배지
│       └── lib.js                # 공용 순수 헬퍼 함수 + 상수
├── run_pipeline.py             # CLI 진입점
├── run_debug_pipeline.py       # Debug 이미지 일괄 실행 wrapper
├── run_floodfill_batch.py      # 구 FloodFill 배치 wrapper (폐기 모듈 import로 현재 실행 불가)
├── visualize_debug.py          # Debug bbox 시각화
├── pytest.ini                  # pytest 수집 경로를 tests/로 고정
├── tests/                      # pytest 테스트 (문서 동기화 검사 포함)
├── proof/                      # 검증용 스크립트 및 출력물
│   └── src/                    # 검증 보조 스크립트 (삭제 금지)
├── batch/                      # OpenAI Batch API 보존 영역 (레퍼런스)
│   ├── submit_batch_folder.py
│   ├── receive_batch_result.py
│   └── check_all_batches.py
├── archive/                    # 현재 실행 경로 아님 (retired_gradio_ui/ 등)
└── pipeline/
    ├── pipeline_runner.py      # 5단계 파이프라인 실행 엔진 + stream_subprocess()
    ├── input_preprocessor.py   # 입력 정리 (가로 2페이지 spread 자동 분할)
    ├── page_preparation.py     # UI 입력: 중앙 표지 crop·우→좌 펼침 계획과 원본 보존
    ├── cloud_vision_api.py     # Step 1
    ├── text_group_builder_v2.py  # Step 2
    ├── dialogue_extractor.py   # Step 3 진입점 (경계 기반 검출 + 텍스트 기반 지우기 + OCR sheet)
    ├── bubble_detector.py      # 말풍선 FloodFill 검출 코어 (구 floodfill_anchor_probe.py)
    ├── flood_split.py          # shared flood region split 후보 평가
    ├── edge_barrier_flood_rescue.py  # 열린 말풍선 edge barrier 구제
    ├── bubble_text_filters.py  # 말풍선 후보 텍스트 필터 정책
    ├── unboxed_text_detector.py  # 텍스트 기반 후보 local mask 검증
    ├── unboxed_grouping.py     # 텍스트 기반 처리 대상 TG 선별·인접 TG 묶기
    ├── text_style_metrics.py   # TextFragment 기준 글꼴 스타일 지표
    ├── run_masked_vis.py       # 텍스트 기반 추출·통합 마스킹 시각화 (dialogue_extractor가 import)
    ├── bubble_candidate_visualizer.py  # 말풍선 후보 검토 시각화 (*_bubble_vis.jpg)
    ├── manual_guidance.py      # 편집기 guidance → 유효 TG 생성, atomic JSON 쓰기
    ├── region_types.py         # 처리 기준 상수·기존 JSON 타입 호환·비파괴 projection
    ├── editor_review_bridge.py # 세션 산출물 조회·이미지 crop·revision·미적용 검토 초안
    ├── geometry.py             # 사각형 좌표 유틸
    ├── gemini_ocr_translate_v5.py  # Step 4 (GPT-6.1 Sol 기본, OpenAI Responses API)
    ├── openai_tasks.py        # OpenAI 작업별 Sol/Luna 라우팅·JSON 출력·제한 승격
    ├── work_glossary.py        # 작품별 URL 자료 저장·단일 OpenAI 용어 정리·수동 결정·버전 고정
    ├── work_matching.py        # 기존 CV·로컬 작품 조회·확인 작품 재사용
    ├── work_sources.py         # 공개 작품 URL의 제한 수집·본문 추출·자료 인용 구간
    ├── font_style_analyzer.py  # Step 4 직후 render_hint CV 보정
    ├── render_text_overlay.py  # Step 5
    ├── overlay_anchor.py       # 글자 입히기 수동값을 region 번호 대신 영역 위치로 재매칭
    ├── background_restore.py   # 배경 복원 탭 엔진 (문맥 영역·마스크 분리, LaMa, 확정 패치 합성)
    ├── openai_image_redraw.py  # 렌더 생략 페이지 OpenAI 이미지 재생성 (main_v2 export)
    ├── vis/                    # pipeline 진단 시각화 도구
    ├── experimental/           # pipeline 승격 후보 실험 모듈
    ├── ocr_reference/          # 글꼴 스타일 참고 이미지
    ├── archive/                # 폐기 모듈 보관 (floodfill_anchor_probe.py, apply_masking.py 등)
    └── __init__.py
```

## 표준 파이프라인

```text
입력 이미지 (CLI는 input_preprocessor로 가로 2페이지 spread를 자동 분할)
  ↓
Step 1: Cloud Vision API (이미지 단위 호출, upscale2x_clahe 전처리, languageHints=["ja","en"])
  → {work_dir}/json/{name}_cv.json
  ↓
Step 2: Text Grouping (text_group_builder_v2)
  → {work_dir}/json/{name}_text_groups.json
  ↓
Step 3: 대사 추출 (dialogue_extractor — 경계 기반 검출 + 텍스트 기반 지우기 + OCR sheet)
  → {work_dir}/json/{name}_regions.json
  → {work_dir}/masked/{name}_masked.jpg
  → {work_dir}/sheets/{name}_indexed_sheet.jpg
  → {work_dir}/vis/{name}_bubble_vis.jpg
  ↓
Step 4: OCR + 번역 + integrated JSON 생성 (gemini_ocr_translate_v5)
  → {work_dir}/json/{name}_integrated.json
  ↓
Step 5: Text Overlay (regions.json으로 bbox·safe rect 결정)
  → {output_dir}/final/{name}_final.jpg
```

각 단계는 별도 subprocess로 실행되며, 어느 단계든 실패하면 그 이미지는 실패로 끝난다. 예전 Step 5 White Masking(`apply_masking.py`)은 없어졌고 마스킹은 Step 3이 직접 만든다.

예전 `integrate_ocr_v3.py` 단계는 메인 파이프라인에서 제거됐다. 현재는 `pipeline/gemini_ocr_translate_v5.py`가 OpenAI 응답을 바로 overlay가 읽는 integrated JSON 구조로 저장한다.

## Root Wrapper

### `main.py`, `run_test_pipeline.py` — 폐기됨

Gradio UI(`main.py`)와 test_image 범위 실행 wrapper(`run_test_pipeline.py`)는 2026-08-14에 `archive/retired_gradio_ui/`로 옮겼다. UI는 `main_v2.py`, CLI는 `run_pipeline.py`를 쓴다. `main.py` 전용이던 `difficulty_scorer.py`, `gemini_image_redraw.py`도 함께 `pipeline/archive/`로 옮겼다.

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
| `GET /api/session/{id}` | 세션 메타 + 페이지 **요약** 목록(URL, 상태 플래그, `detail_rev`)과 `hydration_pending`. 정상 상태에서는 파일을 쓰지 않는다 |
| `GET /api/session/{id}/page/{image_id}` | 선택한 페이지 하나의 전체 레코드(`regions`, `integrated`, `text_groups`, guidance 등). 읽기 전용 |
| `POST /api/import-path`, `/api/upload` | 이미지 폴더/파일을 세션에 추가 |
| `POST /api/analyze(-stream)` | Step 1~3 실행 (pre_ocr까지), 캐시 우선 재사용 |
| `POST /api/guidance`, `/api/guidance/clear` | 수동 보정(TG 삭제/split, edge barrier, manual region 등) 반영 후 Step 3 재실행 |
| `POST /api/run-final(-stream)` | Step 4(OCR/번역) + Step 5(오버레이) 실행. body의 `ocr_model`로 모델 지정 |
| `GET /api/ocr-models` | OCR/번역 모델 목록(`id`, `label`, `note`)과 기본값 |
| `GET /api/glossary/{session_id}`, `POST /api/glossary/confirm`, `/api/glossary/research`, `/api/glossary/review` | 작품 확인·용어집 재사용·비동기 웹 조사·불확실/충돌 검토. 확정 용어집 버전을 Step 4에 주입 |
| `POST /api/overlay/apply` | 오버레이 위치/폰트만 다시 렌더링 (OCR 재실행 없음). 저장한 수동값에 영역 위치(`_anchor`)를 기록 |
| `GET /api/session/{id}/export`, `POST /api/export/generate-missing` | 결과 ZIP 내보내기, 실패 이미지 OpenAI 이미지 생성 fallback |
| `GET /api/file` | 세션·입력 캐시·분석 캐시 폴더 아래 이미지 파일 서빙 |
| `/api/restore/*` | 배경 복원 탭: 상태·자동 마스크·마스크 저장·LaMa 작업 실행/조회·확정·패치 제거 (`pipeline/background_restore.py`) |
| `GET /manual` | 사용자 매뉴얼 `docs/editor_manual.html`을 그대로 제공. 시작 화면과 `?` 도움말에서 링크 |

**동작 메모**

- 세션 목록 GET은 캐시 연결(hydrate)을 멱등으로 한다. 이미 같은 캐시 파일에 연결돼 있으면 아무것도 쓰지 않는다. guidance 재적용(Step 3 재실행)이 필요한 페이지는 GET에서 돌리지 않고 백그라운드 hydration으로 넘기며, 끝날 때까지 `hydration_pending`이 true다. 프론트는 이 값이 true인 동안만 요약 목록을 다시 받고, 선택 페이지 상세는 `detail_rev`가 바뀔 때만 다시 받는다.
- `/api/file`은 `SESSION_ROOT`, `INPUT_CACHE_DIR`, `ANALYSIS_CACHE_DIR` 아래의 이미지 확장자 파일만 내려준다. 그 밖의 경로는 403이다. 예전처럼 저장소 루트 전체를 열면 `.env`나 인증서 파일까지 읽힌다.
- UI 정적 자산(`ui_v2/`)은 `Cache-Control: no-cache`로 내려준다(`RevalidatingStaticFiles`). `index.html`이 버전 없는 `/app.bundle.js`를 참조하므로, 번들을 다시 빌드하면 새로고침만으로 반영된다.
- 업로드·폴더 import는 `allocate_input_path()`로 세션 `input/`에서 이미지 id(stem)와 파일이 모두 겹치지 않는 이름을 고른다. input 파일은 전역 입력 캐시를 가리키는 symlink라서, 기존 경로에 덮어쓰면 다른 이미지(다른 세션 포함)의 원본이 바뀌기 때문이다. `/api/upload`는 `"xb"` 모드로 써서 기존 파일을 따라가 덮어쓰지 않는다.
- `/api/analyze-stream`은 한 장이 실패해도 나머지를 끝까지 처리한다. 실패한 이미지는 `image_id`가 붙은 `error` 이벤트로 알리고, 마지막 `complete` 이벤트를 `ok: false`로 보낸다.
- subprocess 실행(`run_subprocess`, `run_subprocess_stream`)은 `pipeline_runner.stream_subprocess()`를 공유한다. 아래 `pipeline_runner.py` 항목 참고.
- 글자 입히기 수동값(`overlay_guidance.json`)은 integrated의 region 번호(`"5"`, `"7:1"`)를 키로 쓴다. 번호는 Step 3가 돌 때마다 읽기 순서로 새로 매겨지고(Ubox 하나로도 크게 섞인다) OCR을 다시 돌리면 integrated도 새 번호를 쓴다. 그래서 수동값을 저장할 때와 OCR 직전에 영역 위치를 `_anchor`로 기록하고(`stamp_overlay_anchors_for_record`, integrated보다 나중에 저장된 수동값만), OCR 직후 겹침(IoU 0.6 이상)이 가장 큰 영역 번호로 옮긴다(`remap_overlay_guidance_for_record`). 짝이 없으면 지우지 않고 `unmatched_regions`에 보관한다. 판정 로직은 `pipeline/overlay_anchor.py`.
- JSON 저장은 `manual_guidance.write_json()`을 쓴다. 임시 파일에 다 쓴 뒤 `os.replace`로 교체하므로 쓰는 도중 중단돼도 기존 JSON이 잘린 채 남지 않는다.
- Step 4 모델은 편집기 OCR 탭의 선택 상자에서 고른다. 서버 기본값과 같으면 프론트가 `ocr_model: null`을 보내 `--model` 인자를 생략한다. 추가 프롬프트는 `--extra-prompt=값` 형식으로 넘겨 `-`로 시작하는 프롬프트도 옵션으로 오인되지 않는다.

**프론트엔드** — `ui_v2/index.html`이 로드하는 `ui_v2/app.bundle.js`는 `frontend/src/`를 esbuild로 번들링한 결과물이다. 소스는 다음처럼 나뉜다.

영역 ID는 R 번호로 통일하고 경계 기반/텍스트 기반 배지를 붙인다. 저장된 `bubble/unboxed`를 유지하는 호환 정책과 변수명 변경 내역은 [변경 기록](REGION_PROCESSING_BASIS_CHANGELOG.md)을 참고한다.

| 파일 | 역할 |
| --- | --- |
| `frontend/src/lib.js` | `api()`/`apiStream()` fetch 헬퍼, TG·region·guidance·overlay 관련 순수 함수와 상수, 세션별 OCR 설정(추가 프롬프트·모델) localStorage 저장 |
| `frontend/src/regionTypes.js` | `regionProcessingBasis()`로 과거 타입을 경계 기반/텍스트 기반으로 해석. `regionTypeLabel()`은 배지 이름, `usesEdgeBarrier()`는 검출 방법에 따른 색상 구분 |
| `frontend/src/RegionTypeBadge.jsx` | 공통 R 번호 옆에 표시하는 처리 기준 배지. 미확인 값은 미분류로 표시 |
| `frontend/src/EditorCanvas.jsx` | Step 3 캔버스 — TG on/off, edge barrier, 경계 기반 seed·텍스트 기반 영역 등 guidance 편집 |
| `frontend/src/OverlayCanvas.jsx` | Step 5 캔버스 — 오버레이 안전 영역 이동/리사이즈/회전, eraser rect 편집. 화면 기준 3px 이상 끌었을 때만 수동 위치로 저장 |
| `frontend/src/keymap.js` | 단축키 단일 정의(키 조합, 탭 범위, 조건, 동작 id, 설명). App 디스패처·툴바 tooltip·도움말이 읽고, `tests/test_docs_sync.py`가 `docs/editor_manual.html` 단축키 표와 대조 |
| `frontend/src/viewportControls.js` | 두 Konva 캔버스 공용 보기 조작 — 휠=이동, 핀치·Ctrl/Cmd+휠=확대, Space·휠버튼·Hand 도구 드래그 이동, 맞춤/100%/선택 확대 |
| `frontend/src/ShortcutHelp.jsx` | `?`로 여는 단축키 도움말. 현재 탭의 keymap 항목을 묶어 표시 |
| `frontend/src/RestorePanel.jsx` | 배경 복원 탭 — 제거 마스크 브러시·지우개, 문맥 영역 사각형, 자동 마스크, LaMa 작업 실행·확정·패치 제거. 자기 캔버스 키를 직접 처리한다(keymap의 `passive` 항목) |
| `frontend/src/App.jsx` | 세션 관리, 이미지 목록, 워크플로우 탭(분석/OCR/오버레이/배경 복원/내보내기) 전체 상태. Action History의 텍스트 기반 줄에서 LaMa 강제 토글(`manual_regions[].inpaint_engine = "lama"`) |
| `frontend/src/main.jsx` | React 엔트리포인트 |

프론트엔드 코드를 수정하면 `frontend/`에서 `npm install`(최초 1회) 후 `npm run build`로 `ui_v2/app.bundle.js`를 다시 생성해야 한다. `ui_v2/app.bundle.js`는 결과물이므로 커밋 대상이며, 앱을 실행만 할 때는 Node.js가 필요 없다.

### `run_pipeline.py`

CLI 진입점. 단일 이미지 또는 폴더 배치 처리.

```bash
python run_pipeline.py --input image.jpg --output output/ [--force] [--workers 4] [--ocr-model gpt-5.6-terra] [--pre-ocr]
```

- 입력은 `input_preprocessor.prepare_pipeline_inputs()`로 정리한다(가로 2페이지 spread는 `{work_dir}/split_input/`에 분할).
- 단일 이미지도 폴더와 같은 `run_batch_images()` 경로를 쓴다. 최종 결과는 `{output}/final/`에 저장된다.
- `--keep-cache` 없이 끝나면 중간 파일을 지운다. `--pre-ocr`(Step 3까지)일 때는 유지한다.

### `run_debug_pipeline.py`

Debug 이미지를 `pipeline_runner.run_single_image()`로 처리한다.

- 최종 결과와 중간 파일을 모두 `proof/Debug/`에 모은다.
- 처리 후 tight TG 생성과 `visualize_debug.py` 시각화를 시도하지만, 둘 다 구 경로·구 파일명(`pipeline/cluster_cv_v5.py`, `{번호}_cv.json` 루트 배치)을 기대해 현재 산출물 구조에서는 경고만 남긴다.

### `visualize_debug.py`

Debug 산출물 시각화 도구다.

- 입력: `temp_output/Debug/`의 `{번호}_cv_clusters_v5.json`, `{번호}_integrated.json` (구 파일명 기준)
- 출력: `temp_output/Debug/{번호}_debug.jpg`
- 빨간색: sentence/union bbox
- 파란색: matched cluster bbox
- 초록색: line bbox

## Proof 영역

proof/ 폴더는 파이프라인 검증 전용 임시 실행 영역이다. `--work-dir proof/ --output proof/`로 실행하면 다음처럼 쌓인다. 예전 실험이 남긴 `flood_vis/`, `ocr_sheets/` 같은 폴더가 함께 있을 수 있다.

```text
proof/
  json/              *_cv.json, *_text_groups.json, *_regions.json, *_integrated.json
  masked/            *_masked.jpg
  sheets/            *_indexed_sheet.jpg
  vis/               *_bubble_vis.jpg
  merge/             *_merge.jpg (bubble_vis 좌 / final 우)
  final/             *_final.jpg
  src/               검증 보조 스크립트 (삭제 금지)
```

### `pipeline/vis/visualize_inflection_candidates.py` — shared flood split 후보 시각화

실제 구현은 `pipeline/vis/visualize_inflection_candidates.py`에 있다. `proof/src` wrapper는 제거됐다.

### inpaint 후보 검증 (현재 리포지토리에 wrapper 없음)

실제 구현은 `pipeline/experimental/inpaint_candidate.py`에 있다. 모듈을 직접 호출한다.

## Batch 영역

### `batch/submit_batch_folder.py`, `receive_batch_result.py`, `check_all_batches.py` — OpenAI Batch API

대용량 OpenAI 이미지 Batch API 실험/운영용이다. Free tier에서 실제 대용량 작업이 막히는 문제가 확인되어 메인 파이프라인과 분리했다. 현재는 레퍼런스 목적으로 보존한다.

## Pipeline 파일

### `pipeline/cloud_vision_api.py`

Google Cloud Vision OCR wrapper다. REST API(API Key, `CLOUD_VISION_API_KEY`) 방식으로 단일화됐다.

- 파이프라인은 CLI(`cloud_vision_api.py <image> <out_json>`)로 이미지마다 1회 호출한다. 예전 3x3 그리드 묶음 호출(`detect_text_grid()`)은 없어졌다.
- `CloudVisionAPI.detect_document_text(preprocess="upscale2x_clahe")`: 2배 확대 + CLAHE 전처리 후 `DOCUMENT_TEXT_DETECTION`으로 단어 단위 bbox + confidence를 얻고, 좌표를 원본 크기로 되돌린다.
- languageHints `["ja", "en"]`을 요청에 넣는다.
- API 호출이 실패하면 CLI는 결과 파일을 쓰지 않고 exit 1로 끝난다. 빈 배열을 저장하면 이후 단계와 main_v2 CV 캐시가 "텍스트 0개"를 성공으로 굳히기 때문이다. 연결 오류 메시지에 섞인 API 키는 `***`로 가린다.
- 출력은 이후 `text_group_builder_v2.py`가 읽는 Vision JSON이다.

**OCR 타입 변경 이력**: `TEXT_DETECTION` → `DOCUMENT_TEXT_DETECTION` 으로 전환했다.
파이프라인 경로(`detect_document_text`)는 `DOCUMENT_TEXT_DETECTION`을 사용한다. `detect_text()`(`TEXT_DETECTION`)는 남아 있지만 파이프라인에서 호출하지 않는다.
`TEXT_DETECTION`은 짧은 텍스트 조각에 유리하지만 세로쓰기 만화 말풍선처럼 문맥이 있는 레이아웃에서는 `DOCUMENT_TEXT_DETECTION`이 행/단 구분 정확도가 높아 clustering 품질이 개선됐다. 특히 큰 말풍선의 텍스트 블록 인식률이 향상됐다.

### `pipeline/text_group_builder_v2.py`

Cloud Vision OCR 결과를 Text Fragment → Text Line → Text Group 3단계로 병합한다. 구 `text_group_builder.py`는 이 파일로 통합됐다(사본은 `archive/experiment/src/`).

> 파일명/변수명의 `cluster`는 개념상 **Text Group**에 해당한다. 실제 파일명(`_text_groups.json`)과 코드 내 변수명은 유지한다.

- `build_text_groups_v2()`: 진입점. CLI는 `python -m pipeline.text_group_builder_v2 <cv_json> <out_json> [image_height]`
- `pass1_create_lines()`: Text Fragment → Text Line, 같은 세로줄 기준 BFS 병합
- `repair_line_fragments()`: Pass 1.5. 같은 열이 조각난 Text Line을 가드 조건 안에서 재병합 (기본 ON)
- `filter_furigana_lines()`: 후리가나로 보이는 작은 Text Line 제거
- `pass2_create_clusters()`: Text Line → Text Group, 4-stage rule 병합
- `expand_x_adjacent()`: Pass 2.5. 인접 열만 보수적으로 묶음 (기본 ON)
- `consolidate_clusters()`: Pass 3. 공격적 재병합 (기본 OFF)
- `_format_text_groups()`: 일본어 세로쓰기 기준 오른쪽→왼쪽, 위→아래(`-x, y`)로 정렬하고 `text_group_id`를 1부터 부여

Text Group이 FloodFill의 **Anchor Text Group** seed 후보로 사용된다.

### `pipeline/dialogue_extractor.py`

이 파일을 포함한 최종 처리 기준 해석은 `pipeline/region_types.py`를 공통으로 사용한다. `region_processing_basis()`는 기존 `type: bubble/unboxed`를 각각 `boundary_based/text_based`로 읽으며, 기존 타입이 파생 필드보다 우선한다. `regions_with_processing_basis()`는 API 응답의 메모리 복사본에만 필드를 추가하므로 기존 캐시 파일은 수정하지 않는다. `source_method`와 검출기 내부 후보 분류는 이 두 처리 기준과 구분한다.

Step 3 진입점이다. `pipeline_runner`가 `python -m pipeline.dialogue_extractor <image> <text_groups> <masked> <regions> <indexed_sheet> --output-vis-path <bubble_vis>`로 호출하고, `main_v2.py`는 guidance 재적용 때 `build_dialogue_sheet()`를 직접 import한다.

- `build_dialogue_sheet()`: ① `bubble_detector.build_ocr_region_sheet()`로 경계 기반 검출·마스킹 → ② 텍스트 기반 영역의 글자를 LaMa(`simple_lama_inpainting`)로 지움 → ③ 두 종류 crop을 읽기 순서로 모아 indexed OCR sheet + `regions.json` 저장
- 편집기 guidance(`manual_regions`, `manual_seed_points`, `virtual_walls`, `split_guides`, suppressed region)를 인자로 받아 반영한다.
- region별 `safe_text_rect`(`_compute_safe_text_rect`)와 `mask_centroid`(`_compute_mask_centroid`)를 계산해 `regions.json`에 기록한다. Step 5 배치의 기준값이다.
- LaMa 실행 장치(`_lama_device`): `LAMA_DEVICE`(cpu|mps|cuda)가 없으면 cuda → mps → cpu 순으로 고른다. M3 Pro에서 MPS는 첫 호출(GPU 준비)이 CPU보다 조금 느리고 이후 호출은 약 3배 빠르며, 결과 차이는 최대 수 단계(0〜255)다. MPS 초기화가 실패하면 CPU로 돈다. 상주 서버에서는 `background_restore`도 이 모델 하나를 같이 쓴다.
- LaMa 동시 실행 제한(`_acquire_lama_process_slot`): 분석은 페이지마다 Step 3 프로세스를 따로 띄우고, LaMa를 올린 프로세스는 2〜3.7GB를 쓴다. 그래서 LaMa를 올리기 전에 임시 폴더의 슬롯 파일(`comic_translator_lama_slots/slotN.lock`)에 배타 잠금을 걸어, LaMa를 쓰는 프로세스를 머신 전체에서 `LAMA_MAX_PROCESSES`개(기본 2)로 묶는다. 자리가 없으면 기다리고, 슬롯은 프로세스가 끝날 때 풀린다. 상주 서버가 모델을 올리면 슬롯 하나를 계속 쓰므로 1로 줄이지 않는다. fcntl이 없는 Windows에서는 제한하지 않는다.
- 자동 텍스트 기반 영역(`_select_unboxed_inpaint_mask`): 배경이 선화 섞임·어두움이면 TG 사각형(+8px)을 박스로 보고 수동 텍스트 기반 영역과 같은 글자 마스크로 지운다(`_ubox_style_mask_in_roi`). 글자가 안 잡힐 때만 사각형 전체로 지운다. 사각형 전체를 지우면 머리카락 같은 그림을 LaMa가 통째로 다시 그려 뭉개졌다. 위치마다 글자 색을 따로 정하는 마스크는 흰 글자+검은 외곽선에서 외곽선이 남아 흰 얼룩이 생겨 쓰지 않는다.
- 수동 텍스트 기반 영역(`_apply_manual_ubox_lama`): 박스 안에서 글자 색 픽셀만 마스크로 잡고, 문맥은 박스 바깥 24px까지 본다. 박스가 어두우면 밝은 글자로 보고 마스크를 6px 넓힌다(흰 테두리가 남으면 LaMa가 글자 모양 얼룩을 되살린다). 엔진은 `_classify_inpaint_background` 배경 분류로 Telea/LaMa를 고르고, guidance의 `inpaint_engine: "lama"`가 있으면 그 값이 우선한다.

### `pipeline/bubble_detector.py`

말풍선 검출 코어다. 구 `floodfill_anchor_probe.py`를 Probe → Validation → Rescue → Finalize 4단계 구조로 고도화한 모듈이다(구 파일은 `pipeline/archive/`에 보존).

**공개 함수:**

- `build_ocr_region_sheet()`: 표준 경로. flood 판정 + split + 흰색 마스킹 + region 메타 생성. `dialogue_extractor`와 `run_masked_vis`가 호출한다.
- `visualize_anchor_floodfill()`: flood 결과와 region_type 분류 디버그 시각화 (CLI 4인자 모드 전용)

**내부 주요 함수:**

- `_flood_with_fallback()`: 3단계 시드 탐색 — 좌우 바깥 여백 → 중심 근처 → bbox 가장자리 4방향
- `_auto_split_flood_mask()`: 단일/다중 anchor flood region에 inflection split 시도 (CV 미인식 버블 포함 목적)
- `_compute_flood_bimodality(mask, gray)`: flood mask 내부 흑/백 bimodality 계산 (0-1)
- `_is_bubble_shaped(mask, min_solidity)`: flood inner mask solidity로 말풍선 형태 판별
- `_is_dark_bg_text(gray, rect, threshold, expand)`: bbox 주변 평균 밝기로 검은 배경 흰 글자 판별
- `_classify_anchor_region()`: region_type + quality_flags 반환

**판정 흐름 요약** (상세 수치는 모듈 docstring과 [pipeline_guide.html](pipeline_guide.html) 참고):

```
Probe      TG 주변 흰 seed에서 FloodFill (magic-wand 방식)
Validation bimodality·형태 점수로 bubble_region 승격 판정. 탈락은 삭제하지 않고 bbox_fallback으로 강등
Rescue     비율 초과 anchor 합류, Edge Barrier(edge_barrier_flood_rescue.py) 등으로 탈락 후보 구제
Finalize   is_valid 확정 후에만 region_type 부여. 남은 bbox_fallback은 텍스트 기반 후처리 대상
split      2개 이상 anchor 공유 → inflection line (1차) → erosion CC (2차, disjoint bbox 조건)
```

region_type 분류 (`_classify_anchor_region`): `bubble`, `narration_like`, `noise_like`, `failed_candidate`. `dark_bg_text`, `symbol_only`, `metadata` 후보는 flood 전에 걸러진다.

> 필터 체계 상세 → [FLOODFILL_MASKING_PROGRESS.md](FLOODFILL_MASKING_PROGRESS.md)
> 표준 경로 연결 상태 → [PIPELINE_STATUS.md](PIPELINE_STATUS.md)

### Step 3 보조 모듈

| 파일 | 역할 |
| --- | --- |
| `pipeline/edge_barrier_flood_rescue.py` | 열린 말풍선 경계를 edge barrier 기반 flood로 구제 (`run_edge_barrier_rescue_auto()`). 수동 edge guidance도 이 경로를 탄다 |
| `pipeline/unboxed_text_detector.py` | 텍스트 기반 처리 후보를 TextGroup 기반 local mask로 검증 (`extract_unboxed_text_candidate()`) |
| `pipeline/unboxed_grouping.py` | 텍스트 기반 처리 대상 TG 선별(효과음·noise 판정)과 인접 TG 묶기 (`cluster_adjacent_text_groups()`) |
| `pipeline/bubble_text_filters.py` | 말풍선 후보 판정용 텍스트 필터 (금지 문자열, symbol-only, 단독 효과음 등) |
| `pipeline/text_style_metrics.py` | TextFragment 기준 글꼴 스타일 지표와 페이지 기준선 계산 |
| `pipeline/run_masked_vis.py` | 경계 기반 + 텍스트 기반 통합 마스킹 시각화. `dialogue_extractor`가 텍스트 기반 추출 헬퍼(`_extract_with_lane_retry` 등)를 import한다 |
| `pipeline/bubble_candidate_visualizer.py` | 말풍선 후보 검토 시각화 `*_bubble_vis.jpg` 생성 (`render_candidate_review_vis()`) |
| `pipeline/geometry.py` | `as_rect()`, `union_rect()` 사각형 좌표 유틸 |

### `pipeline/flood_split.py`

같은 flood region을 공유하는 Anchor Text Group을 독립 region으로 나눌지 판단한다.

- `collect_inflection_candidates(mask, anchor_centers, min_defect_depth, anchor_rects)`: convexity defect pair 전체를 평가해 후보 목록 반환. `anchor_rects` 지정 시 split line이 anchor center로부터 `min(bbox_w, bbox_h) * 0.25` 이상 떨어져야 유효 (`bboxes_clear` 플래그). 메인 로직과 시각화 공유.
- `split_mask_by_inflection_line(mask, anchor_ids, anchor_centers, anchor_rects)`: `collect_inflection_candidates` 결과로 split line 선택. ATG가 선 양쪽에 갈리고 각 sub-mask 안에 center가 포함되어야 수락. 계산은 mask·anchor를 담는 영역(+8px)만 잘라서 하고 결과 좌표를 페이지로 되돌린다(`_split_crop_box`, `_uncrop_split_item`). 후보마다 페이지 전체 크기 배열을 만들던 때는 무거운 페이지에서 Step 3 시간의 85%를 썼다. geometry fallback의 최소 분할 면적은 `image_area`로 페이지 면적 기준을 유지한다.
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

OCR, 번역, Text Group 매칭을 한 번에 수행하고 integrated JSON을 생성한다.

- 표준 입력: `--ocr-sheet-image`(indexed OCR sheet) + `--ocr-sheet-json`(`*_regions.json`) + `*_text_groups.json`. OCR sheet가 없을 때만 원본 이미지 + CV cluster 힌트 경로로 fallback한다.
- `--model`로 모델을 고른다. `gemini-*`는 OpenAI, `gpt-*`는 OpenAI로 보낸다. UI 노출 목록은 `SUPPORTED_OCR_MODELS`, 기본값은 `MODEL_NAME`이다.
- OCR 직후 `font_style_analyzer.update_integrated_render_hints()`로 render_hint를 보정한다.
- 출력: `*_integrated.json`
- 출력 구조: `clusters`(=Text Group), `unmatched_clusters`, `model`, `ocr_source`. 번역을 끝내 받지 못한 region이 있으면 `missing_region_ids`도 쓴다.
- 부분 성공 보존: chunk 응답은 region 단위로 검증한다(`collect_valid_ocr_regions`). 통과한 region은 보관하고, 다음 시도(최대 3회)에는 실패한 region만 담은 시트(`*_chunk_NN_retryK.jpg`)를 다시 보낸다. 끝까지 실패한 region은 `missing_region_ids`로 남기고 페이지는 성공 처리한다. 렌더는 누락 region을 원본으로 두므로 편집기에서 직접 번역을 넣는다. region을 하나도 받지 못하면(API 키·네트워크 오류) 페이지를 실패로 둔다.
- 각 항목은 `sentence_id`, `text`, `text_translated`, `matched_cluster`, `matched_cluster_details`, `bounding_rect`, `location`, `source_region_id`, render_hint 관련 필드를 포함한다.

### `pipeline/font_style_analyzer.py`

indexed sheet crop의 획두께·ink density를 재서 OpenAI가 준 `render_hint_raw`를 `render_hint_final`로 보정한다. 진입점은 `update_integrated_render_hints()`이고, 단독 CLI로 OCR 없이 render_hint만 다시 계산할 수 있다.

### `pipeline/pipeline_runner.py`

5단계 파이프라인 실행 엔진이다.

- `run_single_image()`: 1장 처리. 각 단계를 `python -m pipeline.*` subprocess로 순차 실행하며 중간 파일 캐시를 활용한다. `stop_after="extract"`/`"pre_ocr"`면 Step 3에서 멈추고, 이때는 final 이미지가 있어도 Step 1-3 산출물을 다시 확인한다.
- `run_batch_images()`: 이미지별 `run_single_image()`를 ThreadPoolExecutor로 병렬 실행한다. Step 1도 이미지 단위로 호출한다.
- 어느 단계든 subprocess가 실패하면(`PipelineError`) 그 이미지는 실패로 끝난다.
- Step 4는 Step 3 산출물(`regions.json`, indexed sheet, `text_groups.json`)이 `integrated.json`보다 새로우면 다시 만든다.
- Step 5에는 `--regions_json`과 `--original_image`를 넘긴다.
- `stream_subprocess(cmd, timeout, on_line)`: 출력을 reader thread로 줄 단위로 읽어 `on_line`에 넘기고 `(returncode, 출력 줄)`을 반환한다. 출력이 멈춰도 timeout이 지켜지며, 넘기면 프로세스를 종료하고 `subprocess.TimeoutExpired`를 던진다. 자식 환경에는 `PYTHONUNBUFFERED=1`, `PYTHONIOENCODING=utf-8`, 저장소 루트를 앞에 둔 `PYTHONPATH`를 넣는다(`_subprocess_env()`). `main_v2.py`도 이 함수를 쓴다.

### `pipeline/manual_guidance.py`

편집기 guidance(TG off/split 등)를 원본 TG에 적용해 Step 3용 유효 TG를 만든다(`build_effective_text_groups()`). 원본 OCR/TG 산출물은 건드리지 않는다. `write_json()`은 임시 파일에 쓴 뒤 `os.replace`로 교체하는 atomic 쓰기이고, Windows에서 교체가 잠시 거부되면 몇 번 재시도한다.

### `pipeline/input_preprocessor.py`

`run_pipeline.py`가 실행 전에 입력을 정리한다. `prepare_pipeline_inputs()`가 가로/세로 비율이 기준(기본 1.25) 이상인 2페이지 spread를 오른쪽→왼쪽 읽기 순서로 나눈다(`split_landscape_page()`).

### `pipeline/openai_image_redraw.py`

렌더링을 건너뛴 페이지(표지·컬러 일러스트 등)를 OpenAI 이미지 모델로 번역 이미지로 다시 만든다(`redraw_image_with_openai()`). `main_v2.py`의 export 단계 "generate missing" 경로에서 쓴다.

### `pipeline/render_text_overlay.py`

마스킹된 이미지 위에 한국어 번역문을 렌더링한다.

- DP 기반 최적 줄바꿈(`wrap_tokens`)으로 토큰 단위 분배. 같은 시작점에서 줄 폭이 한도를 넘으면 더 긴 후보는 재지 않는다.
- 토큰화(`tokenize`)는 단일 dash도 토큰으로 남기고, 공백 없이 붙은 dash는 앞 단어에 붙인다.
- aspect-ratio 3단계(tall/wide/square) 기반 inner padding(`inner_rect_for`)
- split 버블 폰트 균일화: 같은 flood region에서 나뉜 버블은 그룹 최솟값의 120%를 상한으로 맞춘다(`prepare_page_layouts` 2nd pass)
- 표준 경로는 `--regions_json`을 `build_flood_info_from_regions_json`, `build_shared_flood_groups_from_regions_json`으로 읽어 bbox·safe rect를 고른다. `--flood_vis_json`(`build_flood_info_by_anchor`, `build_shared_flood_groups`)은 선택 입력으로 남아 있다.
- 배경 복원 탭에서 확정한 패치는 `--restore-state`(`state.json`)로 받아 렌더 직전 마스크 안쪽에만 합성한다(`background_restore.apply_restore_patches`).
- 구버전: `archive/render_text_overlay_legacy.py`

### `pipeline/background_restore.py`

배경 복원 탭(`RestorePanel.jsx`, `main_v2.py`의 `/api/restore/*`)의 엔진이다. 엔진은 LaMa 하나다. FLUX.1 Fill(MFLUX)을 시험했지만 어두운 배경·투명 말풍선에서도 LaMa보다 결과가 나쁘고 영역당 수 분이 걸려 제거했다.

- 문맥 영역(`context_rect`)과 제거 마스크를 분리한다. 모델에는 문맥 영역을 잘라 넘기고, 바꾸는 것은 마스크 픽셀뿐이다(`composite_inside_mask`). 마스크 바깥 픽셀이 하나라도 바뀌면 결과를 버린다(`changed_pixels_outside_mask`).
- 처리 해상도는 긴 변 512/768/원본 중 고르며 기본은 원본이다. 16의 배수 패딩과 역변환은 `prepare_model_input`/`restore_from_model_output`이 맡고, 이진 마스크는 최근접 보간만 쓴다.
- 자동 마스크(`build_text_stroke_mask`)는 `regions.json`의 텍스트 기반 영역과 CV 단어 bbox를 seed로 글자 획만 고른다.
- 확정 패치는 세션 작업 폴더의 `restore/<이미지>/`에 PNG로 저장해 페이지 JPEG를 다시 저장하지 않는다.

### 보관·폐기된 pipeline 모듈

| 구 파일 | 현재 위치 | 대체 |
| --- | --- | --- |
| `floodfill_anchor_probe.py` | `pipeline/archive/` | `bubble_detector.py` |
| `apply_masking.py` | `pipeline/archive/` | 마스킹은 Step 3 `dialogue_extractor.py`가 `*_masked.jpg`로 직접 만든다 |
| `difficulty_scorer.py` | `pipeline/archive/` | 없음 (파이프라인 미연결, `main.py` 전용) |
| `gemini_image_redraw.py` | `pipeline/archive/` | `openai_image_redraw.py` |
| `text_group_builder.py` | `archive/experiment/src/` | `text_group_builder_v2.py` |
| `cluster_cv_v5.py` | `archive/` | `text_group_builder_v2.py` |

## Box 병합 책임 구분

| 문제 유형 | 우선 확인 파일 |
| --- | --- |
| Text Fragment가 Text Line으로 잘못 묶임 | `pipeline/text_group_builder_v2.py` |
| 여러 Text Line이 Text Group으로 잘못 묶임 | `pipeline/text_group_builder_v2.py` |
| OpenAI가 Text Group id를 잘못 매칭함 | `pipeline/gemini_ocr_translate_v5.py` |
| union bbox가 너무 크거나 작음 | `pipeline/gemini_ocr_translate_v5.py`, `pipeline/render_text_overlay.py` |
| 마스킹이 말풍선 경계를 침범함 | `pipeline/dialogue_extractor.py`, `pipeline/bubble_detector.py` |
| 텍스트 기반 글자 지우기가 이상함 | `pipeline/dialogue_extractor.py`, `pipeline/unboxed_text_detector.py` |
| 번역 텍스트가 영역에 맞지 않음 | `pipeline/render_text_overlay.py` (safe rect는 `pipeline/dialogue_extractor.py`) |
| flood가 배경으로 샘 / region 분류 오류 | `pipeline/bubble_detector.py`, `pipeline/edge_barrier_flood_rescue.py` |

## 검증 명령

```bash
.venv/bin/python -m compileall -q main_v2.py run_pipeline.py run_debug_pipeline.py visualize_debug.py pipeline batch
.venv/bin/python -m pytest -q    # pytest.ini가 tests/만 수집한다
```

작품 연결 UI는 프로젝트명 옆에 표시하며 연결 전 후보/검색 상태와 연결 후 작품명/용어집 조사 상태를 구분한다. `POST /api/glossary/match`가 CV 기반 재사용 또는 웹 후보 검색을 시작한다. 검색 실패 중에도 기존 작품 연결을 유지한다.

작품 자료 수집은 `pipeline/work_sources.py`가 공개 URL 본문을 읽고, `pipeline/work_glossary.py`가 작품 ID별로 저장·재사용한다. 작품 매칭은 로컬 CV·저장 작품만 사용하며 OpenAI는 저장 자료 정리에만 한 번 사용한다. 유료 검색은 호출하지 않는다.

## 이미지 생성 권장과 후보 등록 (2026-10-20)

`pipeline/image_generation_bridge.py`는 원본·번역·용어집 revision을 묶어 생성 요청과 미검토 결과를 보관한다. `run_editor_mcp.py`에 권장·요청·등록 도구가 있고, `main_v2.py`의 읽기 API와 `frontend/src/ImageGenerationPanel.jsx`가 원본/후보 비교를 제공한다. 모델 호출은 호스트 내장 도구가 담당하며 최종 출력은 변경하지 않는다. [기록](IMAGE_GENERATION_REVIEW.md).

분석 캔버스의 TG/Region 번호 배치는 `frontend/src/canvasLabels.js`가 화면 좌표에서 계산한다. `frontend/src/EditorCanvas.jsx`는 승격 TG 번호 중복을 줄이고 여백 목록·연한 번호를 그린다. 데이터 ID나 좌표는 변경하지 않는다.
