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
| `main_v2.py` | 기존 영역 JSON을 상세 응답에서 새 의미로 읽기 |
| `pipeline/dialogue_extractor.py` | 신규 영역 JSON의 파생 필드, 최종 영역 처리 기준 조회 통일 |
| `pipeline/background_restore.py` | 텍스트 기반 영역을 복원 seed로 고르는 조건 |
| `pipeline/gemini_ocr_translate_v5.py` | 텍스트 기반 통합 판정과 신규 integrated 파생 정보 |

`bubble_detector.py`, `unboxed_text_detector.py` 등 알고리즘·호환 모듈 파일명과 기존 private 함수명은 유지했다. 검출 알고리즘, rect 계산, 분류 임계값, 마스크·복원 방식은 이 작업에서 바꾸지 않았다. OpenAI/Gemini 모델·키 설정도 변경하지 않았다.

## 검증

- `tests/test_region_processing_basis.py`: 기존 타입 우선순위, 신규 필드, 미분류 처리, Python/JS 판정 일치, 오래된 캐시 심볼릭 링크의 API 읽기 전용성.
- 기존 세션 상세, Guidance, OCR segment, 배경 복원 관련 테스트를 실행했다.
- 프론트엔드를 빌드하고 복사본 00008에서 Edge 경로의 경계 기반 영역과 과거 U4가 R4·텍스트 기반으로 표시되는 것을 확인했다.
- 기존 가이드의 B/U 키와 저장 키를 유지한 채 조작 예시를 다시 캡처해 `docs/editor_manual.html`에 내장했다. 분류나 보정 품질의 시각적 정답을 새로 확정하지 않았다.

## 문서 관리 위치

사용법과 구조는 `docs/`의 정본 문서에서 관리한다. 이번 기록은 같은 폴더의 시점 기록(`record`)으로 등록한다. 모든 최상위 docs 문서는 `docs/DOC_SOURCES.json`에 역할과 관련 소스를 선언한다.

- 사용법: `docs/editor_manual.html`
- 구조: `docs/CODEBASE_FILE_MAP.md`
- 작업 상태·과거 진행 기록: `docs/PIPELINE_STATUS.md`, `docs/FLOODFILL_MASKING_PROGRESS.md` 등. 과거 기록의 용어는 당시 맥락을 보존한다.
- 시각 판정 사례: `docs/cases/README.md`
- 검증 스크립트·정리 가능한 실행 산출물: `proof/src/`, `proof/`의 타입별 하위 폴더
- `reports/`에도 보고서가 있지만 현재 기술 문서의 정본·변경 기록 관리 위치는 `docs/`다.

Claude와 다른 에이전트는 `AGENTS.md`, `CLAUDE.md`의 링크를 통해 이 기록을 읽고 새 용어를 사용한다. 새 처리 기준을 추가하는 변경과 단순 표시 이름 변경을 구분하고, 기존 JSON 문자열·캐시 경로를 일괄 치환하지 않는다.
