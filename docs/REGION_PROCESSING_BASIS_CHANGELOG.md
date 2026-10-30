# 영역 처리 기준 용어 변경 기록

2026-10-19 · 사용자와 합의한 용어·변수명·호환 정책의 구현 기록. 다른 에이전트에게 일을 넘기는 handoff가 아니라 완료된 변경을 설명하는 문서다.

## 합의한 의미

| 구분 | 새 용어 / 코드 값 | 의미 | 기존 저장 값 |
|---|---|---|---|
| 처리 기준 | 경계 기반 / `boundary_based` | 검출한 경계나 수동 Edge를 기준으로 처리 범위를 확보 | `type: bubble` |
| 처리 기준 | 텍스트 기반 / `text_based` | 텍스트 위치·획을 중심으로 처리 범위를 구성 | `type: unboxed` |
| 영역 ID | R1, R2, R3… | 처리 기준과 무관한 영역 번호 | 기존 `region_id` 유지 |
| 검출 방법 | 일반 FloodFill, Edge barrier 등 | 영역을 얻은 방법 | `source_method` 유지 |
| 복원 방법 | Telea, LaMa 등 | 제거 마스크 내부 배경을 복원하는 방법 | 기존 엔진·설정 유지 |

과거 Bubble은 대상의 모양, Unboxed는 경계 유무를 표현해 기준이 비대칭이었다. 새 이름은 **처리 범위를 정한 근거**로 맞췄다. “실제 말풍선인가”, “분류가 맞는가”, “복원 품질이 좋은가”를 뜻하지 않는다. Edge는 세 번째 처리 타입이 아니다.

## 화면과 변수명

- 분석 탭 이름은 **영역 분석**이다.
- 분석·Overlay 캔버스는 공통 R 번호 옆에 **경계 기반 / 텍스트 기반** 배지를 표시한다. Overlay의 Selected Region, Region List, 대사 편집 팝업에도 배지를 표시한다.
- B 도구 이름은 **경계 기반 seed**, U 도구 이름은 **텍스트 기반 영역**이다. 기존 B/U 단축키와 내부 도구 ID `bubble`/`ubox`는 그대로다.
- `regionProcessingBasis()` / `region_processing_basis()`가 처리 기준을 해석한다. 타입을 구분하려고 `source_method` 문자열을 임의로 검색하지 않는다.
- `regionTypeLabel()` / `region_type_label()`이 표시 이름을 반환한다. 해석할 수 없는 값은 미분류로 표시한다.
- `isUnboxed`, `isUbox`, `uboxCount`처럼 처리 분류를 나타내던 주요 UI 변수는 `isTextBased`, `textBasedGuideCount`로 바꿨다.
- `addBubbleSeed`는 `addBoundarySeed`, `toggleUboxForcedLama`는 `toggleTextBasedForcedLama`, `startSpeechBubbleAnalysis`는 `startRegionAnalysis`로 바꿨다.
- Overlay의 `overlayRegionKind()`가 만들던 bubble/edge/unboxed 3분류를 제거했다. 처리 기준은 두 가지이며, Edge 색상은 `usesEdgeBarrier()`로 검출 방법을 별도 조회한다.

## JSON과 캐시 호환 계약

1. `type: bubble/unboxed`는 기존 파이프라인과 저장 형식의 호환 값으로 유지한다. 이 문자열을 전역 치환하지 않는다.
2. `processing_basis: boundary_based/text_based`는 새 의미를 드러내는 파생 필드다. 신규 Step 3 영역 JSON과 신규 indexed sheet 기반 integrated 결과에 추가한다.
3. 오래된 캐시에 새 필드가 없어도 정상이다. API의 상세 조회에서 메모리상의 복사본에만 필드를 붙인다. 원본 캐시 파일은 다시 쓰지 않는다.
4. 기존 `type`과 `processing_basis`가 충돌하면 실제 기존 처리에 쓰이는 `type`을 우선한다. 데이터 이관 없이 파생 필드를 새 의미의 단일 원본으로 취급하지 않는다.
5. `bubble_rect`, `unboxed_rect`, `manual_seed_points`, `manual_regions`, `source_method`, 가이드 ID, cache digest/version, 산출물 파일명은 유지한다. 화면 이름 변경 때문에 강제 재분석하거나 캐시를 무효화하지 않는다.
6. 이미 생성된 OCR sheet·분석 이미지의 픽셀에는 과거 U 표기가 남을 수 있다. OCR 입력과 기존 캐시 호환을 위해 indexed sheet의 과거 R/U 라벨 형식도 유지한다. 현재 앱의 실시간 캔버스·목록에서는 R 번호와 배지를 쓴다.

