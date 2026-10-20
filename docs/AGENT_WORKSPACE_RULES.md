<!-- 에이전트 공용 작업 폴더 규칙 문서 -->
# Agent Workspace Rules

> 마지막 확인: 2026-10-06. `pytest.ini` 추가에 따라 테스트 위치 규칙을 덧붙였다. 같은 날 바뀐 `pipeline_runner`의 subprocess 실행 방식은 폴더 규칙에 영향이 없다.


이 문서는 Codex, Claude 등 에이전트가 공통으로 따라야 하는 작업 폴더 규칙을 정의한다.

## 기본 원칙

- 중간 산출물은 실행 작업 폴더의 타입별 하위 폴더에 저장한다.
- 실행 작업 폴더 루트에는 플랫 산출물을 만들지 않는다.
- 최종 text overlay 이미지만 `output_dir`에 저장한다.
- 에이전트가 로컬 검증을 수행할 때 `work_dir`가 `proof/`이면 `output_dir`도 `proof/` 내부로 잡는다.
- 임시 디버그 이미지는 `misc/` 또는 목적이 분명한 하위 폴더에 저장한다.
- `proof/src/*.py`는 검증용 소스 스크립트이므로 생성 산출물 정리 대상에서 제외한다.

## 표준 실행 폴더

파이프라인 실행은 `work_dir`와 `output_dir`를 분리한다.

```text
work_dir/              중간 산출물 전용
output_dir/            최종 결과물 전용
```

UI 실행에서는 보통 `temp_output/{session}/`가 `work_dir` 역할을 한다.
로컬 검증에서는 `proof/`를 `work_dir`와 `output_dir`로 함께 사용할 수 있다.
에이전트 검증 실행에서는 최종 결과만 `proof/` 루트에 직접 저장한다.

## work_dir 구조

```text
work_dir/
  json/          JSON 산출물
  flood_vis/     FloodFill 시각화 이미지
  masked/        masking 중간 결과와 최종 masked 이미지
  ocr_sheets/    Gemini OCR 입력 sheet 이미지
  merge/         masked 이미지와 overlay 결과 비교 이미지
  inpaint/       inpaint 실험 산출물
  restore/       편집기 배경 복원 탭 상태 (restore/{name}/state.json, 마스크·확정 패치 PNG, jobs/)
  page_policy/   페이지 제외·복원 및 analysis_route 설정 (캐시와 독립, 원본/기존 결과 유지)
  image_generation/ 이미지 생성 권장·요청·미검토 후보 (원본/최종 출력과 분리)
  reviews/       MCP 검토 초안 (reviews/{image_id}/{proposal_id}.json; guidance와 별도, 미적용 상태)
  misc/          기타 임시 디버그 산출물
```

대표 파일 배치는 다음과 같다.

```text
work_dir/json/{name}_cv.json
work_dir/json/{name}_cv_clusters_v5.json
work_dir/json/{name}_flood_regions.json
work_dir/json/{name}_flood_vis.json
work_dir/json/{name}_integrated.json
work_dir/flood_vis/{name}_flood_debug.jpg
work_dir/masked/{name}_flood_pre_masked.jpg
work_dir/masked/{name}_masked.jpg
work_dir/ocr_sheets/{name}_flood_sheet.jpg
work_dir/ocr_sheets/{name}_flood_indexed_sheet.jpg
work_dir/merge/{name}_merge.jpg
```

## output_dir 구조

`output_dir`에는 최종 text overlay 결과만 저장한다.

```text
output_dir/{name}_final.jpg
```

`output_dir`에는 `*_cv.json`, `*_masked.jpg`, `*_flood_vis.json`, `*_merge.jpg` 같은 중간 산출물을 저장하지 않는다.
에이전트 검증 실행의 `output_dir`는 `output/` 아래가 아니라 `proof/`이다.

## proof 규칙

`proof/`는 로컬 검증 전용 scratch work_dir다.
생성 산출물은 언제든 삭제될 수 있다.
루트에는 최종 `*_final.jpg`만 직접 생성한다.
그 외 JSON, flood_vis, masked, merge, ocr_sheets 산출물은 타입별 하위 폴더에 둔다.

검증 보조 스크립트는 `proof/src/`에 둔다.
메인 파이프라인으로 승격된 흐름은 `run_pipeline.py`를 우선 사용한다.
pytest 테스트는 `tests/`에 둔다. `pytest.ini`가 `testpaths = tests`로 고정돼 있어 `proof/`, `archive/` 아래의 `test_*.py`는 그냥 `pytest`를 실행해도 수집되지 않는다.

보존 대상은 다음 소스 스크립트다.

```text
proof/src/*.py
```

`proof/`를 정리할 때는 생성 파일만 삭제하고 검증 스크립트는 유지한다.

```bash
find proof -type f ! -path 'proof/src/*' -delete
```

필요한 하위 폴더는 파이프라인이 다시 생성한다.

## 금지 패턴

- `work_dir` 루트에 `00031_cv.json` 같은 플랫 중간 파일을 직접 생성하지 않는다.
- `output_dir`에 compare, sheet, flood debug, masked 이미지를 저장하지 않는다.
- 에이전트 검증 작업에서 `--work-dir proof`와 `--output output/...`을 같이 쓰지 않는다.
- 에이전트 검증 작업에서 `--work-dir proof`를 쓰면 보통 `--output proof`를 같이 쓴다.
- `CLAUDE.md`나 `AGENTS.md`에 폴더 규칙을 중복으로 길게 복사하지 않는다.
- 폴더 규칙 변경이 필요하면 이 문서를 먼저 갱신하고 각 에이전트별 가이드에서는 링크만 유지한다.

OpenAI 전환(2026-10-20): pipeline_runner의 Step 4 호출 로그와 모델 기본값이 OpenAI로 변경됐으며 work_dir/output_dir 배치 규칙은 동일하다.

페이지별 `analysis_route: standard` 선택도 `work/page_policy/{image_id}.json`에 저장한다. 페이지 제외 여부와 서로를 보존하며, 미검토 생성 후보는 기존 image_generation 폴더에 유지한다.

## 브라우저 검증

사용자가 작업 중인 브라우저 창에 자동화 탭이 떴다 사라지지 않도록, UI 검증은 별도 headless 브라우저를 기본으로 사용한다. 현재 검증 실행기는 `proof/src/run_headless_browser_check.cjs`이다. 사용자의 기존 창·탭을 검증용으로 열거나 전환하지 않는다.
