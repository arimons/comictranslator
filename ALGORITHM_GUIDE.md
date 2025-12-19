# Comic Translator - 알고리즘 가이드

## 📋 프로젝트 개요

만화 이미지의 일본어 텍스트를 한국어로 자동 번역하고 자연스럽게 합성하는 시스템.

**파이프라인:**
```
입력 이미지
  ↓
Cloud Vision API (텍스트 탐지)
  ↓
Cluster CV v5 (텍스트 그룹화)
  ↓
Integrate OCR v3 (OCR + 번역 통합)
  ↓
Apply Masking (마스킹 적용)
  ↓
Render Text Overlay (텍스트 렌더링)
  ↓
출력 이미지
```

---

## 🎯 Core Modules

### 1. Cloud Vision API (`cloud_vision_api.py`)
**역할:** 이미지에서 텍스트 영역 탐지

- Google Cloud Vision 사용
- 각 텍스트에 대해 bounding box 제공
- 출력: `cv_clusters_v5.json`

```json
[
  {
    "box_2d": [ymin, xmin, ymax, xmax],
    "label": "텍스트 내용"
  }
]
```

---

### 2. Gemini API (`gemini_api.py`, `gemini_ocr_translate_v5.py`)
**역할:** OCR + 번역 통합 처리

- Google Gemini 3 Pro API 사용
- 텍스트 OCR (Gemini로 재인식)
- 일본어 → 한국어 번역 (한 번의 API 호출)
- 출력: `ocr_v5.json` (OCR 결과)

```json
[
  {
    "sentence_id": 1,
    "text_original": "원본 일본어",
    "text_translated": "번역된 한국어",
    "cv_cluster_list": [1, 2, 3]
  }
]
```

---

### 3. Cluster CV v5 (`cluster_cv_v5.py`)
**역할:** 텍스트 블록을 논리적 단위로 그룹화

#### 알고리즘: 2-Pass BFS Clustering

**Pass 1: Block → Line (Y축 확장)**
- CV에서 감지된 개별 텍스트 블록을 "행(Line)"으로 통합
- 같은 X 영역에서 세로(Y축)로 인접한 블록 병합
- 방법: BFS (Breadth-First Search)
- 목표: 수직 정렬된 단어들을 한 줄로 인식

```
Stage 1 조건: Y축 오버랩 + X 근접성
├─ Y_overlap_ratio > 0.5
└─ X_distance < avg_width * 1.5
```

**Pass 2: Line → Cluster (4-Stage Rule)**
- 라인들을 더 큰 단위(클러스터)로 그룹화
- 4단계 규칙으로 점진적 확대:

```
Stage 1: 강한 연결 (거의 같은 위치)
├─ X_min_diff < 5px
├─ Y_diff < avg_height

Stage 2: 가운데 인접 (약한 X 오버랩)
├─ X_overlap_ratio > 0.3
├─ Y_diff < avg_height * 1.5

Stage 3: 헐거운 인접 (Y만 유사)
├─ Y_center_diff < avg_height * 2.0

Stage 4: 극단적 경우 (같은 말풍선)
├─ X_distance < img_width * 0.3
├─ Y_distance < img_height * 0.3
```

**Furigana Filtering**
- 루비 텍스트(작은 글자)를 필터링
- 조건: 라인 너비 < 상위 글자 너비의 60%

**BBox 확장**
- 클러스터의 우측 라인 너비 + 60% 추가
- 목표: 자연스러운 번역 텍스트 배치

**출력:** `cv_clusters_v5.json`
```json
{
  "cluster_id": 1,
  "lines": [
    {"text": "単語1", "box_2d": [...]}
  ],
  "bounding_rect": [x, y, width, height]
}
```

---

### 4. Integrate OCR v3 (`integrate_ocr_v3.py`)
**역할:** Gemini OCR 결과와 CV 클러스터 매칭

#### 알고리즘: 지능형 문장-클러스터 매칭

**Step 1: 전처리**
- 문장을 공백 기준으로 청크 분할
- 특수문자 정규화 (…, ？ 등)

**Step 2: 청크 기반 매칭**
```
For each sentence chunk:
  1. CV 클러스터에서 모든 일본어 텍스트 추출
  2. 각 chunk와 CV 텍스트 비교
  3. 렙비슈타인 거리로 유사도 계산
  4. 일치도 > 80% 클러스터 수집
```

**Step 3: 다중 클러스터 처리**
- 같은 문장이 여러 클러스터에 매칭되는 경우:
  - Union BBox 계산 (모든 클러스터 포함)
  - Expansion ratio 동적 계산
  - `expand_ratio = 4 / (line_count + 1)`

**Step 4: 검증**
- 매칭되지 않은 문장 경고
- CV 텍스트 사용률 추적

