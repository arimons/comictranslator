# FloodFill 서브시스템 레퍼런스

최종 갱신: 2026-05-16 (코드 직접 반영)

연결 상태·이슈 추적은 [PIPELINE_STATUS.md](PIPELINE_STATUS.md) 참고.
파일/함수 역할 개요는 [CODEBASE_FILE_MAP.md](CODEBASE_FILE_MAP.md) 참고.

---

## 현재 결론

Anchor Text Group(ATG) 중심점 주변의 흰 픽셀을 seed로 `floodFill`을 수행해 말풍선 내부 영역을 검출한다. OpenCV `findContours` 기반 윤곽 탐색은 표준 경로에서 제외.

표준 흐름:

```text
Cloud Vision (3x3 grid, languageHints=["ja","en"])
  → Clustering V5 / Anchor Text Group
  → ATG 중심 주변 흰 픽셀 seed로 floodFill
  → pre-filter / region 판정 / rescue
  → split (inflection line → erosion CC)
  → flood_pre_masked.jpg + packed OCR sheet 생성
  → Gemini OCR + 번역
  → flood mask 또는 fallback bbox 기준 overlay
```

---

## 핵심 개념

| 용어 | 의미 |
|---|---|
| **ATG (Anchor Text Group)** | `cv_clusters_v5`의 cluster 하나. flood fill seed 출발점. |
| **Flood Region** | ATG anchor 주변 흰 픽셀에서 시작한 floodFill 연결 영역. 여러 ATG가 같은 말풍선이면 동일 flood_rect를 공유할 수 있다. |
| **Inner Flood Mask** | flood region hole fill 후 3px erode한 mask. white masking에 사용. |
| **flood_rect** | flood region의 최소 외접 직사각형 `[x, y, w, h]`. |
| **flood_rect_anchor_ratio** | `flood_rect 면적 / anchor bbox 면적`. 너무 작으면 획 사이 틈새, 너무 크면 배경 누수. |
| **Packed OCR Sheet** | flood region crop을 한 이미지에 가로로 이어 붙인 Gemini OCR 입력. |
| **union_bbox_fallback** | flood 실패 시 anchor bounding_rect를 그대로 사용하는 경로. **현재 `create_floodfill_fallback_outputs`에서는 비활성화** — diagnostics 기록만 하고 OCR sheet 제외. |

---

## 구현 파일

### `pipeline/floodfill_anchor_probe.py`

**시드 탐색:**
- `_find_side_white_seed(gray, rect)`: 세로쓰기 anchor 좌우 바깥 여백에서 흰 시드 우선 탐색
- `_find_white_seed(gray, center, rect)`: 중심에서 가장 가까운 흰 픽셀 탐색
- `_flood_with_fallback(image, gray, center, rect)`: 3단계 복합 탐색 — ① 좌우 바깥(`_find_side_white_seed`) → ② 중심 근처(`_find_white_seed`) → ③ bbox 가장자리 4방향 안쪽 10px. flood area < 500px이면 다음 단계로.

**판정/분류:**
- `_is_dark_bg_text(gray, rect, threshold=120)`: bbox crop grayscale 평균 < threshold이면 True
- `_is_symbol_only_text(text)`: 일본어·영문·숫자 없이 기호만 있으면 True
- `_is_short_non_japanese_noise_text(text)`: 일본어 없이 5자 이하 영문/숫자/기호면 True
- `_is_bubble_shaped(mask, min_solidity)`: flood inner mask solidity로 말풍선 형태 판별
- `_compute_flood_bimodality(mask, gray)`: flood mask 내부 픽셀 흑/백 bimodality 반환 (0~1). 높을수록 말풍선 배경 + 검은 글자 이중분포. 낮으면 flood가 글자 획 사이로 파고든 것.
- `_classify_anchor_region(cluster, flood_rect, inner_area, touches_border, safe)`: region_type + quality_flags 반환