예를 들어 과거 JSON의 `{"region_id":4,"type":"unboxed"}`는 현재 화면에서 **R4 · 텍스트 기반**이다. API는 여기에 `processing_basis: text_based`를 덧붙여 반환하며, 파일을 마이그레이션하지 않는다.

## 코드 위치

| 파일 | 역할 |
|---|---|
| `pipeline/region_types.py` | Python의 처리 기준 상수·호환 변환·표시 이름·비파괴 API projection |
| `frontend/src/regionTypes.js` | JS의 동일한 처리 기준 상수·변환·검출 방법 조회 |
| `frontend/src/RegionTypeBadge.jsx` | 공통 DOM 타입 배지 |
| `frontend/src/lib.js` | 도구 표시 이름, 처리 기준과 검출 방법을 분리한 색상 선택 |
| `frontend/src/EditorCanvas.jsx` | 분석 캔버스의 R 번호·배지와 가이드 조작 이름 |
| `frontend/src/OverlayCanvas.jsx` | Overlay 캔버스·대사 편집 배지 |
| `frontend/src/App.jsx`, `frontend/src/keymap.js` | 목록·선택 패널·이력·탭·도움말의 새 용어 |
| `app.py` | 기존 영역 JSON을 상세 응답에서 새 의미로 읽기 |
| `pipeline/dialogue_extractor.py` | 신규 영역 JSON의 파생 필드, 최종 영역 처리 기준 조회 통일 |
| `pipeline/background_restore.py` | 텍스트 기반 영역을 복원 seed로 고르는 조건 |
| `pipeline/gemini_ocr_translate_v5.py` | 텍스트 기반 통합 판정과 신규 integrated 파생 정보 |

`bubble_detector.py`, `unboxed_text_detector.py` 등 알고리즘·호환 모듈 파일명과 기존 private 함수명은 유지했다. 검출 알고리즘, rect 계산, 분류 임계값, 마스크·복원 방식은 이 작업에서 바꾸지 않았다. OpenAI/Gemini 모델·키 설정도 변경하지 않았다.

## 검증

- `tests/test_region_processing_basis.py`: 기존 타입 우선순위, 신규 필드, 미분류 처리, Python/JS 판정 일치, 오래된 캐시 심볼릭 링크의 API 읽기 전용성.
- 기존 세션 상세, Guidance, OCR segment, 배경 복원 관련 테스트를 실행했다.
- 프론트엔드를 빌드하고 복사본 00008에서 Edge 경로의 경계 기반 영역과 과거 U4가 R4·텍스트 기반으로 표시되는 것을 확인했다.
- 기존 가이드의 B/U 키와 저장 키를 유지한 채 조작 예시를 다시 캡처해 `docs/editor_manual.html`에 내장했다. 분류나 보정 품질의 시각적 정답을 새로 확정하지 않았다.
- 최종 `python -m pytest -q`: **424 passed, 13 subtests passed**. 프론트엔드 빌드도 통과했다.
- 매뉴얼의 내장 이미지 15개를 실제 브라우저에서 확인했다. 용어 치환은 base64 이미지 데이터를 제외하며, 같은 입력으로 문서를 반복 생성해도 결과가 같다. 기존 CSS는 유지하고 데스크톱·모바일 가로 넘침이 없는 것을 확인했다.

## 문서 관리 위치

