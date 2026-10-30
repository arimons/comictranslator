# 커스텀 마스크(지우기 보정) 계획서

작성 2026-10-30. 상태: §0의 확정 설계로 구현(2026-10-30). 실제 데이터 전후 확인과 적대적 리뷰는 남았다.

## 0. 확정 설계와 구현 (2026-10-30)

사용자와 여러 번 정정한 끝에 확정한 의도: 칠한 곳은 **자동 분석이 애매하게 놓친 자리의 수동 지정**이다. 처리는 두 가지다.
1. 결과 이미지에서 칠한 곳을 **흰색으로 칠한다**. LaMa·엔진 자동 판단은 쓰지 않는다.
2. 칠한 곳을 붙는 영역의 **OCR 시트 crop에 합쳐** 놓친 글자가 OCR·번역에 들어가게 한다. 이것이 핵심 의도다.

중간에 버린 안: 영역의 `safe_text_mask`에 합쳐 LaMa로 함께 지우기(1차 구현), 페이지 단위로 마지막에 LaMa로 지우기(`5021f72`). 두 안 모두 OCR에 들어가지 않았고, LaMa가 필요 없었다.

- **붙는 영역**: R을 선택하고 칠하면 그 R. 선택 없이 칠하면 Step 3에서 가장 많이 겹치는 영역(경계 기반은 영역 마스크, 그 밖은 사각형과 겹친 픽셀 수). 어느 영역과도 안 겹치면 OCR에는 넣지 않고 흰칠만 한다.
- **저장**: `guidance.custom_masks = [{id, mode: "add", strokes: [{points, width}], rects, polygons}]`. R 지정 기록에는 `instance_ref`·`region_ref`·`target_key`·`snapshot`·`display_region_id`가 더 붙는다. R마다 기록 하나, 지정 없는 기록 하나다. 좌표는 원본 기준이다. 참조 없는 기존 기록(`5021f72`)은 지정 없는 기록으로 읽는다.
- **흰칠**: `dialogue_extractor._whiten_custom_masks`. 말풍선 흰칠·텍스트 LaMa·보호 범위 복원이 모두 끝난 뒤, `cv2.imwrite(output_masked_path)` 직전에 한다. 페이지 좌표라 R이 바뀌어도 계속되고, 재사용한 이전 결과 위에도 다시 칠한다.
- **OCR 반영**: `_attach_custom_masks_to_ocr`. `regions.json` 영역에 영역 참조를 붙인 직후, indexed sheet를 만들기 전에 한다. 붙을 영역의 crop을 칠한 범위까지 넓히고, 칠한 픽셀은 원본 이미지에서 가져온다. 경계 기반은 마스크 밖이 흰 crop이라 칠한 곳만 새로 보인다. 넓어진 crop의 원점은 `ocr_crop_rect`로 남기고, 모든 영역의 `sheet_rect`를 다시 계산한다. `bubble_rect`·`unboxed_rect`·마스크·윤곽은 바꾸지 않아 글자 배치와 영역 키가 그대로다. `font_style_analyzer.py`는 `ocr_crop_rect`를 시트 crop 원점으로 쓴다. `*_flood_regions.json`은 CLI 파이프라인 산출물이라 편집기 경로에서는 쓰지 않는다.
- **재분석 참조**: `region_refs.resolve_instance_record`와 POLICY `custom_mask`(정확 일치만 keep, 합침·축소·분할·geo·짝 없음은 dormant). 휴면이면 OCR 반영만 쉬고 흰칠은 계속한다. 인스턴스 참조가 약하면(geo·충돌 접미사) `target_key`로 찾는다. 옮겨 붙이려면 POLICY 값만 `move`로 바꾸면 된다.
- **결과 기록**: 영역마다 `ocr_crop_rect`·`custom_mask_ocr = {mask_ids, shapes, pixels}`, `regions.json` 최상위 `custom_masks_applied = {mask_ids, strokes, rects, polygons, pixels, ocr_regions, ocr_shapes, not_in_ocr}`.
- **캐시**: 보정이 바뀌면 이전 지우기 결과를 재사용하지 않는다(`guidance_allows_unboxed_mask_reuse`의 `stable_keys`).
- **편집기**: 도구 `M`(선택한 R·TG로 확대), 붓 `[` `]`(2-64px), 클릭 다각형(첫 점·Enter로 닫기, Esc 취소), Shift+드래그 사각형. R 지정 기록은 자홍, 자동 배정 기록은 주황으로 그린다. 분석 뒤 OCR crop이 넓어진 영역은 자홍 점선 상자로 보여 준다. 기록 목록은 "R n 지우기 보정"(휴면이면 "영역 없음: 흰칠만")과 "지우기 보정(자동 배정)".
- **남은 일**: 실제 데이터로 OCR 시트 전후 확인(1-3건), 텍스트 기반 자동 마스크를 겹쳐 보기, 돋보기.

