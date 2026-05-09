# 코드베이스 파일 맵

작성일: 2026-05-05

OpenCV 기반 말풍선/box 보정 로직을 새로 붙이기 전 기준점이다. `archive/`, 출력물, 이미지, 가상환경은 제외하고 현재 실행 경로의 소스만 정리했다.

## 현재 구조

```text
NEW_COMIC_TRANSLATOR/
├── main.py                    # Gradio UI, 표준 파이프라인 wrapper
├── run_debug_pipeline.py       # Debug 이미지 일괄 실행 wrapper
├── run_test_pipeline.py        # test_image 범위 실행 wrapper
├── visualize_debug.py          # Debug bbox 시각화
├── batch/                      # Google Gemini Batch API 보존 영역
│   ├── submit_batch_folder.py
│   ├── receive_batch_result.py
│   ├── check_all_batches.py
│   ├── run_batch.sh
│   └── run_batch_auto.sh
└── pipeline/
    ├── cloud_vision_api.py
    ├── cluster_cv_v5.py
    ├── gemini_ocr_translate_v5.py
    ├── apply_masking.py
    ├── render_text_overlay.py
    ├── translate_cover.py
    └── __init__.py
```

## 표준 파이프라인

```text
입력 이미지
  ↓
Cloud Vision API
  → temp_output/{name}_cv.json
  ↓
Cluster CV V5
  → temp_output/{name}_cv_clusters_v5.json
  ↓
Gemini OCR + 번역 + integrated JSON 생성
  → temp_output/{name}_integrated.json
  ↓
White Masking
  → temp_output/{name}_masked.jpg
  ↓
Text Overlay
  → output/{subfolder}/{name}_final.jpg
```

예전 `integrate_ocr_v3.py` 단계는 메인 파이프라인에서 제거됐다. 현재는 `pipeline/gemini_ocr_translate_v5.py`가 Gemini 응답을 바로 masking/overlay가 읽는 integrated JSON 구조로 저장한다.

## Root Wrapper

### `main.py`

Gradio UI와 표준 파이프라인 진입점이다.

- `process_single_image()`가 단일 이미지 처리 흐름을 담당한다.
- Cloud Vision과 clustering은 import로 실행한다.
- Gemini integrated 생성, masking, overlay는 subprocess로 `pipeline/*.py`를 호출한다.
- `batch_translate_images()`는 여러 이미지 처리를 `ThreadPoolExecutor`로 병렬화한다.
- 컬러 이미지 자동 감지 시 `pipeline/translate_cover.py` 경로를 사용한다.

주의:

- `skip_cv` 인자는 하위 호환용으로 남아 있다. 현재 Gemini integrated 생성에는 cluster hint가 필요하므로 캐시가 없으면 CV/cluster를 생성한다.
- 기본 폰트 경로는 일부 UI 값과 실제 `Fonts/` 폴더 대소문자가 다를 수 있어 추후 정리 대상이다.

### `run_debug_pipeline.py`

`Debug/00013`, `00014`, `00016`, `00019`를 `main.process_single_image()`로 처리한다.

- 최종 결과와 중간 파일을 모두 `temp_output/Debug/`에 모은다.
- 처리 후 `visualize_debug.py`를 호출해 `{번호}_debug.jpg`를 만든다.

### `run_test_pipeline.py`

`test_image/00020.jpg`부터 `00035.jpg`까지를 현재 메인 파이프라인으로 처리한다.

- 예전 CV Grid → cluster → OCR → integration 개별 subprocess 테스트 경로는 제거했다.
- 출력은 `temp_output/test_pipeline/`에 모은다.

### `visualize_debug.py`

Debug 산출물 시각화 도구다.

- 입력: 원본 이미지, `{번호}_cv_clusters_v5.json`, `{번호}_integrated.json`
- 출력: `temp_output/Debug/{번호}_debug.jpg`
- 빨간색: sentence/union bbox
- 파란색: matched cluster bbox
- 초록색: line bbox

## Batch API 보존 영역

`batch/` 아래 파일은 대용량 Gemini 이미지 Batch API 실험/운영용이다. Free tier에서 실제 대용량 작업이 막히는 문제가 확인되어 메인 파이프라인과 분리했다.

- `batch/submit_batch_folder.py`: 폴더 이미지를 업로드하고 Async Batch 작업 생성
- `batch/receive_batch_result.py`: batch 결과 조회/다운로드/복구
- `batch/check_all_batches.py`: batch 상태 확인
- `batch/run_batch.sh`: 기본 prompt로 폴더 반복 제출
- `batch/run_batch_auto.sh`: custom prompt로 폴더 반복 제출