후속 문서 점검에서 현재 해설 본문의 옛 분류 표현도 정리했다. 대상은 `AGENTS.md`, `CLAUDE.md`, `docs/CODEBASE_FILE_MAP.md`, `docs/pipeline_guide.html`, `docs/editor_manual.html`, `docs/editor_agent_bridge_review.md`다. `README.md`는 현재 정본으로 연결하는 안내와 초기 구현 기록의 범위를 명시했다. 코드 식별자·JSON 예시·과거 기록을 일괄 치환하지 않으며, 검출기 내부 후보 분류와 최종 처리 기준을 별도로 설명한다.

사용법과 구조는 `docs/`의 정본 문서에서 관리한다. 이번 기록은 같은 폴더의 시점 기록(`record`)으로 등록한다. 모든 최상위 docs 문서는 `docs/DOC_SOURCES.json`에 역할과 관련 소스를 선언한다.

- 사용법: `docs/editor_manual.html`
- 구조: `docs/CODEBASE_FILE_MAP.md`
- 작업 상태·과거 진행 기록: `docs/PIPELINE_STATUS.md`, `docs/FLOODFILL_MASKING_PROGRESS.md` 등. 과거 기록의 용어는 당시 맥락을 보존한다.
- 시각 판정 사례: `docs/cases/README.md`
- 검증 스크립트·정리 가능한 실행 산출물: `proof/src/`, `proof/`의 타입별 하위 폴더
- `reports/`에도 보고서가 있지만 현재 기술 문서의 정본·변경 기록 관리 위치는 `docs/`다.

Claude와 다른 에이전트는 `AGENTS.md`, `CLAUDE.md`의 링크를 통해 이 기록을 읽고 새 용어를 사용한다. 새 처리 기준을 추가하는 변경과 단순 표시 이름 변경을 구분하고, 기존 JSON 문자열·캐시 경로를 일괄 치환하지 않는다.

## 2026-10-20 분석 캔버스 라벨 정리

캔버스의 R 번호 옆 타입 이름 배지를 없애고 청록(경계 기반)·주황(텍스트 기반)으로 구분한다. Region의 anchor_ids에 연결된 TG 번호는 기본 중복 표시하지 않고 선택할 때만 나타낸다. 오른쪽 실제 빈 여백이 있으면 R↔TG 연결 번호를 모으고 이미지 안 번호는 연하게 남긴다. 여백이 없으면 화면 좌표 기준으로 영역 상자와 라벨 충돌을 줄여 배치한다. 기존 JSON·캐시·TG/Region ID 및 선택·삭제 동작은 변경하지 않는다.

구현: `frontend/src/EditorCanvas.jsx`, `frontend/src/canvasLabels.js`. 배치 검증: `proof/src/test_canvas_labels.mjs`, 실제 Ore_3 페이지 읽기 전용 점검: `proof/src/check_canvas_labels.js`. 폭 1600/3000의 캔버스에서 확인했으며 분석·번역 요청과 파일 수정 없이 화면만 점검했다.

2026-10-20 삭제 수정: Region Off는 경계/텍스트 기반 모두 처리 대상에서 제외한다. 자동 Region 무효화 후 TG가 unboxed fallback으로 재생성되던 경로를 막았다. 적용한 제외 기록은 유지하되 캔버스 삭제 상자는 숨기고 Region 0개를 명시한다. 검증은 `tests/test_dialogue_extractor_manual.py`, `tests/test_app.py`, `proof/src/test_region_exclusion.mjs`에 남긴다.

삭제 회귀 확인: 전체 pytest 511개·하위 검사 13개 통과. `proof/src/check_region_off_click.js`는 같은 위치의 TG가 Region Off 클릭을 가로채지 않는지 응답 fixture로 검사했고, `proof/src/check_empty_region_page.js`는 실제 사망플래그 7권 2페이지의 Region/OCR 0개와 처리 대상 없음 표시를 읽기 전용으로 확인했다. 두 브라우저 검사 모두 분석·번역·저장 POST 0건이다.

2026-10-20 페이지 전체 제외 추가: TG(분석 입력), Region(묶인 처리 영역)과 별도로 페이지의 processing_excluded 상태를 둔다. 제외 페이지는 원본 출력, 복원은 기존 TG·Region 편집을 유지한다. 활성 TG만 Region 생성 입력이 되므로 TG를 끄면 그 TG가 자동 복원되는 것은 아니지만, 같은 Region에 남은 TG로 새 Region이 만들어지거나 번호가 바뀔 수 있다.