아래 §1-§6은 처음 계획이다. §0과 다르면 §0을 따른다.

## 1. 목적

자동 분석이 만든 지우기 마스크가 일부 글자(루비, 떨어진 획 등)를 놓칠 때, 사용자가 그 부분만 칠해 **기존 마스크에 합쳐서** 지운다.
핵심 사용 방식은 텍스트 기반(unboxed) 영역이다. 마스크를 일부러 타이트하게 잡고 LaMa로 지우되, 놓친 곳만 수동으로 추가해 품질을 올린다.
경계 기반 flood fill 영역은 이번 범위가 아니다(사용자 판단: 확장 가치 낮음). 단, 저장 구조는 나중에 확장 가능하게 둔다.

## 2. 현재 구조 (확인한 사실)

- 텍스트 기반 지우기는 `pipeline/dialogue_extractor.py`의 `build_dialogue_sheet`에서 `safe_text_mask`(글자 획 마스크)를 만들고,
  엔진이 LaMa(기본) 또는 Telea일 때 그 마스크 위치만 `masked_image`에 덮어쓴다(약 3296-3330행).
  → **커스텀 마스크는 `safe_text_mask`에 OR로 합치는 한 지점**이면 된다.
- 수동 ubox 마스크는 `_manual_ubox_text_mask`가 만든다(`dark_threshold`, `dilate_px` 등으로 타이트함 조절 가능).
- 이전 지우기 결과 재사용(`reused_unboxed_inpaint`)이 있다. **재사용 키에 커스텀 마스크가 들어가야** 한다. 안 그러면 보정이 반영되지 않는다.
- 영역 제외·엔진 고정은 `guidance`의 `suppressed_regions`, `inpaint_engine_overrides`에 저장되고, 처리 인스턴스 참조(`region_instance_ref`)로 영역에 붙는다.
  (`app.py` 2084-2157행, `frontend/src/App.jsx` 2633-2680행, `pipeline/region_refs.py`)
- 영역 참조 규칙은 `pipeline/region_refs.py` 모듈 설명과 POLICY 표가 기준이다.

## 3. 저장 스키마 (안)

`guidance.custom_masks` (목록). 항목은 다음과 같다.

```json
{
  "id": "cm_<짧은 해시>",
  "region_instance_ref": "auto|tg:3",
  "mode": "add",
  "strokes": [{"points": [[x,y],...], "width": 12}],
  "rects": [[x,y,w,h]],
  "display_region_id": 5
}
```

- 좌표는 원본 이미지 좌표. 획(strokes)은 폴리라인+굵기, 사각형(rects)은 단순 추가용. PNG를 쓰지 않아 JSON만으로 diff·되돌리기가 쉽다.
- `mode`는 처음엔 `add`만 구현한다. `subtract`(마스크에서 빼기)는 스키마에 자리만 두고 구현하지 않는다.
- 참조는 `region_instance_ref`(처리 인스턴스 참조)를 쓴다. 지우기 대상이 "어떤 처리 인스턴스"인가에 걸리기 때문이다. 엔진 고정과 같은 방식이다.
- 영역을 못 찾으면 다른 번호로 옮기지 않고 **휴면**(저장은 유지, 어디에도 안 붙음)으로 둔다. `region_refs.py` POLICY 표에 소비처 `custom_mask`를 추가한다.
  합침/분할/축소 시 동작은 엔진 고정의 정책과 같게 시작하고, 표 한 곳에서 바꿀 수 있게 한다.

## 4. 단계 분할 (각 단계가 끝나도 테스트 통과 + 앱 동작)

### 단계 1. 설계 확인 (읽기 전용, Opus)
- `region_refs.py` POLICY 표에 `custom_mask` 소비처를 어떻게 넣을지, 휴면/이동 정책, `_anchor` 등 기존 보정과 충돌 없는지 점검.
- 스키마 확정, 재사용 키 영향 범위 확인, 영향 받는 소비처 전수 목록(파일:줄) 작성.
- 산출물: 위 항목이 확정된 설계 메모. 코드 수정 없음.