**핵심 함수:**
- `_auto_split_flood_mask(mask, anchor_ids, anchor_centers)`: 단일/다중 anchor flood region에 inflection split 시도. CV 미인식 버블도 anchor 없는 sub-region으로 OCR sheet 포함.
- `visualize_anchor_floodfill(...)`: flood 결과 + region_type 분류 시각화 → `flood_vis.json`, `flood_debug.jpg` (Step 3-A)
- `create_floodfill_fallback_outputs(...)`: flood 판정 + OCR sheet + 마스킹 통합 생성 → `flood_pre_masked.jpg`, `flood_sheet.jpg`, `flood_regions.json` (Step 3-B)
- `apply_floodfill_white_mask(...)`: flood mask 기반 흰색 마스킹 (단독 경로, 현재 표준 미사용)
- `create_packed_ocr_sheet(...)`: flood region crop만 모아 OCR sheet 생성 (단독 레거시 경로)

### `pipeline/flood_split.py`

- `collect_inflection_candidates(mask, anchor_centers, min_defect_depth, anchor_rects)`: convexity defect pair 전체 평가. `anchor_rects` 지정 시 split line이 anchor center로부터 `min(bbox_w, bbox_h) × 0.25` 이상 떨어져야 `bboxes_clear=True`.
- `split_mask_by_inflection_line(mask, anchor_ids, anchor_centers, anchor_rects)`: 최고점 후보를 선택해 mask 분할. 각 sub-mask 안에 해당 ATG center가 실제 포함되어야 수락.
- `split_mask_by_erosion_components(mask, anchor_ids, anchor_centers)`: 점진적 erosion으로 CC 분리 후 Voronoi 할당. `all_anchor_rects_are_disjoint` 통과 시에만 호출.
- `all_anchor_rects_are_disjoint(anchor_rects)`: erosion split 사전 조건 검사 — bbox 겹치면 Voronoi 오배정 위험.
- `_line_segment_intersects_rect(p1, p2, rect)`: Liang-Barsky 선분-직사각형 교차 판단.

---

## Region Type 분류

`flood_vis.json` 각 result의 `region_type_guess` 필드.

| region_type | 의미 | 시각화 |
|---|---|---|
| `in_bubble` | flood 성공, 닫힌 말풍선 | flood mask 색칠 |
| `narration_like` | panel 직접 텍스트·독백·나레이션 (jp_chars ≥ 4 AND (touches_border OR ratio ≥ 8)) | anchor bbox 보라색 |
| `noise_like` | 손글씨·SFX·장식·오인식 (symbol_heavy / empty_inner / low_solidity) | 회색 박스 |
| `dark_bg_text` | 검은 배경 흰 글자 (bbox 평균 < 120) | 어두운 박스 |
| `unsafe_flood` | 위 분류 미확정, flood 불안전 | — |
| `symbol_only` | seed 없음 + 기호만 (visualize 전용) | — |

> `visualize_anchor_floodfill`과 `create_floodfill_fallback_outputs`의 `safe` 판정 기준이 일부 불일치. `visualize_anchor_floodfill`은 `not touches_border`를 safe 조건에 포함하지만 `create_floodfill_fallback_outputs`는 touches_border를 quality_flags에만 기록하고 수락 조건에서 제외. (미해결 이슈 — PIPELINE_STATUS.md 참고)

---

## 필터 체계 (`create_floodfill_fallback_outputs`)

### Phase 1 — Pre-filter (flood 시도 전)

| 조건 | 기준 | 결과 |
|---|---|---|
| `dark_bg_text` | bbox grayscale 평균 < 120 | `dark_bg_skipped` — 완전 제외 |
| `symbol_only_text` | 일본어·영문·숫자 없이 기호만 | `symbol_only_skipped` |
| `short_non_japanese_noise_text` | 일본어 없이 5자 이하 | `symbol_only_skipped` |

### Phase 2 — Flood 수락 조건

수락: `flood_area_ratio ≤ 0.12` AND `1.0 ≤ flood_rect_anchor_ratio ≤ 12.0` AND `center_inside_flood_rect`

수락 후 추가 필터:
- text_noise 신호(`symbol_heavy_low_japanese_text` 또는 `oversized_symbol_heavy_flood`) AND `solidity < 0.85` → `noise_like_skip` (`flood_not_bubble_shaped`)
- `bimodality < 0.85` → `noise_like_skip` (`low_bimodal_flood`)

