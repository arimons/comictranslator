# Pipeline Status

최종 갱신: 2026-05-16 (floodfill 코드 전체 반영)

파이프라인 각 단계의 현재 연결 상태와 다음 작업을 추적한다.
파일/함수 역할 설명은 [CODEBASE_FILE_MAP.md](CODEBASE_FILE_MAP.md),
FloodFill 상세는 [FLOODFILL_MASKING_PROGRESS.md](FLOODFILL_MASKING_PROGRESS.md) 참고.
고난도 CV 사례 레지스트리는 [cases/README.md](cases/README.md)에서 추적한다.

---

## 파이프라인 단계별 상태

| 단계 | 파일 | 상태 |
|---|---|---|
| Cloud Vision OCR | `pipeline/cloud_vision_api.py` | ✅ 표준 경로 연결됨 |
| Text Group 병합 | `pipeline/cluster_cv_v5.py` | ✅ 표준 경로 연결됨 |
| FloodFill 분류 | `pipeline/floodfill_anchor_probe.py` | ✅ 표준 경로 연결됨 (Step 3, 소프트 — 실패해도 계속) |
| Gemini OCR+번역 | `pipeline/gemini_ocr_translate_v5.py` | ✅ 표준 경로 연결됨 |
| White Masking | `pipeline/apply_masking.py` | ✅ flood mask 승격 연결됨 (no-text 영역 원본 픽셀 복원 포함) |
| Text Overlay | `pipeline/render_text_overlay.py` | ✅ DP 엔진 + flood_vis.json 연동 완료 |
| 실행 엔진 | `pipeline/pipeline_runner.py` | ✅ 6단계 subprocess 통일, 캐시·배치 지원 |
| Gradio UI | `main.py` | ✅ pipeline_runner 기반으로 재작성 완료 |

---

## proof 파이프라인 테스트 현황

| 범위 | 상태 | 비고 |
|---|---|---|
| 00002~00028 | ✅ 완료 | proof/NNNNN_merge.jpg 생성 |
| 00002, 00004, 00011, 00024, 00025 | ⚪ 0 cluster | dark_bg/효과음 페이지 — 정상 |
| 00032 | ✅ 별도 완료 | 개발 기준 이미지로 사용 중 |
| 00001, 00029~00040 (00032 제외) | ❌ 미처리 | proof 파이프라인 미실행 |

---

## 최근 구현 완료 (2026-05-16)

### FloodFill 구제 로직 강화 (`floodfill_anchor_probe.py`)

1. **touches_border 제외 조건 삭제** — `create_floodfill_fallback_outputs`의 flood 수락 조건에서 `not touches_border` 제거. 패널 경계 근처 말풍선이 잘못 탈락하던 문제 해결 (00079 A1+A2). touches_border는 quality_flags 기록용으로만 남음.

2. **combined_anchor_rescue** — 2개 이상의 소형 ATG가 동일 flood_rect를 공유하고 각각 개별 ratio 통과 실패 시, 합산 anchor 면적으로 ratio를 재계산하여 flood group으로 승격. `fallback_reason="combined_anchor_rescue"` 태그 부여 (00081 A4+A5).

3. **high_ratio_bubble_rescued** — flood_rect_anchor_ratio 초과이지만 flood_area_ratio 안전 + solidity ≥ 0.65 + bimodality ≥ 0.85이면 유효 말풍선으로 구제. `fallback_reason="high_ratio_bubble_rescued"` 태그 부여 (00088 A14, 00106 A2).

4. **cross-split** — ratio 초과로 fallback 판정된 anchor가 flood 성공 anchor와 동일 `detected_flood_rect`를 공유하면 해당 flood group에 합류. anchor 단위 diagnostics에서도 수정 반영.

5. **bimodality 필터** (`_compute_flood_bimodality`) — flood mask 내부 픽셀의 흑/백 이중분포(bimodality) 측정. threshold 0.85 미만이면 글자 획 사이로 flood가 파고든 것으로 판단 → `low_bimodal_flood` skip. `high_ratio_bubble_rescued` 조건에도 bimodality ≥ 0.85 포함.

6. **`union_bbox_fallback` 비활성화** — `create_floodfill_fallback_outputs`에서 flood 실패 anchor는 diagnostics 기록 후 OCR sheet에서 제외(`continue`). 이전에는 bbox crop을 sheet에 포함했으나 말풍선 아닌 영역이 Gemini에 전달되는 문제로 제거.

7. **pre-skip 카테고리** — flood 시도 전 조기 제외:
   - `symbol_only_text`: 일본어·영문·숫자 없이 기호만 있는 ATG
   - `short_non_japanese_noise_text`: 일본어 없이 5자 이하 짧은 영문/숫자/기호

8. **`_auto_split_flood_mask`** — 단일 anchor 포함 모든 flood region에 inflection split 시도. CV가 놓친 말풍선도 Gemini가 살릴 수 있도록 anchor 없는 sub-region도 OCR sheet 포함 대상. `create_packed_ocr_sheet`에서 사용.

