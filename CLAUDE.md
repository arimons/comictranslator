# Comic Translator 작업 가이드

현재 프로젝트 기준은 `AGENTS.md`와 `docs/CODEBASE_FILE_MAP.md`를 따른다.

요약:

- 표준 파이프라인: `Cloud Vision → Clustering V5 → Gemini OCR+번역+Integrated JSON → Masking → Text Overlay`
- 현재 표준 산출물: `*_cv.json`, `*_cv_clusters_v5.json`, `*_integrated.json`, `*_masked.jpg`, `*_final.jpg`
- `*_ocr_v5.json`, `*_integrated_translated.json`은 현재 표준 산출물이 아니다.
- `integrate_ocr_v3.py`, `gemini_api.py`, `bbox_utils.py`, `preprocess_for_cv.py`, `gemini_batch_api.py`는 현재 실행 파이프라인에서 제거되어 `archive/`에 있다.
- Gemini Batch API 관련 파일은 `batch/` 폴더에 보존하며 메인 파이프라인과 분리한다.

Debug 실행:

```bash
. .venv/bin/activate
python3 run_debug_pipeline.py
```