### Phase 3 — Ratio 초과 구제

| 조건 | 기준 | 결과 |
|---|---|---|
| ratio > 12 AND area ≤ 0.12 AND center_inside AND solidity ≥ 0.65 AND bimodality ≥ 0.85 | — | `high_ratio_bubble_rescued` → method=flood |
| 나머지 ratio 초과 | — | `unsafe_flood_rect_anchor_ratio` — `cluster_actual_flood_rect` 기록 |

### Phase 4 — 기타 실패

| 조건 | 결과 |
|---|---|
| `flood_rect_anchor_ratio < 1.0` | `small_ratio_skip` |
| `flood_area_ratio > 0.12` | `unsafe_region` |
| center가 flood_rect 밖 | `anchor_center_outside_flood_rect` |
| union_bbox_fallback 경로 | **비활성화** — diagnostics 기록 후 OCR sheet 제외 |

### Phase 5 — Post-loop Rescue (전체 loop 완료 후)

**cross-split**: `cluster_actual_flood_rect`에 기록된 ratio 초과 anchor가 flood 성공 anchor와 동일 flood_rect 공유 → 해당 flood group에 합류.

**combined_anchor_rescue**: ratio 초과 anchor 2개 이상이 동일 flood_rect 공유 AND 합산 anchor 면적 기준 ratio 통과 AND `bimodality ≥ 0.85` → 새 flood group으로 승격. `fallback_reason="combined_anchor_rescue"`.

### 판정 기준 수치 요약

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `max_area_ratio` | 0.12 | flood 면적 / 이미지 면적 상한 |
| `min_flood_rect_anchor_ratio` | 1.0 | flood_rect 면적 / anchor 면적 하한 |
| `max_flood_rect_anchor_ratio` | 12.0 | flood_rect 면적 / anchor 면적 상한 |
| `dark_bg_mean_threshold` | 120 | bbox grayscale 평균 dark_bg 판정 경계 |
| `erode_pixels` | 3 | inner mask erosion 픽셀 수 |
| `MIN_BUBBLE_AREA` | 500 | flood area 이하이면 시드 retry |
| `MIN_SAFE_INNER_AREA` | 300 | inner area 이하이면 unsafe |
| bimodality threshold | 0.85 | 미만이면 획 파고들기 판정 |
| solidity threshold (noise_like) | 0.85 | 미만이면 버블 형태 아님 |
| solidity threshold (high_ratio rescue) | 0.65 | 이상이면 구제 가능 |

---

## Split 로직

2개 이상의 anchor가 동일 flood region 공유 시:

```
1차: split_mask_by_inflection_line (anchor_rects proximity 25% guard)
  → 성공: sub-region별 독립 OCR crop
  ↓ 실패
2차: split_mask_by_erosion_components
  → 조건: all_anchor_rects_are_disjoint == True (bbox 겹침 없을 때만)
  → 성공: erosion sub-region별 독립 OCR crop
  ↓ 실패
하나의 번역 단위로 유지 (quality_flags: "shared_flood_no_inflection_merge")
```

inflection line 선택 기준 (`collect_inflection_candidates`):
- `separates`: ATG center가 line 양쪽에 분리
- `centers_inside`: 각 sub-mask 안에 해당 ATG center 포함
- `bboxes_clear`: split line이 anchor center로부터 `min(bbox_w, bbox_h) × 0.25` 이상 이격
- `min_area_ratio ≥ 0.10`: 작은 쪽 sub-region이 전체의 10% 이상
- 위 조건 모두 통과한 후보 중 score 최대 선택

score = separation − cut_cost × 0.55 + (depth1 + depth2) × 0.01 + area_balance × 0.8

---

## 반환 JSON 구조

### `flood_vis.json` (`visualize_anchor_floodfill`)

