# FloodFill Masking Progress

## 현재 결론

OpenCV `findContours` 기반 말풍선 후보 탐색은 표준 경로에서 제외한다. 현재 가장 유효한 방향은 `Anchor Text Group` 중심점 주변의 흰 seed에서 `floodFill`을 수행해 말풍선 내부 영역을 잡는 방식이다.

표준 후보 흐름:

```text
Cloud Vision
  -> Clustering V5 / Anchor Text Group
  -> Anchor 중심점 기반 floodFill
  -> flood region merge
  -> flood 우선 masking / 위험 region union bbox fallback
  -> packed OCR sheet 생성
  -> Gemini OCR + 번역 + region_id 매칭
  -> flood mask 또는 fallback bbox 기준 overlay
```

## 핵심 개념

- `Anchor Text Group`: `cv_clusters_v5`의 loose cluster를 seed 후보로 재해석한 것. 실제 마스킹 영역이 아니라 위치 힌트다.
- `Flood Region`: Anchor 중심 주변의 흰 픽셀에서 시작한 floodFill 연결 영역. 여러 Anchor가 같은 말풍선이면 같은 flood region을 공유할 수 있다.
- `Inner Flood Mask`: flood region hole fill 후 3px erode한 mask. 실제 white masking에 우선 사용한다.
- `Packed OCR Sheet`: flood/fallback region crop을 한 이미지에 차곡차곡 붙인 Gemini OCR 입력 이미지다.
- `Union BBox Fallback`: flood가 새거나 너무 작으면 Anchor `bounding_rect`를 그대로 마스킹/OCR crop에 사용한다.

## 구현 파일

- `pipeline/floodfill_anchor_probe.py`
  - `visualize_anchor_floodfill()`
  - `apply_floodfill_white_mask()`
  - `create_packed_ocr_sheet()`
  - `create_floodfill_fallback_outputs()`
- `visualize_debug.py`
  - `visualize_anchor_text_groups()` 추가됨

## 현재 안전장치

`create_floodfill_fallback_outputs()`는 region마다 `method`, `fallback_reason`, `quality_flags`를 기록한다.

현재 fallback 조건:

```text
flood_area_ratio > 0.12          -> fallback
touches_image_border            -> fallback
flood_rect / anchor_bbox < 1.0   -> fallback
flood_rect / anchor_bbox > 12.0  -> fallback
```

`quality_flags` 예시:

```text
unsafe_flood_rect_anchor_ratio
touches_image_border
excessive_flood_area
unsafe_region
```

## OCR Sheet Index 개선

00028에서 좌상단 텍스트가 있는 crop에 `R1` 라벨이 원문을 침범했다.

현재 수정:

- clean OCR sheet에는 라벨을 넣지 않는다.
- indexed OCR sheet는 crop 내부가 아니라 상단 `indexed_label_gutter` 영역에 라벨을 그린다.
- JSON에는 clean용 `sheet_rect`와 indexed용 `indexed_sheet_rect`를 구분해 기록한다.

## 검증 산출물

공통 산출물 패턴:

```text
temp_output/Debug/{번호}_anchor_floodfill_eroded.jpg
temp_output/Debug/{번호}_floodfill_fallback_masked.jpg
temp_output/Debug/{번호}_floodfill_fallback_regions_only.jpg
temp_output/Debug/{번호}_floodfill_fallback_packed_ocr_sheet.jpg
temp_output/Debug/{번호}_floodfill_fallback_packed_ocr_sheet_indexed.jpg
temp_output/Debug/{번호}_floodfill_fallback.json
temp_output/Debug/{번호}_floodfill_validation_contact.jpg
```

요약:

| 이미지 | regions | flood | fallback | 비고 |
|---|---:|---:|---:|---|
| 00021 | 8 | 6 | 2 | rectangular 말풍선 일부 fallback |
| 00027 | 6 | 6 | 0 | 특수/투명 말풍선 flood 통과 |
| 00028 | 6 | 4 | 2 | 말풍선 없는 텍스트 일부 union bbox fallback |
| 00033 | 22 | 8 | 14 | 흰 배경 대사 케이스 대부분 fallback |
| 00037 | 10 | 4 | 6 | 배경 누수 다수 fallback |
| 00038 | 17 | 6 | 11 | 큰 배경 flood 누수 다수 fallback |

00037/00038은 기존 cluster JSON이 없어 새로 생성했다.

```text
temp_output/Debug/00037_cv.json
temp_output/Debug/00037_cv_clusters_v5.json
temp_output/Debug/00038_cv.json
temp_output/Debug/00038_cv_clusters_v5.json
```

## Gemini OCR 검증

00019 packed indexed OCR sheet를 `gemini-3.1-flash-lite-preview`로 OCR/번역했다.

입력:

```text
temp_output/Debug/00019_floodfill_packed_ocr_sheet_indexed.jpg
```

출력:

```text
temp_output/Debug/00019_packed_ocr_gemini_flash_lite.json
```

결과는 region id 매칭, OCR, 번역 모두 매우 양호했다. 라벨 `R1`, `R2`는 OCR 본문에 섞이지 않았다.

## 남은 리스크

- 00033 같은 흰 배경 대사 페이지는 flood가 배경 전체로 새기 쉽다. 현재 ratio fallback이 필요하다.
- 00037/00038은 배경 텍스처, 큰 말풍선, 작은 텍스트가 섞여 fallback 비율이 높다.
- union bbox fallback은 정확한 말풍선 형태가 아니라 사각형 마스킹이라, overlay 배치 품질은 별도 검증이 필요하다.
- packed OCR sheet가 너무 넓어지는 경우가 있다. 다음 단계에서 row packing이 필요하다.
- OCR indexed sheet 라벨은 상단 gutter로 이동했지만, 실전 OCR에는 clean sheet를 기본으로 쓰고 metadata로 region id를 매칭하는 편이 더 안전하다.

## 다음 작업

1. `pipeline/floodfill_anchor_probe.py`를 정식 모듈 `pipeline/floodfill_masking.py`로 승격한다.
2. `create_floodfill_fallback_outputs()`를 표준 masking 단계에 연결한다.
3. packed OCR sheet를 Gemini 입력으로 사용하고, `region_id` 기반 JSON 응답을 integrated JSON으로 변환한다.
4. overlay는 `method=flood`이면 flood mask/rect 기준, `method=union_bbox_fallback`이면 union bbox 기준으로 분기한다.
5. packed OCR sheet는 현재 1-row append 방식이므로, 최대 width 기준 row packing을 추가한다.
6. fallback region에는 평탄화/inpaint/blur 등 배경 정리 옵션을 실험한다.