실행은 프로젝트 루트에서 한다.

```bash
python3 batch/submit_batch_folder.py input/some_folder
python3 batch/receive_batch_result.py
bash batch/run_batch.sh
```

## Pipeline 파일

### `pipeline/cloud_vision_api.py`

Google Cloud Vision OCR wrapper다.

- `CloudVisionAPI`가 credential과 `vision.ImageAnnotatorClient`를 관리한다.
- 단일 이미지 OCR과 Grid OCR 유틸리티가 있다.
- 출력은 이후 `cluster_cv_v5.py`가 읽는 Vision JSON이다.

### `pipeline/cluster_cv_v5.py`

Cloud Vision OCR block을 line/cluster로 병합한다.

- `pass1_create_lines()`: block → line, 같은 세로줄 기준 BFS 병합
- `filter_furigana_lines()`: 후리가나로 보이는 작은 line 제거
- `pass2_create_clusters()`: line → cluster, 4-stage rule 병합
- `sort_clusters_reading_order()`: 일본어 세로쓰기 기준 오른쪽→왼쪽, 위→아래 정렬

OpenCV 말풍선 로직을 붙일 1차 후보 파일이다. 현재는 말풍선 contour가 아니라 bbox 간 거리와 y 시작점 기준으로만 묶기 때문에, 가까운 다른 말풍선 텍스트가 섞이거나 같은 말풍선 안의 먼 텍스트가 분리될 수 있다.

### `pipeline/gemini_ocr_translate_v5.py`

Gemini 한 번의 호출로 OCR, 번역, 문장 단위 cluster 매칭을 수행하고 integrated JSON을 생성한다.

- 입력: 원본 이미지 + `*_cv_clusters_v5.json`
- 출력: `*_integrated.json`
- 출력 구조: `clusters`, `unmatched_clusters`, `model`
- 각 cluster는 `sentence_id`, `text`, `text_translated`, `matched_cluster`, `matched_cluster_details`, `union_bbox`, `bounding_rect`, `location`을 포함한다.

### `pipeline/apply_masking.py`

`*_integrated.json`의 bbox를 기준으로 원문 텍스트 영역을 흰색 마스킹한다.

- `union_bbox` 또는 `bounding_rect`를 사용한다.
- `location == "in_bubble"`이면 corner fade 마스킹을 적용한다.
- 그 외에는 반투명 흰색 마스킹을 적용한다.

OpenCV contour가 들어오면 말풍선 내부로 mask를 제한하거나 contour mask와 교차시키는 위치가 이 파일이다.

### `pipeline/render_text_overlay.py`

마스킹된 이미지 위에 한국어 번역문을 렌더링한다.

- bbox 우선순위: `union_bbox → bounding_rect_expanded → bounding_rect`
- line 수와 orientation에 따라 렌더링 영역을 확장한다.
- 가중 글자수 기반 줄바꿈과 폰트 크기 보정을 수행한다.

### `pipeline/translate_cover.py`

컬러 표지/일러스트를 Gemini image model로 직접 번역 이미지 생성하는 별도 경로다.

## Box 병합 책임 구분

| 문제 유형 | 우선 확인 파일 |
| --- | --- |
| 글자 block이 line으로 잘못 묶임 | `pipeline/cluster_cv_v5.py` |
| 여러 line이 cluster로 잘못 묶임 | `pipeline/cluster_cv_v5.py` |
| Gemini가 cluster id를 잘못 매칭함 | `pipeline/gemini_ocr_translate_v5.py` |
| union bbox가 너무 크거나 작음 | `pipeline/gemini_ocr_translate_v5.py`, `pipeline/render_text_overlay.py` |
| 마스킹이 말풍선 경계를 침범함 | `pipeline/apply_masking.py` |
| 번역 텍스트가 영역에 맞지 않음 | `pipeline/render_text_overlay.py` |

## OpenCV 말풍선 로직 적용 후보

1. `pipeline/bubble_detector.py` 신규 추가
   - 입력: 원본 이미지
   - 출력: contour polygon, bounding rect, binary mask, confidence
2. `visualize_debug.py`에 contour overlay 추가
3. `cluster_cv_v5.py`에서 같은 contour 내부 line만 cluster 후보로 허용
4. `gemini_ocr_translate_v5.py`에서 `union_bbox`를 contour rect 기준으로 보정
5. `apply_masking.py`에서 contour mask와 bbox mask를 교차

## 검증 명령

```bash
. .venv/bin/activate
python3 -m compileall main.py run_debug_pipeline.py run_test_pipeline.py visualize_debug.py pipeline batch
```