```json
{
  "image": "...",
  "clusters": "..._cv_clusters_v5.json",
  "results": [
    {
      "cluster_id": 1,
      "anchor_bbox": [x, y, w, h],
      "anchor_center": [cx, cy],
      "seed": [sx, sy],
      "flood_area": 12345,
      "flood_area_ratio": 0.023,
      "flood_rect": [x, y, w, h],
      "inner_area": 11000,
      "inner_area_ratio": 0.021,
      "erode_pixels": 3,
      "touches_image_border": false,
      "safe_region": true,
      "region_type_guess": "in_bubble",
      "quality_flags": [],
      "flood_rect_anchor_ratio": 3.4
    }
  ]
}
```

### `flood_regions.json` (`create_floodfill_fallback_outputs`)

```json
{
  "summary": {
    "total_anchors": N,
    "dark_bg_skipped": N,
    "noise_like_skipped": N,
    "symbol_only_skipped": N,
    "flood": N,
    "flood_fail_small_ratio": N
  },
  "dark_bg_skipped": [...],
  "symbol_only_skipped": [...],
  "noise_like_skipped": [...],
  "diagnostics": [
    {
      "cluster_id": 1,
      "method": "flood",
      "fallback_reason": "",
      "anchor_bbox": [x, y, w, h],
      "seed": [sx, sy],
      "flood_rect": [x, y, w, h],
      "flood_area_ratio": 0.023,
      "inner_area_ratio": 0.021,
      "flood_rect_anchor_ratio": 3.4,
      "touches_image_border": false,
      "quality_flags": []
    }
  ],
  "regions": [
    {
      "region_id": 1,
      "anchor_ids": [1],
      "method": "flood",
      "fallback_reason": "",
      "original_rect": [x, y, w, h],
      "sheet_rect": [sx, 0, w, h],
      "indexed_sheet_rect": [sx, gutter, w, h],
      "seed_points": [[sx, sy]],
      "flood_area_ratio": 0.023,
      "inner_area_ratio": 0.021,
      "flood_rect_anchor_ratio": 3.4,
      "touches_image_border": false,
      "quality_flags": [],
      "safe_region": true
    }
  ]
}
```

`method` 값: `"flood"` / `"union_bbox_fallback_disabled_skip"` / `"noise_like_skip"` / `"symbol_only_skip"` / `"short_non_japanese_noise_skip"`

`fallback_reason` 값: `""` (정상) / `"high_ratio_bubble_rescued"` / `"combined_anchor_rescue"` / `"unsafe_flood_rect_anchor_ratio"` / `"unsafe_region"` / `"anchor_center_outside_flood_rect"` / `"flood_not_bubble_shaped"` / `"low_bimodal_flood"` / `"no_seed"`

### OCR Sheet 구조

- **clean sheet** (`flood_sheet.jpg`): 라벨 없음, OCR 순도용
- **indexed sheet** (`flood_indexed_sheet.jpg`): 상단 `indexed_label_gutter`(48px) 영역에 `R{N}` 라벨. crop 내부는 침범하지 않음.
- `sheet_rect`: clean sheet 기준 `[sheet_x, 0, w, h]`
- `indexed_sheet_rect`: indexed sheet 기준 `[sheet_x, gutter, w, h]`

---

## 검증 산출물

```text
proof/Debug/
  {번호}_anchor_floodfill_eroded.jpg      flood_vis 시각화
  {번호}_floodfill_fallback_masked.jpg    flood 마스킹 결과
  {번호}_floodfill_fallback_regions_only.jpg
  {번호}_floodfill_fallback_packed_ocr_sheet.jpg
  {번호}_floodfill_fallback_packed_ocr_sheet_indexed.jpg
  {번호}_floodfill_fallback.json          flood_regions.json
  {번호}_floodfill_validation_contact.jpg

work_dir/ (표준 파이프라인)
  {name}_flood_vis.json        Step 3-A 출력 (overlay bbox 선택 + 난이도)
  {name}_flood_debug.jpg
  {name}_flood_pre_masked.jpg  Step 3-B 출력 (Step 5 입력)
  {name}_flood_sheet.jpg
  {name}_flood_regions.json
  {name}_flood_indexed_sheet.jpg
```
