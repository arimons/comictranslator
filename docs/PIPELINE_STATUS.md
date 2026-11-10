# Pipeline Status

최종 갱신: 2026-11-10 (코드 기준으로 전면 재작성)

이 문서는 표준 파이프라인의 단계별 연결 상태와 아직 남은 이슈를 한곳에서 확인하려는 개발자(프로젝트 소유자와 작업 에이전트)를 위한 현황판입니다. 2026-05-16 판은 이미 없어진 파일을 "연결됨"으로 적고 있어서, 단계 구성은 `pipeline/pipeline_runner.py`의 `run_single_image()`와 `run_pipeline.py`를 기준으로 다시 확인해 썼습니다.

파일·함수별 역할은 [CODEBASE_FILE_MAP.md](CODEBASE_FILE_MAP.md), FloodFill 세부는 [FLOODFILL_MASKING_PROGRESS.md](FLOODFILL_MASKING_PROGRESS.md)(2026-05-16 기준 기록), 고난도 CV 사례는 [cases/README.md](cases/README.md), 기능별 후속 작업은 [IMPROVEMENT_BACKLOG.md](IMPROVEMENT_BACKLOG.md)를 참고합니다.

---

## 파이프라인 단계별 상태

`run_single_image()`는 아래 5단계를 순서대로 subprocess로 실행합니다. 어느 단계든 실패하면 그 이미지는 실패로 끝납니다. 이전 판에 있던 별도의 "White Masking" 단계는 없어졌고, 마스킹 결과(`*_masked.jpg`)는 Step 3이 직접 만듭니다.

| 단계 | 모듈 | 주요 산출물 | 상태 |
|---|---|---|---|
| Step 1 Cloud Vision | `pipeline/cloud_vision_api.py` | `*_cv.json` | 연결됨. 이미지마다 1회 호출하며, 예전 9장 묶음 호출은 없어졌습니다 |
| Step 2 Text Grouping | `pipeline/text_group_builder_v2.py` | `*_text_groups.json` | 연결됨 |
| Step 3 대사 추출 | `pipeline/dialogue_extractor.py` (경계 기반 검출은 `pipeline/bubble_detector.py`) | `*_regions.json`, `*_masked.jpg`, `*_indexed_sheet.jpg`, `*_bubble_vis.jpg` | 연결됨. 경계 기반 검출·마스킹, 텍스트 기반 글자 지우기(LaMa), indexed OCR sheet 생성을 한 번에 합니다 |
| Step 4 OCR+번역 | `pipeline/gemini_ocr_translate_v5.py` | `*_integrated.json` | 연결됨. 기본은 OpenAI이고 `--model`로 모델을 고르며 Gemini도 선택할 수 있습니다. OCR 대상 영역이 없으면 `status: skipped`만 담습니다 |
| Step 5 Text Overlay | `pipeline/render_text_overlay.py` | `*_final.jpg`, `*_merge.jpg` | 연결됨. `regions.json`의 영역 정보로 위치를 정합니다 |

실행 엔진은 `pipeline/pipeline_runner.py`(`run_single_image()`, `run_batch_images()`)이고, CLI 진입점은 `run_pipeline.py`입니다. 단일 이미지도 배치 실행기를 거칩니다. 편집기 서버(`app.py`)는 같은 단계를 `stop_after` 옵션으로 끊어서(글자 묶음까지, OCR 전단계까지) 호출합니다.

Gradio UI(`main.py`)는 폐기되어 `archive/retired_gradio_ui/`에 있고, 현재 UI는 FastAPI 기반 `app.py`와 `frontend/`입니다. 구형 `*_flood_vis.json`, `*_flood_regions.json`, `*_flood_pre_masked.jpg`는 현재 표준 산출물이 아닙니다.

---

## 확정된 변경 이력

이전 판은 2026-05-16의 FloodFill 구제 로직 강화(touches_border 제외 조건 삭제, 합산 anchor 구제, 고비율 말풍선 구제, 흑백 이중분포 필터, 시드 탐색 3단계 등)와 Gemini OCR 재시도 강화, White Masking 연결을 상세히 적었습니다. 그중 FloodFill 구제 로직은 지금도 `pipeline/bubble_detector.py`에 남아 있고(`combined_anchor_rescue`, `high_ratio_bubble_rescued`, `_compute_flood_bimodality`, `_auto_split_flood_mask`, `_find_side_white_seed`), 세부는 [FLOODFILL_MASKING_PROGRESS.md](FLOODFILL_MASKING_PROGRESS.md)에 있습니다. 반면 `union_bbox_fallback`, OCR 재시도 함수 `_needs_retry()`, flood 기반 마스크 복원 함수 `revert_no_text_flood_regions()`는 현재 코드에서 찾을 수 없습니다. 표준 경로가 Step 3 한 단계로 합쳐지면서 사라진 것으로 보이며, 이 문서의 이력도 그에 맞춰 줄였습니다.

---

