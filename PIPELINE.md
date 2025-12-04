# 파이프라인 기술 문서

Comic Translator V5의 전체 파이프라인에 대한 자세한 기술 문서입니다.

## 목차

1. [전체 아키텍처](#전체-아키텍처)
2. [각 단계 상세 설명](#각-단계-상세-설명)
3. [데이터 구조](#데이터-구조)
4. [텍스트 렌더링 로직](#텍스트-렌더링-로직)
5. [최적화 전략](#최적화-전략)

---

## 전체 아키텍처

```
┌─────────────────┐
│  입력 이미지들   │
│  (JPG/PNG)      │
└────────┬────────┘
         │
         ▼
┌────────────────────────────────────┐
│ Step 1: Cloud Vision Grid API     │
│ • N개 이미지 → 1회 API 호출        │
│ • 그리드 레이아웃 (3x3, 4x4 등)    │
│ • 출력: *_cv.json                  │
└────────┬───────────────────────────┘
         │
         ▼
┌────────────────────────────────────┐
│ Step 2-7: 병렬 처리 (각 이미지)    │
│ ThreadPoolExecutor (max 5개)       │
└────────┬───────────────────────────┘
         │
         ├── 이미지 1 ──┐
         ├── 이미지 2 ──┤
         ├── 이미지 3 ──┼─→ 병렬 실행
         ├── 이미지 4 ──┤
         └── 이미지 5 ──┘
                │
                ▼
    ┌───────────────────────┐
    │ Step 2: 클러스터링 V5 │
    │ cluster_cv_v5.py      │
    │ 출력: cv_clusters_v5  │
    └───────┬───────────────┘
            │
            ▼
    ┌───────────────────────┐
    │ Step 3: Gemini OCR    │
    │ test_cluster_ocr.py   │
    │ 출력: ocr_v5.json     │
    └───────┬───────────────┘
            │
            ▼
    ┌───────────────────────┐
    │ Step 4: 텍스트 통합   │
    │ integrate_ocr.py      │
    │ 출력: integrated.json │
    └───────┬───────────────┘
            │
            ▼
    ┌───────────────────────┐
    │ Step 5: 번역          │
    │ translate_integrated  │
    │ 출력: *_translated    │
    └───────┬───────────────┘
            │
            ▼
    ┌───────────────────────┐
    │ Step 6: White Masking │
    │ apply_text_overlay.py │
    │ 출력: *_masked.jpg    │
    └───────┬───────────────┘
            │
            ▼
    ┌───────────────────────┐
    │ Step 7: Text Overlay  │
    │ render_text_overlay   │
    │ 출력: *_final.jpg     │
    └───────┬───────────────┘
            │
            ▼
    ┌───────────────────────┐
    │   최종 번역 이미지    │
    └───────────────────────┘
```

---

## 각 단계 상세 설명

### Step 1: Cloud Vision Grid API

**목적**: 여러 이미지를 하나의 그리드로 결합하여 1회 API 호출로 처리

**처리 과정**:
1. N개 이미지 로드
2. Grid 크기 계산: `cols = ceil(√N)`, `rows = ceil(N/cols)`
3. 각 이미지를 Grid 셀에 배치
4. 결합된 이미지로 Cloud Vision API 호출
5. 각 bbox를 원본 이미지 좌표로 역변환
6. 이미지별 `*_cv.json` 파일 저장

**출력 형식** (`*_cv.json`):
```json
[
  {
    "bbox": [x, y, width, height],
    "text": "日本語テキスト",
    "confidence": 0.95
  },
  ...
]
```

**비용 절감**:
- 기존: N개 이미지 = N회 API 호출
- Grid: N개 이미지 = 1회 API 호출
- **절감 비율**: ~N배

---

### Step 2: 클러스터링 V5

**목적**: CV bbox를 의미있는 텍스트 단위로 그룹화

**처리 과정**:
1. Bbox orientation 판단 (vertical/horizontal)
   - `aspect_ratio = height / width`
   - `vertical` if `aspect_ratio > 1.5`
   - `horizontal` otherwise

2. 인접성 기반 클러스터링
   - Vertical: Y축 기준 정렬, X축 거리 확인
   - Horizontal: X축 기준 정렬, Y축 거리 확인

3. Width 확장 (Vertical만)
   - 가장 오른쪽 line의 max block width * 0.55

4. Bounding poly 계산
   - 모든 line bbox 합집합

**출력 형식** (`*_cv_clusters_v5.json`):
```json
[
  {
    "cluster_id": 1,
    "orientation": "vertical",
    "lines": [
      {
        "text": "テキスト",
        "bbox": [x, y, w, h]
      }
    ],
    "bounding_poly": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
  },
  ...
]
```

**주요 로직**:
```python
# Width 확장 (Vertical)
max_block_w = max(b['bbox'][2] for b in rightmost_line['bboxes'])
expansion = int(max_block_w * 0.55)

# Cluster bbox 계산
if orientation == 'vertical':
    if line is rightmost_line:
        all_bboxes.append([lx, ly, lw + expansion, lh])
```

---

### Step 3: Gemini OCR

**목적**: 정확한 일본어 OCR 및 bubble_overlap 감지

**처리 과정**:
1. 전체 이미지 + cluster 좌표를 Gemini에 전달
2. 각 cluster의 정확한 텍스트 추출
3. Text type 분류 (dialogue/narration/sound_effect)
4. Location 감지 (in_bubble/outside_bubble)
5. Bubble overlap 판단

**Gemini 프롬프트 구조**:
```
다음 이미지에서 각 영역({cluster_id})의 일본어 텍스트를 정확히 추출하세요.

영역 정보:
- Cluster #1: [x1,y1], [x2,y2], [x3,y3], [x4,y4]
- Cluster #2: ...

반환 형식 (JSON):
{
  "clusters": [
    {
      "cluster_id": 1,
      "text": "抽出したテキスト",
      "text_type": "dialogue",
      "location": "in_bubble",
      "confidence": "high",
      "bubble_overlap": "none"
    }
  ]
}
```

**출력 형식** (`*_ocr_v5.json`):
```json
{
  "clusters": [
    {
      "cluster_id": 1,
      "text": "日本語",
      "text_type": "dialogue",
      "location": "in_bubble",
      "confidence": "high",
      "bubble_overlap": "none"
    }
  ],
  "merge_suggestions": [],
  "split_suggestions": []
}
```

---

### Step 4: 텍스트 통합

**목적**: CV clusters + Gemini OCR 결과 병합

**처리 과정**:
1. CV clusters에서 bounding_poly와 orientation 가져오기
2. Gemini OCR에서 text, text_type, location 가져오기
3. cluster_id 기준으로 매칭
4. 통합 JSON 생성

**출력 형식** (`*_integrated.json`):
```json
[
  {
    "cluster_id": 1,
    "bounding_poly": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]],
    "orientation": "vertical",
    "text": "日本語テキスト",
    "text_type": "dialogue",
    "location": "in_bubble",
    "confidence": "high",
    "match_score": 1.0
  }
]
```

---

### Step 5: Gemini Translation

**목적**: 일본어 → 한국어 번역

**처리 과정**:
1. Integrated JSON의 모든 텍스트 추출
2. Gemini에 배치 번역 요청
3. 번역 결과를 각 cluster에 추가

**Gemini 프롬프트**:
```
다음 일본어 만화 텍스트들을 한국어로 번역하세요.

중요 지침:
1. 동일한 화자의 대사는 일관된 어투를 유지
2. 문맥을 파악하여 자연스러운 한국어로 번역
3. 효과음은 한국어 의성어/의태어로 변환
4. 나레이션은 설명조로 번역

텍스트 목록:
[{"id": 1, "text": "テキスト", "type": "dialogue"}, ...]

반환 형식 (JSON):
{
  "translations": [
    {"cluster_id": 1, "original": "...", "translated": "..."}
  ]
}
```

**출력 형식** (`*_integrated_translated.json`):
```json
[
  {
    "cluster_id": 1,
    "bounding_poly": [...],
    "orientation": "vertical",
    "text": "日本語",
    "translated": "한국어",
    "text_type": "dialogue",
    ...
  }
]
```

---

### Step 6: White Masking

**목적**: 원본 텍스트 영역을 흰색으로 가리기

**BBox 확장 로직**:

```python
# Line 개수 기반 width 확장
if line_count >= 4:
    width_expansion_ratio = 1.0
else:
    width_expansion_ratio = 4.0 / (line_count + 1)

# 예시:
# 1줄: 4/(1+1) = 2.0배
# 2줄: 4/(2+1) = 1.33배
# 3줄: 4/(3+1) = 1.0배
# 4줄 이상: 1.0배
```

**Masking 전략**:
1. **원본 영역**: 100% 불투명 흰색 (완전히 가림)
2. **확장 영역**: 50% 투명 흰색 (배경 살짝 비침)

**처리 과정**:
1. 각 cluster의 bounding_poly 로드
2. Line 개수에 따라 bbox 확장
3. 원본 영역: 100% 흰색 사각형 그리기
4. 확장 영역: 50% 투명 흰색 사각형 그리기
5. Masked 이미지 저장

**출력**: `*_masked.jpg`

---

### Step 7: Text Overlay

**목적**: 번역된 한국어 텍스트를 이미지에 렌더링

#### 폰트 설정
- **기본 폰트**: Freesentation-4Regular.ttf
- **폰트 크기**: 30px (±3px 범위: 27-33px)
- **줄 간격**: 1.3배

#### 줄바꿈 로직

**우선순위**:
1. **말줄임표 분리**: `...` 또는 `…`로 시작하면 별도 줄
2. **띄어쓰기 우선**: 단어 단위로 분리
3. **최소 4글자 유지**: 각 줄은 최소 4글자 이상
4. **조사 기준**: 긴 단어는 한국어 조사 위치에서 분리

```python
# 말줄임표 처리
if word.startswith('...') or word.startswith('…'):
    lines.append('...')  # 말줄임표는 별도 줄
    word = rest_of_text

# 최소 글자수 유지
min_chars = 4
while len(current_line) < min_chars and has_next_word:
    current_line += " " + next_word
```

#### 짧은 텍스트 중앙 정렬

**조건**:
- 4글자 이하
- 1줄
- Vertical orientation

**동작**:
```python
if len(text) <= 4 and len(lines) == 1 and orientation == 'vertical':
    # 원본 bbox의 중간 y 좌표부터 시작
    center_y = orig_y + orig_h / 2
    start_y = center_y
else:
    # 기본: 확장된 bbox 내에서 수직 중앙 정렬
    start_y = y + (h - total_text_height) / 2
```

**예시**:
```
원본 bbox: y=818~864 (높이 46px)
중간 y = (818 + 864) / 2 = 841

텍스트 "그래서?" (3글자)
→ y=841부터 렌더링 (중앙에 위치)
```

#### 렌더링 과정
1. PIL Image로 변환
2. 폰트 크기 계산 (27-33px 범위)
3. 텍스트를 줄별로 분리
4. 각 줄을 중앙 정렬하여 그리기
5. OpenCV Image로 변환
6. 저장

**출력**: `*_final.jpg`

---

## 데이터 구조

### CV JSON (`*_cv.json`)
```json
[
  {
    "bbox": [x, y, width, height],
    "text": "認識されたテキスト",
    "confidence": 0.95
  }
]
```

### CV Clusters V5 (`*_cv_clusters_v5.json`)
```json
[
  {
    "cluster_id": 1,
    "orientation": "vertical" | "horizontal",
    "lines": [
      {
        "text": "line text",
        "bbox": [x, y, w, h]
      }
    ],
    "bounding_poly": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
  }
]
```

### OCR V5 (`*_ocr_v5.json`)
```json
{
  "clusters": [
    {
      "cluster_id": 1,
      "text": "日本語",
      "text_type": "dialogue" | "narration" | "sound_effect",
      "location": "in_bubble" | "outside_bubble",
      "confidence": "high" | "medium" | "low",
      "bubble_overlap": "none" | "partial" | "full"
    }
  ],
  "merge_suggestions": [],
  "split_suggestions": []
}
```

### Integrated (`*_integrated.json`)
```json
[
  {
    "cluster_id": 1,
    "bounding_poly": [...],
    "orientation": "vertical",
    "text": "日本語",
    "text_type": "dialogue",
    "location": "in_bubble",
    "confidence": "high",
    "match_score": 1.0
  }
]
```

### Integrated Translated (`*_integrated_translated.json`)
```json
[
  {
    "cluster_id": 1,
    "bounding_poly": [...],
    "orientation": "vertical",
    "text": "日本語",
    "translated": "한국어",
    "text_type": "dialogue",
    "location": "in_bubble",
    "confidence": "high",
    "match_score": 1.0
  }
]
```

---

## 텍스트 렌더링 로직

### 1. BBox 확장 계산

```python
def calculate_expanded_bbox(bounding_poly, line_count, orientation):
    # Width 확장 비율
    if line_count >= 4:
        width_expansion_ratio = 1.0
    else:
        width_expansion_ratio = 4.0 / (line_count + 1)
    
    if orientation == 'vertical':
        # Vertical: width 확장
        new_width = current_width * width_expansion_ratio
        width_expansion = new_width - current_width
        
        # 중점 기준으로 좌우 확장
        new_x_min = x_min - width_expansion / 2
        new_x_max = x_max + width_expansion / 2
    else:
        # Horizontal: height 확장
        new_height = current_height * width_expansion_ratio
        ...
    
    return orig_bbox, expanded_bbox
```

### 2. 폰트 크기 계산

```python
def calculate_font_size(bbox_width, bbox_height, text):
    base_font_size = 30
    min_font_size = 27
    max_font_size = 33
    min_chars = 4  # 고정
    
    # 30px부터 시작해서 bbox에 맞는지 확인
    for font_size in range(base_font_size, min_font_size - 1, -1):
        lines = split_text_intelligently(text, min_chars)
        line_count = len(lines)
        
        required_height = font_size * line_count * 1.3
        
        if required_height <= bbox_height * 0.95:
            return font_size
    
    # 더 큰 폰트도 시도
    for font_size in range(base_font_size + 1, max_font_size + 1):
        ...
    
    return base_font_size
```

### 3. 의미 단위 줄바꿈

```python
def split_text_intelligently(text, min_chars=4):
    particles = ['은', '는', '이', '가', '을', '를', ...]
    
    lines = []
    current_line = ""
    words = text.split()  # 띄어쓰기로 분리
    
    for word in words:
        # 1. 말줄임표 처리
        if word.startswith('...') or word.startswith('…'):
            if current_line:
                lines.append(current_line)
            lines.append('...')
            word = rest
            current_line = ""
        
        # 2. 단어 추가
        test_line = current_line + (" " if current_line else "") + word
        
        # 3. 최소 글자수 확인
        if len(test_line) >= min_chars or not has_next:
            current_line = test_line
            # 다음 단어를 추가해도 자연스러운지 확인
            if not too_long:
                continue
            else:
                lines.append(current_line)
                current_line = ""
        else:
            # 최소 글자수 미만이면 계속 추가
            current_line = test_line
    
    return lines
```

### 4. 텍스트 렌더링

```python
def render_korean_text(image, text, bbox, original_bbox, orientation):
    font_size = calculate_font_size(bbox[2], bbox[3], text)
    font = ImageFont.truetype(font_path, font_size)
    
    lines = split_text_intelligently(text, min_chars=4)
    line_height = font_size * 1.3
    
    # 짧은 텍스트 중앙 정렬
    if len(text) <= 4 and len(lines) == 1 and orientation == 'vertical':
        # 원본 bbox 중간부터 시작
        orig_x, orig_y, orig_w, orig_h = original_bbox
        start_y = orig_y + orig_h / 2
    else:
        # 확장된 bbox 내에서 중앙 정렬
        total_height = line_height * len(lines)
        start_y = bbox[1] + (bbox[3] - total_height) / 2
    
    # 각 줄 렌더링
    for i, line in enumerate(lines):
        text_y = start_y + i * line_height
        draw.text((text_x, text_y), line, font=font, fill=color)
```

---

## 최적화 전략

### 1. Grid CV 처리

**문제**: N개 이미지 → N회 API 호출 → 높은 비용
**해결**: Grid 레이아웃으로 1회 호출

```python
def detect_text_grid(image_paths):
    n = len(image_paths)
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    
    # 그리드 이미지 생성
    grid_image = create_grid(image_paths, rows, cols)
    
    # 1회 API 호출
    result = vision_api.text_detection(grid_image)
    
    # 각 이미지별로 bbox 분리
    return split_by_grid(result, rows, cols)
```

### 2. 병렬 처리

**문제**: 순차 처리 → 느린 속도
**해결**: ThreadPoolExecutor로 병렬 처리

```python
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {
        executor.submit(process_single_image, path): path
        for path in image_paths
    }
    
    for future in as_completed(futures):
        result = future.result()
```

### 3. 캐싱

**문제**: 재실행 시 중복 API 호출
**해결**: 중간 결과 JSON 파일 캐싱

```python
# CV JSON이 있으면 재사용
if cv_json.exists():
    log.append("✅ CV JSON 캐시 사용")
else:
    # API 호출
    call_cv_api()
```

### 4. Batch 처리

**Gemini Translation**:
```python
# 개별 번역 (N회 API 호출)
for cluster in clusters:
    translate(cluster['text'])

# 배치 번역 (1회 API 호출)
all_texts = [c['text'] for c in clusters]
results = translate_batch(all_texts)
```

---

## 성능 메트릭

### 처리 시간 (단일 이미지)

| 단계 | 소요 시간 |
|------|-----------|
| CV API | ~2초 (Grid 사용 시 N개 → ~2초) |
| 클러스터링 | ~0.5초 |
| Gemini OCR | ~10-15초 |
| 통합 | ~0.1초 |
| 번역 | ~5-10초 |
| Masking | ~0.5초 |
| Overlay | ~0.5초 |
| **총합** | **~20-30초 (단일), ~30-45초 (배치 5개)** |

### 비용 (예상)

- **Cloud Vision API**: $1.50/1000 images
  - Grid 사용 시: $1.50/1000 grids (N개 이미지 = 1 grid)
- **Gemini API**: 무료 tier 사용 가능
  - 15 RPM (requests per minute)
  - 1M TPM (tokens per minute)

### 메모리 사용

- **단일 이미지**: ~200MB
- **배치 (5개)**: ~500MB
- **Grid (9개)**: ~300MB (Grid 통합 이미지)

---

## 확장 가능성

### 향후 개선 사항

1. **GPU 가속**: Text rendering에 GPU 사용
2. **더 많은 언어 지원**: 중국어, 영어 등
3. **UI 개선**: 실시간 미리보기, 수동 조정
4. **OCR 정확도**: Fine-tuning Gemini 모델
5. **스타일 전이**: 폰트 스타일 자동 감지 및 적용

---

## 문제 해결 가이드

### 일반적인 문제

#### 1. CV Grid bbox가 겹침
**원인**: Grid 셀 크기가 너무 작음
**해결**: 이미지 크기를 조절하거나 Grid 크기 증가

#### 2. Gemini OCR 오인식
**원인**: 흐릿한 이미지, 복잡한 폰트
**해결**: 이미지 전처리, 프롬프트 개선

#### 3. 번역 품질 저하
**원인**: 문맥 부족, 프롬프트 불충분
**해결**: Extra prompt 활용, 번역 스타일 지정

#### 4. 텍스트가 bbox를 벗어남
**원인**: Font size가 너무 크거나 줄바꿈 실패
**해결**: min_chars 조정, bbox 확장 비율 증가

---

## 참고 자료

- [Google Cloud Vision API Docs](https://cloud.google.com/vision/docs)
- [Google Gemini API Docs](https://ai.google.dev/docs)
- [OpenCV Documentation](https://docs.opencv.org/)
- [Pillow Documentation](https://pillow.readthedocs.io/)
- [Gradio Documentation](https://www.gradio.app/docs/)