### 단계 2. 백엔드 합성 + 저장 정규화 (Sonnet 가능)
- `app.py`의 guidance 정규화/stable_keys에 `custom_masks` 추가(`suppressed_regions`와 같은 방식).
- `dialogue_extractor.py`에서 텍스트 기반 영역의 `safe_text_mask`에 커스텀 마스크를 OR로 합성. 이미지 범위·영역 rect 밖은 잘라낸다(영역 rect 안으로 한정할지는 단계 1에서 확정).
- 재사용 키에 커스텀 마스크 해시 포함. 결과 `regions.json`에 `custom_mask_applied`(획 수, 추가 픽셀 수) 기록.
- 테스트: 합성 결과(추가 픽셀 포함), 영역 밖 무시, 재분석 시 유지, 재사용 키 무효화, LaMa 호출은 모킹(`_get_lama`).

### 단계 3. region_ref 연동 (Opus 권장)
- 번호가 바뀌는 재분석(합침·분할·삭제) 뒤에도 보정이 제 영역에 붙는지. 못 찾으면 휴면, 돌아오면 다시 붙음.
- 테스트: `tests/`의 region_ref 회귀 테스트 패턴을 따라 번호가 바뀌는 시나리오 추가.

### 단계 4. 편집기 UI (Sonnet 가능)
- 영역 분석 탭에 "지우기 보정" 도구: 브러시(굵기 조절)와 사각형, 선택한 텍스트 기반 영역에만 적용.
- 보정은 반투명 색으로 겹쳐 표시, 되돌리기/삭제, 목록(`App.jsx`의 guidance 항목 목록)에 항목 추가.
- 칠한 뒤 `변경사항 적용`으로 반영. 기존 `guidance` 저장/되돌리기 흐름을 그대로 쓴다.
- `web/app.bundle.js` 재빌드 후 커밋.

### 단계 5. 실제 데이터 검증 + 문서
- 맥미니 3권의 루비 잔여 페이지(예: 44페이지)로 전후 비교. **1-3건**만 확인한다.
- `docs/editor_manual.html`, `CODEBASE_FILE_MAP.md`, `REGION_PROCESSING_BASIS_CHANGELOG.md`에 사용법·스키마 추가. 영향 없는 문서는 끝에 `변경 검토` 주석.

### 단계 6. 최종 적대적 리뷰 (Opus, 읽기 전용)
- 확인할 것: 재사용 키 누락으로 보정이 안 먹는 경우, 번호가 바뀐 뒤 엉뚱한 영역에 붙는 경우, 영역 삭제 후 보정 잔존, 좌표계(크롭/원본) 혼동, 되돌리기 경로.

## 5. 위험과 주의

- **재사용 캐시 무효화 누락**이 가장 큰 위험이다. 단계 2 테스트에 반드시 포함.
- 좌표는 원본 기준으로만 저장한다. 크롭/ROI 좌표를 저장하면 영역 크기 변경 때 어긋난다.
- 추가 마스크가 글자 아닌 그림(얼굴, 배경 선)에 걸리면 LaMa가 그림을 지운다. 영역 rect 안으로 자르는 것을 기본으로 하고, 단계 1에서 확정한다.
- 사용자가 영역을 지우면(suppress) 그 영역의 보정도 함께 휴면 처리한다.
- 유료 분석 방지: 검증 중 앱 서버/미리보기를 새로 띄우지 않는다. 실제 분석은 사용자가 수동으로 실행한다.

## 6. 작업 규칙 (이 저장소)

- 프로젝트 `CLAUDE.md`, `AGENTS.md`를 먼저 읽는다. 저장된 `bubble/unboxed`와 좌표 키를 전역 치환하지 않는다.
- 테스트: `. .venv/bin/activate && python -m pytest tests -q --deselect tests/test_render_text_overlay.py::RenderTextOverlayTest::test_explicit_translation_lines_are_preferred_over_height_filling_split`
- 코드와 문서를 같은 커밋에 넣는다(`tests/test_docs_sync.py`).
- 배포: 커밋 → push → 맥미니(`~/Developer/NEW_COMIC_TRANSLATOR`)에서 `git pull --ff-only`. 서버 재시작은 사용자가 한다.