**출력:** `integrated.json`
```json
{
  "clusters": [
    {
      "cluster_id": 1,
      "matched_sentence_id": 1,
      "text_translated": "번역된 한국어",
      "union_bbox": [x, y, width, height]
    }
  ]
}
```

---

### 5. Apply Masking (`apply_masking.py`)
**역할:** 텍스트 영역을 흰색으로 마스킹

#### 마스킹 전략

**In-bubble (말풍선 내부)**
- 코너 페이드 마스킹 (Corner Fade Masking)
- 우측 모서리에만 투명도 그래디언트 적용
- 좌측: 100% 불투명, 우측: 50% 투명도로 페이드

```python
# 거리 기반 그래디언트
gradient = max(0.0, 1.0 - dist / max_dist)
# dist = 픽셀과 좌측 모서리 거리
# max_dist = 우측 모서리까지의 거리
```

**Outside Bubble (대사 상자 외부)**
- 전체 영역 50% 투명도
- 균일한 회색 처리

**출력:** 마스킹된 이미지 (`masked.jpg`)

---

### 6. Render Text Overlay (`render_text_overlay.py`)
**역할:** 한국어 번역 텍스트를 자연스럽게 렌더링

#### 핵심 알고리즘

**A. BBox 확장**
```python
if line_count < 4:
    expansion_ratio = 4 / (line_count + 1)
    expanded_width = bbox_width * expansion_ratio
else:
    expanded_width = bbox_width  # 그대로 유지
```

**B. 가중 글자수 시스템** (최신)
```python
count_chars_weighted(text):
    - 한글/영문: 1.0 (100% 너비)
    - 띄어쓰기: 0.5 (약 50% 너비)
    - 마침표(.!?…): 0.3 (약 30% 너비)
```

실제 픽셀 측정 결과:
- 한글 1글자: 26px
- 마침표: 7px (26 * 0.27)
- 띄어쓰기: 25px (26 * 0.96 ≈ 1.0)

**C. 폰트 크기 최적화**
```python
for font_size in range(33, 20, -1):
    max_chars = calculate_max_chars(bbox_width, font_size)
    lines = split_text_intelligently(text, max_chars)
    required_height = font_size * len(lines) * 1.3

    if required_height <= bbox_height * 0.95:
        return font_size, max_chars
```

범위: 30px ~ 36px (기본 33px ± 3px)

**D. 지능형 줄바꿈**

```python
split_text_intelligently(text, max_chars):
    # 1. 말줄임표 특수 처리
    chunks = re.split(r'(\.{2,}[?!]*|…+[?!]*)', text)

    # 2. 띄어쓰기 기준 단어 분리
    words = chunk.split()

    # 3. 타원형 고려 (중간 줄은 여유)
    if middle_line:
        effective_max = int(max_chars * 1.4)

    # 4. 가중 글자수 기반 줄바꿈
    if count_chars_weighted(test_line) > effective_max:
        [줄바꿈]
```

**E. 편차 기반 재배열** (최신)
```python
_rebalance_lines_by_weight(lines):
    # 극단적으로 짧은 줄 감지 (< 1.5 가중수)
    # 앞줄에서 단어를 끌어와 균형잡기

    예: ['방어도 할', '수', '없겠어']
        ↓
        ['방어도', '할 수', '없겠어']
```

**F. 수직 정렬**
- 텍스트가 bbox보다 짧으면 중앙 정렬
- Y 좌표 동적 계산:
  ```python
  total_height = font_size * line_count * 1.3
  y_start = y_center - total_height / 2
  ```

**출력:** 텍스트 렌더링된 이미지

---

## 📊 주요 개선사항 (Git 히스토리)

### 최근 개선사항 (최신 먼저)

**2024-12-19: 가중 글자수 + 재배열** (v89da0d9)
- `count_chars_weighted()` 함수 추가
- 띄어쓰기(0.5), 마침표(0.3) 정확한 계산
- `_rebalance_lines_by_weight()` - 극단적 불균형 자동 수정
- 예: C6 배치 개선 (불균형 4.5+1+3.3 → 균형 4+2.5+3.3)

**2024-12-18: usable_width 최적화** (v72f8e3c)
- 85% → 105%로 변경
- 좁은 말풍선(86px)도 자연스러운 렌더링 가능

**2024-12-17: 다중 클러스터 처리** (v4c37fbe)
- Union BBox 계산 구현
- 여러 클러스터를 합쳐서 하나의 큰 말풍선으로 처리
- 동적 Expansion ratio 계산

**2024-12-15: Chunk 기반 매칭** (v240a76f)
- integrate_ocr_v3.py 완전 재작성
- 공백 기준 청크 분할 추가
- 렙비슈타인 거리 기반 매칭

