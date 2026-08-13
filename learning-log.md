# Learning Log — 바이브 코딩 구조학습

## 1회차 (2026-07-09)
- 대상: NEW_COMIC_TRANSLATOR 자체 코드베이스 (홀수 회차)
- 주제: 설계 구조 — 전체 아키텍처 조망 (UI / 실행 엔진 / 파이프라인 단계 3계층)
- 배운 개념: `main_v2.py`(UI) · `pipeline_runner.py`(엔진) · `pipeline/`(단계별 로직)로 계층을 나눠, UI가 `main.py`→`main_v2.py`로 교체돼도 엔진과 단계 로직은 그대로 재사용됐다. CLI(`run_pipeline.py`)와 웹 UI가 같은 엔진을 공유한다.
