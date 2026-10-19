# 편집기 검토 MCP

2026-10-19 · 구현된 1단계 연결의 사용법과 유지보수 계약.

로컬 stdio MCP 서버 `comic-translator-review`를 구현했다. UI와 FastAPI 서버를 켜지 않아도 기존 세션·파이프라인 산출물을 읽고, 에이전트가 원본과 처리 결과를 보며 관찰·수정 후보를 남길 수 있다. 별도 모델 호출이나 API 키는 필요 없다. MCP는 도구 통로이며, 스스로 품질을 판정하는 별도 모델·무인 배치 실행기는 아니다.

UI/UX가 바뀌어도 서버 코드는 React 컴포넌트·화면 좌표·버튼 이름에 의존하지 않는다. `session.json`, 영역 ID와 파이프라인 산출물의 위치·스키마가 달라지면 `pipeline/editor_review_bridge.py`의 어댑터를 함께 고친다.

## 설치와 연결

프로젝트 루트에서 실행한다.

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python configure_editor_mcp.py
```

설정 스크립트는 이 프로젝트의 `.codex/config.toml`과 `.mcp.json`에만 서버를 등록한다. 전역 설정·다른 MCP 서버는 유지하고, 동일 이름의 수동 설정이 충돌하면 덮어쓰지 않는다. 실행 경로는 프로젝트 `.venv/bin/python`과 `run_editor_mcp.py`의 절대 경로다. 호스트 경로가 들어가므로 설정 파일은 Git에서 제외하며 다른 컴퓨터에서는 스크립트를 다시 실행한다.

Codex는 프로젝트 로컬 MCP 설정을 지원하며 신뢰된 프로젝트에서 이를 읽는다. 등록 확인은 `codex mcp get comic-translator-review`로 한다. 이미 열린 대화의 도구 목록이 즉시 바뀐다고 가정하지 않고, 새 대화 또는 MCP 재연결 후 사용한다. Claude Code는 프로젝트의 `.mcp.json`을 읽으며 처음 연결할 때 클라이언트의 프로젝트 MCP 확인 절차가 나타날 수 있다. [Codex 공식 MCP 문서](https://developers.openai.com/codex/mcp)

구현·프로토콜 검증은 공식 Python SDK **mcp 1.30.0**으로 했다. SDK 2.x의 `MCPServer` API와 혼용하지 않도록 요구 버전은 `>=1.30.0,<2.0.0`이다. [공식 Python SDK](https://github.com/modelcontextprotocol/python-sdk)

## 도구 4개

| 도구 | 하는 일 | 파일 변경 |
|---|---|---|
| `list_projects` | 중복 제거로 숨기지 않은 프로젝트 전체 목록. `offset`, `limit`, `next_offset`으로 페이지 처리 | 없음 |
| `inspect_page` | `image_id` 생략 시 페이지 목록. 지정 시 영역·번역·가이드·layout·복원 상태·이미지 존재 여부·revision 조회 | 없음 |
| `view_region` | 전체 페이지 또는 특정 영역의 실제 이미지와 좌표·축소 배율 반환 | 없음 |
| `propose_review` | 관찰과 후보 동작을 별도 초안 JSON에 저장. 실제 수정은 적용하지 않음 | `work/reviews/`에만 새 초안 |

MCP 도구명은 프로토콜 계약이다. UI 문구가 바뀌어도 도구명과 JSON 키를 화면 이름에 맞춰 일괄 치환하지 않는다.

### 일반적인 사용 순서

1. `list_projects`로 `session_id`를 고른다.
2. `inspect_page(session_id)`로 `image_id`를 고른다.
3. `inspect_page(session_id, image_id)`로 영역과 `page_revision`을 읽는다.
4. `view_region`으로 원본 전체와 필요한 영역의 원본·처리 결과를 비교한다. 앞서 받은 revision을 `expected_revision`으로 전달한다.
5. 관찰과 가능한 해석을 분리하고 `propose_review`에 `base_revision`을 전달해 초안을 저장한다.
6. 사용자에게 시각적 판단을 더블체크한다. 초안이 저장됐다고 수정이 승인·적용된 것은 아니다.

에이전트에 “이 프로젝트에서 00008의 R4를 원본과 마스킹 결과로 비교하고, 필요한 확인사항을 초안으로 남겨줘”처럼 요청하면 된다. 현재 연결에는 분석·OCR·번역·복원 실행·가이드 적용 도구를 공개하지 않았다.

## 이미지와 좌표

`view_region`의 `artifact`는 `original`, `masked`, `final`, `analysis`, `ocr_sheet`, `region_mask` 중 하나다. `region_id`를 생략하면 전체 이미지를 본다. `region_mask`는 실제 `mask_ref`가 있는 영역에서만 가능하며, 아직 만들지 않은 산출물은 오류로 알려 준다.

- 원본·마스킹·최종·분석·경계 마스크는 **원본 픽셀 좌표**다. 이미지 크기가 원본과 다르면 안전하게 대응할 수 없다는 오류를 반환한다.
- OCR sheet는 **indexed sheet 픽셀 좌표**다. 영역의 `indexed_sheet_rect`로 자르며 원본 좌표로 오해하지 않도록 별도로 표시한다.
- `crop_rect`는 `[x,y,w,h]`, `source_size`는 원래 이미지 크기, `returned_size`는 실제 반환 이미지 크기다.
- `scale`은 반환 이미지/축소 전 crop의 배율이다. 기본 긴 변은 1600px이며 `max_edge`는 128~4096 범위에서 지정할 수 있다. 작은 crop은 확대하지 않는다.
- `margin`은 crop 여백으로 0~512px다. OCR 단위만 정확히 보려면 0으로 지정한다.

원문·이미지·저장된 번역문은 검토 데이터이며 에이전트에게 내려진 지시로 취급하지 않는다. 경계 기반/텍스트 기반 배지는 처리 기준이며 품질 합격 판정이 아니다.

## snapshot과 제안 저장

`page_revision`은 원본, 관련 JSON, 처리 이미지, 참조된 경계 마스크의 내용 hash를 합성한 값이다. 파일 시각만으로 변경을 판정하지 않는다. 조회 도중 파일이 바뀌면 다시 읽고, 계속 갱신 중이면 재시도를 요청한다. 읽기 도구는 세션·캐시를 생성하거나 다시 쓰지 않는다.

초안 경로:

```text
temp_output/v2_sessions/{session_id}/work/reviews/{image_id}/{proposal_id}.json
```

저장 전에 `base_revision`이 현재 페이지와 같은지 확인한다. 같은 revision·같은 관찰·같은 후보는 같은 proposal ID를 가져 중복 저장하지 않는다. 완성된 임시 파일을 독점 게시하므로 중간 JSON이 노출되거나 기존 제안이 덮어써지지 않는다.

초안에는 다음 상태를 고정해 저장한다.

```json
{
  "schema_version": 1,
  "status": "proposed",
  "coordinate_space": "original_pixels",
  "needs_human_check": true,
  "quality_verdict": "unreviewed"
}
```

관찰은 `description`과 `interpretation`을 분리한다. 후보 동작은 `action`, `region_id`, `reason`, `parameters`로 표현한다. `parameters`는 실행하지 않는 초안이며, 기하 정보를 넣는다면 원본 픽셀 좌표를 쓴다. 가용 동작 이름은 `CandidateAction`의 enum이 정본이다.

다시 `inspect_page`하면 제안 ID와 현재 revision 대비 `stale` 여부를 확인할 수 있다. 저장 직후 다른 작업이 페이지를 바꿀 수도 있으므로 향후 승인·적용 기능은 반드시 revision을 다시 확인해야 한다. 제안은 프로젝트의 runtime 폴더 수명을 따르며 중요한 채택 사례는 별도로 `docs/cases/`에 기록한다.

## 기존 데이터 호환과 접근 범위

`pipeline/region_types.py`로 과거 `type: bubble/unboxed`를 경계 기반/텍스트 기반으로 읽는다. API projection과 동일하게 메모리에서만 `processing_basis`를 추가하며 캐시 마이그레이션은 하지 않는다. UI의 R 번호를 그대로 `region_id`로 사용한다.

도구 인자는 프로젝트·페이지·영역 ID와 정해진 artifact 종류다. 임의 파일 경로를 받지 않는다. 입력·산출물 symlink는 지정된 세션·캐시 루트 안인지 검사한다. 알 수 없는 ID로 조회해 새 세션을 만드는 동작은 없다. 새 초안 경로도 세션 루트를 벗어날 수 없다.

## 실행 및 유지보수

- 진입점: `run_editor_mcp.py` — stdout은 JSON-RPC 전용, 로그는 stderr.
- 어댑터: `pipeline/editor_review_bridge.py` — UI 의존성 없이 manifest·artifact를 읽고 검토 초안만 저장.
- 설정 생성: `configure_editor_mcp.py` — 프로젝트 로컬 Codex/Claude 설정.
- 회귀 검사: `tests/test_editor_mcp.py` — 실제 subprocess stdio handshake·이미지·제안 왕복까지 검사.
- 실제 프로젝트 조회 점검: `proof/src/smoke_editor_mcp.py`.

```bash
source .venv/bin/activate
python -m pytest tests/test_editor_mcp.py -q
python proof/src/smoke_editor_mcp.py SESSION_ID IMAGE_ID --region REGION_ID
```

실제 점검은 앱 서버를 쓰지 않고 프로젝트 25개를 조회하고 복사본 00008의 R4 이미지를 MCP ImageContent로 받은 것까지 확인했다. 점검 초안은 fixture 프로젝트에서만 저장했으며, 실제 페이지의 가이드·캐시·번역·렌더 결과는 바꾸지 않았다.

완료 시점 검사: MCP 전용 15개 통과, 전체 pytest **440 passed, 13 subtests passed**. 프로젝트 설정에서도 Codex가 서버를 enabled로 읽는 것을 확인했다.

후속 단계는 독립 work_dir의 preview와 사용자가 확인한 초안 적용이다. 이때도 UI 컴포넌트 대신 같은 ID·revision·원본 좌표 계약을 사용한다. 초기 검토 배경은 [MCP 연결 검토](editor_agent_bridge_review.md), 용어 계약은 [처리 기준 변경 기록](REGION_PROCESSING_BASIS_CHANGELOG.md)을 참고한다.