페이지 제외 검증은 `tests/test_page_processing.py`와 `proof/src/check_page_processing.js`로 수행한다. 원본 PNG의 ZIP 바이트 일치, 기존 final이 있어도 원본 우선, 복원 시 기존 final·TG 편집 보존, 제외 시 유료 호출 차단, MCP 생성 차단을 검사했다. 실제 7권 2페이지에서 버튼을 왕복하고 이전 상태로 복원했으며 분석·번역 요청은 없었다.

2026-10-20 도구 정리: R 제외를 상시 X 도구에서 우클릭 메뉴 ‘이 처리 영역 제외’로 옮겼다. TG/R 삭제와 미적용 Undo를 캔버스에 즉시 반영하며, 실제 재분석은 Cmd/Ctrl+Enter로 현재 페이지에 모아 적용한다. [도구 사용성 검토](TOOLBAR_UX_REVIEW.md).

## 2026-10-29 〜 10-30 영역 참조(region_ref) 전환

번역 번호(`source_region_id`)와 현재 R 번호는 서로 다른 번호 공간인데, 영역을 다시 계산하면 번역·수동값·화자가 엉뚱한 R에 붙는 사고가 있었다. 번호 대신 TG 집합 기반의 영역 참조로 찾도록 단계별로 바꿨다. `pipeline/region_refs.py`가 해석기다.

- 단계 1-2: 회귀 fixture와 해석기. `region_ref`/`region_instance_ref`를 TG 집합에서 만들고 exact·shrink·merge·split·geo·unmatched로 판정한다.
- 단계 3-4: 페이지 상세 응답에 읽기용 projection을 붙이고, Step 3·4 출력(`regions.json`, `integrated.json`)에 `region_ref`, `source_region_ref`, `region_ref_scheme`을 덧붙인다. `region_id`, `source_region_id`, `sentence_id`, 프롬프트 번호는 그대로다.
- 단계 5: 렌더러가 번역 조각, Overlay 수동값, 복원 억제를 참조로 찾는다. `regions.json`이 없으면 예전 번호 경로를 유지한다.
- 단계 6: Overlay 수동값 저장·편집이 참조 키(`overlay_ref_key`)를 쓴다. 화면 라벨은 현재 R 번호다. 구버전 번호 키 요청은 서버가 변환하고, `regions_by_ref`와 구형 `regions`를 함께 기록한다.
- 단계 7: 화자(`/api/speakers`)와 영역 번역 메모(`/api/region-context`)가 `by_region_ref`, `context_by_region_ref`에 기록된다. 구형 키는 병행하며 읽을 때 `by_region_ref → by_region → by_tg` 순이다.
- 단계 8: 처리 제외와 LaMa 고정은 처리 인스턴스 참조(`instance_ref`)를 함께 기록해, 같은 TG의 자동 영역과 수동 영역을 구분한다. 분석 탭 선택은 재분석 뒤 참조를 따라가며 없으면 해제된다. MCP `view_region` 등에 선택 인자 `region_ref`가 추가됐다.
- 2026-10-30 지우기 보정: `guidance.custom_masks`의 흰칠은 페이지 원본 좌표라 영역 변화와 무관하다. OCR 시트 반영은 영역 단위다. R을 지정해 칠한 기록은 `instance_ref`·`region_ref`로 처리 인스턴스를 따라가고(`region_refs.py` POLICY `custom_mask`: 정확 일치만 붙고 합침·분할·축소는 휴면), 지정 없는 기록은 Step 3에서 가장 많이 겹치는 영역에 붙는다. 결과 영역에 `ocr_crop_rect`·`custom_mask_ocr`, `regions.json` 최상위에 `custom_masks_applied`가 덧붙는다. 기존 키(`bubble_rect`, `unboxed_rect`, 좌표 키)는 바뀌지 않는다.

저장된 `bubble/unboxed`와 좌표 키는 전역 치환하지 않는다. 구버전 응답은 예전 번호 경로로 계속 읽는다.