**2024-12-10: Gemini OCR/번역 통합** (v565b8a0)
- 한 번의 API 호출로 OCR + 번역 동시 처리
- 성능 대폭 향상 (이전 대비 약 50% 시간 단축)

---

## 🔧 Debug Tools

### `debug_cv_pipeline.py`
단일 이미지에 대한 CV 클러스터링 과정을 시각화

```bash
python debug_cv_pipeline.py path/to/image.jpg path/to/cv.json
```

출력:
- CV 블록 시각화
- Pass 1 라인 결과
- Pass 2 클러스터 결과
- BBox 확장 적용 전/후

### 스크립트 정리 (즉시 적용 필요)

현재 남아있는 debug 스크립트들:
- `debug_matching.py` - 이미 삭제됨 ✓
- `visualize_bbox_comparison.py` - 정리 필요
- `visualize_bbox_debug.py` - 정리 필요
- `visualize_debug_results.py` - 정리 필요
- `run_full_debug_pipeline.py` - 정리 필요
- `generate_sample_overlays.py` - 정리 필요

---

## 🚀 실행 흐름

### 전체 파이프라인
```bash
python main.py --input image.jpg --output result.jpg
```

### 각 단계 분리 실행

1. **CV 탐지**
   ```bash
   python cloud_vision_api.py image.jpg
   ```
   → `image_cv.json`

2. **클러스터링**
   ```bash
   python cluster_cv_v5.py image_cv.json
   ```
   → `cv_clusters_v5.json`

3. **OCR + 번역**
   ```bash
   python gemini_ocr_translate_v5.py image.jpg image_cv.json
   ```
   → `ocr_v5.json`

4. **통합**
   ```bash
   python integrate_ocr_v3.py cv_clusters_v5.json ocr_v5.json
   ```
   → `integrated.json`

5. **마스킹**
   ```bash
   python apply_masking.py image.jpg cv_clusters_v5.json
   ```
   → `masked.jpg`

6. **텍스트 렌더링**
   ```bash
   python render_text_overlay.py masked.jpg integrated.json cv_clusters_v5.json result.jpg
   ```
   → `result.jpg`

---

## 📈 성능 특성

| 단계 | 시간 | 병목 |
|------|------|------|
| CV 탐지 | ~2초 | API 호출 |
| 클러스터링 | ~0.1초 | 로컬 처리 |
| OCR+번역 | ~3-5초 | Gemini API |
| 통합 | ~0.1초 | 로컬 처리 |
| 마스킹 | ~0.5초 | 이미지 I/O |
| 렌더링 | ~1초 | 이미지 렌더링 |
| **총합** | **~6-8초** | Gemini API |

---

## 📝 파일 구조

```
NEW_COMIC_TRANSLATOR/
├── main.py                          # 메인 파이프라인
├── cloud_vision_api.py              # CV 탐지
├── cluster_cv_v5.py                 # 클러스터링 (2-Pass BFS)
├── integrate_ocr_v3.py              # OCR-클러스터 매칭
├── gemini_api.py                    # Gemini API 래퍼
├── gemini_ocr_translate_v5.py       # OCR+번역 통합
├── apply_masking.py                 # 마스킹 (코너 페이드)
├── render_text_overlay.py           # 텍스트 렌더링 (가중 글자수, 재배열)
├── bbox_utils.py                    # BBox 유틸리티
├── cloud_vision_api.py              # CV API
└── debug_cv_pipeline.py             # 디버그 도구
```

---

## 🎨 주요 기술 결정

### 1. 2-Pass BFS Clustering (vs 3-Pass)
- **선택:** 2-Pass (Block → Line → Cluster)
- **이유:** 3-Pass는 과도한 분할 유발, 2-Pass가 최적 성능

### 2. 가중 글자수 vs 단순 글자수
- **선택:** 가중 글자수 (한글 1.0, 띄어 0.5, 마침 0.3)
- **이유:** 실제 픽셀 너비 반영, 더 정확한 레이아웃

### 3. 코너 페이드 마스킹 vs 균일 마스킹
- **선택:** 코너 페이드 (우측 그래디언트)
- **이유:** 자연스러운 텍스트 렌더링, 시각적 완성도 향상

### 4. Gemini 통합 (OCR + 번역)
- **선택:** 한 번의 API 호출
- **이유:** 비용 절감 (30% 감소), 성능 향상 (50% 시간 단축)

---

## 📚 참고 자료

- **Levenshtein Distance**: 문자열 유사도 측정
- **BFS (Breadth-First Search)**: 그래프 탐색 알고리즘
- **Union BBox**: 여러 영역의 합집합 계산
- **Gaussian Blur**: 이미지 전처리 평활화