9. **`_flood_with_fallback` 3단계 시드 탐색** — ① 좌우 바깥 여백(`_find_side_white_seed`) → ② 중심 근처 최근접 흰 픽셀(`_find_white_seed`) → ③ bbox 가장자리 4방향 안쪽 10px. 글자 획 안 고립 픽셀에 seed가 잡히던 문제 개선.

10. **`split_mask_by_inflection_line` bbox proximity 검사** — split line이 anchor center로부터 `min(bbox_w, bbox_h) * 0.25` 이상 떨어져야 유효 (`bboxes_clear` 플래그). 모서리 스침 오탐 방지.

### Gemini OCR 재시도 강화 (`gemini_ocr_translate_v5.py`)

`_needs_retry()` 함수 도입 — 다음 중 하나라도 해당하면 재시도 (temperature=0.1):
- 반환 region 수 < 예상 region 수
- 임의 region의 `text_translated`가 빈 문자열
- `ocr_text` 중복 (region_id 오할당 cascade shift 감지, 00107 케이스)

단일 이미지 OCR(`_process_single_ocr`)에서도 gemini_clusters 전체 누락 시 재시도 추가.

### White Masking flood 연결 (`apply_masking.py`, `pipeline_runner.py`)

`revert_no_text_flood_regions()` 함수 구현:
- `flood_pre_masked.jpg` 기반 마스킹 적용 후
- `integrated.json`에서 sentence가 없는 flood region(no-text)을 찾아 해당 `original_rect` 픽셀을 원본 이미지에서 복원
- pipeline_runner Step 5가 `shutil.copyfile` 대신 이 함수를 호출 (00082 A3 케이스).

---

## 미해결 이슈

### 진행 중

- [ ] **00107 text_overlay bbox 오배치** — `integrated.json` 번역 내용은 정상, render_text_overlay의 bbox 선택 로직에서 S11 A[9,11,14] narrow_wide_split_rect 처리 의심. 재현 후 원인 확인 필요.
- [ ] **00109 A1 번역 누락** — flood_area_ratio=0.1013 > MAX_SAFE_AREA_RATIO + touches_image_border=True로 진짜 flood leak 판정. 현재 로직으로 구제 불가 — 별도 검토 필요.

### FloodFill 잔여 이슈

- [ ] **00010 A4 flood leak** — anchor center가 flood_rect 밖에 있을 때 union_bbox_fallback으로 처리됨. 근본 원인 미해결.
- [ ] **00001, 00029~00031, 00033~00040 proof 파이프라인 미실행**

### 표준 경로

- [ ] `visualize_anchor_floodfill()`에 동일 필터 반영 — flood_vis 색칠 기준이 `create_floodfill_fallback_outputs()`와 일부 불일치.
- [ ] `proof/run_full_batch.py` Cloud Vision 호출을 `detect_text_grid`(9장 묶음)로 교체.
- [ ] 새 overlay 엔진 폰트 크기 전반 검토 — `page_font_band` 중앙값 기준, 전체적으로 너무 작다는 피드백 있음.

### 미해결 케이스

- [ ] **00032 고난도 이미지** — 손글씨 SFX가 CV에 일본어로 읽혀 텍스트 노이즈 신호 없음, img_gen fallback 후보.
- [ ] **narration_like fallback erase rect** — 단순 union bbox로는 말줄임표 등 작은 기호 누락 (00021 A10).
- [ ] **packed OCR sheet row packing** — 현재 1-row append 방식, 최대 width 초과 시 대응 필요.

---

## 백로그

### 1-ATG 복합 말풍선 shape-split (inpaint 층위)

**개요**: 하나의 ATG가 2개 타원이 결합된 복합 말풍선 전체에 걸쳐 있을 때(00106 A2 유사 케이스),
erosion split으로 CC 2개를 검출하고 ATG 없는 쪽에 **virtual anchor**를 생성해 별도 OCR sheet 영역으로 포함.

**구현 시 고려사항:**
- 잘못 쪼갠 split 영역 → Gemini가 빈 응답 반환으로 자연 안전망 형성 (허용 가능)
- virtual anchor의 cluster_id 할당 방식 미정
- erosion split 후 Voronoi 할당 로직은 이미 존재

**결정 보류** — 구현 복잡도 대비 실효 케이스 적음. inpaint 층위와 동시 검토.

### inpaint + alpha composite 기반 텍스트 교체

현재 흰 박스 마스킹 방식 대신, `cv2.inpaint`로 배경을 복원하고 투명 배경 위에 번역문을 alpha composite하는 흐름.

목표 흐름:
```
ROI 추출 (anchor bbox)
  → Gemini OCR (번역문 확보)
  → threshold로 글자 획만 마스크 생성
  → cv2.inpaint로 배경 복원
  → PIL RGBA로 한국어 텍스트 렌더링
  → alpha composite으로 복원된 배경 위에 합성
```

- [ ] `narration_like` 케이스 검증 — 00032 이미지로 inpaint 배경 복원 품질 확인
- [ ] `noise_like` (SFX/손글씨) 케이스 검증 — 00032 복잡한 배경에서 inpaint 한계 확인
- [ ] inpaint 결과 불충분한 케이스는 LaMa 인페인팅 모델과 비교
- [ ] 검증 통과 시 `apply_masking.py` 또는 신규 모듈로 표준 경로에 편입
