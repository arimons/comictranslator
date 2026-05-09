# Comic Translator - Codex 작업 가이드

## Rules

- 응답 언어: 한국어
- 코드 주석: 한국어 허용
- API 키: `.env` 파일로 분리, 하드코딩 금지
- 가상환경: `.venv`

## 현재 프로젝트 개요

일본어 만화 이미지를 한국어 번역 이미지로 변환하는 파이프라인입니다.

현재 표준 경로는 예전 `OCR → translate → integrate` 분리형이 아니라, Gemini 호출 한 번으로 OCR/번역/integrated JSON 생성을 같이 수행합니다.

```text
Cloud Vision → Clustering V5 → Gemini OCR+번역+Integrated JSON → Masking → Text Overlay
```

## 현재 실행 구조

```text
NEW_COMIC_TRANSLATOR/
├── main.py                    # Gradio UI, 표준 파이프라인 wrapper
├── run_debug_pipeline.py       # Debug 이미지 일괄 실행
├── run_test_pipeline.py        # test_image 범위 실행
├── visualize_debug.py          # Debug bbox 시각화
├── batch/                      # Gemini Batch API 보존 영역
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
    └── translate_cover.py
```

## 표준 파이프라인

```text
입력 이미지
  ↓
Step 1: Cloud Vision API
  → temp_output/{번호}_cv.json
  ↓
Step 2: Clustering V5
  → temp_output/{번호}_cv_clusters_v5.json
  ↓
Step 3: Gemini OCR + 번역 + integrated JSON 생성
  → temp_output/{번호}_integrated.json
  ↓
Step 4: White Masking
  → temp_output/{번호}_masked.jpg
  ↓
Step 5: Text Overlay
  → output/{번호}_final.jpg
```

## 핵심 파일

- `main.py`: UI와 파이프라인 orchestration
- `pipeline/cloud_vision_api.py`: Cloud Vision OCR 호출
- `pipeline/cluster_cv_v5.py`: CV block → line → cluster 병합
- `pipeline/gemini_ocr_translate_v5.py`: Gemini OCR/번역/integrated JSON 생성
- `pipeline/apply_masking.py`: integrated bbox 기반 마스킹
- `pipeline/render_text_overlay.py`: 번역 텍스트 렌더링
- `visualize_debug.py`: union/cluster/line bbox 시각화

## Debug 실행

```bash
. .venv/bin/activate
python3 run_debug_pipeline.py
```

결과:

- `temp_output/Debug/{번호}_cv.json`
- `temp_output/Debug/{번호}_cv_clusters_v5.json`
- `temp_output/Debug/{번호}_integrated.json`
- `temp_output/Debug/{번호}_masked.jpg`
- `temp_output/Debug/{번호}_final.jpg`
- `temp_output/Debug/{번호}_debug.jpg`

## Box 병합 작업 기준

OpenCV 말풍선 contour 로직을 붙일 때 우선순위:

1. `pipeline/cluster_cv_v5.py`
   - line/cluster 후보를 말풍선 contour 단위로 제한
2. `pipeline/gemini_ocr_translate_v5.py`
   - `matched_cluster`, `union_bbox`, `matched_cluster_details` 보정
3. `pipeline/apply_masking.py`
   - contour mask와 bbox mask 교차
4. `pipeline/render_text_overlay.py`
   - contour 내부 렌더링 가능 영역 계산
5. `visualize_debug.py`
   - 기존 bbox와 OpenCV contour를 같은 이미지에 표시

## Batch API

`batch/` 폴더는 Gemini Async Batch API용 보존 영역입니다. Free tier에서 대용량 작업이 막히는 문제가 있었으므로 메인 파이프라인과 분리합니다.

```bash
python3 batch/submit_batch_folder.py input/some_folder
python3 batch/receive_batch_result.py
bash batch/run_batch.sh
```

## 주의사항

- `archive/`는 현재 실행 경로가 아닙니다.
- `integrate_ocr_v3.py`, `gemini_api.py`, `bbox_utils.py`, `preprocess_for_cv.py`, `gemini_batch_api.py`는 현재 메인 파이프라인에서 제거되어 archive에 있습니다.
- `*_ocr_v5.json`, `*_integrated_translated.json`은 현재 표준 파이프라인 산출물이 아닙니다.
- `*_integrated.json`이 masking/overlay의 기준 JSON입니다.