## 미해결 이슈

### 현재도 열려 있음으로 확인한 것

- **번역 결과 생성 단계에서 영역 매칭을 검증하지 않습니다.** anchor가 없는 영역과, 같은 텍스트 그룹이 한 영역에 중복 매핑되는 경우를 걸러내지 못합니다. [IMPROVEMENT_BACKLOG.md](IMPROVEMENT_BACKLOG.md)의 "기능 백로그" 항목과 [render_overlay_review_todo.md](render_overlay_review_todo.md)가 같은 문제를 추적합니다.
- **`anchor_ids == []`인 edge/flood 말풍선의 마스킹·복원 정책.** [render_overlay_review_todo.md](render_overlay_review_todo.md)의 "대기" 항목(00060 R13/R14)으로 아직 열려 있습니다.
- **00026 TG1/TG2의 split 시각 확인.** 같은 문서의 "재확인 중" 항목입니다.

### 현재 코드가 대체해 닫은 것

- **proof 배치의 Cloud Vision 호출을 `detect_text_grid`(9장 묶음)로 교체하는 일.** `detect_text_grid()`는 없어졌고 이미지 단위 호출이 표준입니다([CODEBASE_FILE_MAP.md](CODEBASE_FILE_MAP.md)에 명시). 해당 없음입니다.
- **새 overlay 엔진의 `page_font_band` 기준 글자 크기가 너무 작다는 피드백.** [render_overlay_review_todo.md](render_overlay_review_todo.md)가 "페이지 전체 font band cap은 폐기했다"고 적었으므로 그 기준의 이슈는 닫혔습니다. 남은 글자 크기 문제는 `choose_layout()` 내부 탐색 상한과 후보 선택 문제로 같은 문서에서 관리합니다.
- **packed OCR sheet의 row packing 한계.** 당시 함수 `create_packed_ocr_sheet`는 현재 코드에 없고, Step 3의 `build_dialogue_sheet()`가 읽기 순서로 crop을 모으는 방식으로 바뀌었습니다. 옛 이슈는 해당 없음입니다.
- **inpaint 기반 텍스트 교체(백로그).** 흰 박스 마스킹은 더 이상 쓰지 않습니다. 텍스트 기반 영역은 LaMa로 글자를 지우고, 편집기의 AI 글자 지우기는 `pipeline/background_restore.py`로 복원 패치를 합성합니다. 실험 코드는 `pipeline/experimental/inpaint_candidate.py`에 있습니다. 이전 판의 `cv2.inpaint` + alpha composite 흐름과 `apply_masking.py` 편입 계획은 이 구현으로 대체되었습니다. 당시 계획한 00032 이미지의 품질 비교는 현재 유효성을 확인하지 못했습니다.

---

## 확인 필요 (2026-05 기록, 현재 유효성 미확인)

아래는 2026-05-16 판에 적혀 있던 이슈입니다. 특정 이미지(`test_image`/`proof` 번호)의 결과를 눈으로 다시 봐야 판단할 수 있고, 이 작업에서는 재현하지 않았기 때문에 사실로 단정하지 않고 보존만 합니다. 이후 코드가 여러 번 바뀌어 이미 해결되었을 수 있습니다.

- 00107 text_overlay bbox 오배치: 번역 내용은 정상이고 bbox 선택 로직(S11 A[9,11,14], narrow_wide_split_rect)이 의심된다는 기록.
- 00109 A1 번역 누락: flood_area_ratio 0.1013이 `MAX_SAFE_AREA_RATIO`(현재 코드 값 0.15)를 넘는다고 적었으나, 기준값이 당시와 달라졌을 수 있음.
- 00010 A4 flood leak: anchor 중심이 flood_rect 밖일 때 `union_bbox_fallback`으로 처리되던 문제. 해당 fallback은 현재 코드에 없음.
- 00032 고난도 이미지: 손글씨 SFX를 CV가 일본어로 읽어 노이즈 신호가 없고 이미지 생성 fallback 후보라는 기록.
- narration_like fallback의 erase rect가 단순 union bbox라 말줄임표 같은 작은 기호를 놓친다는 기록(00021 A10).
- 1-ATG 복합 말풍선 shape-split(백로그): 하나의 ATG가 타원 둘이 결합된 말풍선 전체에 걸칠 때 erosion split으로 가상 anchor를 만드는 안. 결정 보류 상태였고, 이후 구조 변경 후 필요성은 재검토하지 않았음.
- `visualize_anchor_floodfill()`과 `build_ocr_region_sheet()`의 필터 불일치: 시각화 함수는 `pipeline/bubble_detector.py`에 남아 있지만, 이 함수가 만들던 `flood_vis.json`은 표준 산출물에서 빠졌습니다. 불일치가 실제로 남았는지는 확인하지 않았음.
- proof 파이프라인 테스트 현황(00001, 00029〜00040 미처리 등): 현재 `proof/`의 상태를 확인하지 않았음.
