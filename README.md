# Comic Translator — 프로젝트 안내 및 초기 구현 기록

**작성일**: 2025-12-22 | **최종 업데이트**: v89da0d9

일본어 만화를 한국어로 자동 번역하는 AI 기반 번역 시스템입니다.

> **현재 문서 안내 (2026-10-19)**: 현재 사용법은 [편집기 매뉴얼](docs/editor_manual.html), 구조는 [코드베이스 해설](docs/CODEBASE_FILE_MAP.md)과 [파이프라인 해설](docs/pipeline_guide.html)을 기준으로 봅니다. 현재 처리 기준은 **경계 기반 / 텍스트 기반**, 화면은 **R 번호 + 타입 배지**입니다. [용어·호환 변경 기록](docs/REGION_PROCESSING_BASIS_CHANGELOG.md)을 함께 참고하세요.
>
> 아래 상세 설명은 2025-12-22 초기 구현 기록입니다. 예시의 `location: in_bubble`, 코너 페이드, Gradio 등은 현재 구현을 선언하지 않습니다. 당시의 위치 분류와 현재의 처리 기준은 구분하며, 저장 예시·파일명·알고리즘 이름은 이력을 위해 보존합니다.


## 📋 목차

1. [주요 기능](#주요-기능)
2. [빠른 시작](#빠른-시작)
3. [상세 설치](#상세-설치)
4. [사용법](#사용법)
5. [파이프라인 구조](#파이프라인-구조)
6. [각 단계 상세 설명](#각-단계-상세-설명)
7. [데이터 구조](#데이터-구조)
8. [텍스트 렌더링 로직](#텍스트-렌더링-로직)
9. [기술 결정사항](#기술-결정사항)
10. [성능 및 비용](#성능-및-비용)
11. [트러블슈팅](#트러블슈팅)

---

## ✨ 주요 기능

- **작품별 자동 용어집**: 작품 확인 후 웹 조사, 근거 있는 표기 자동 적용, 충돌·불확실 항목만 검토 ([사용법](docs/WORK_GLOSSARY.md))
- **Grid CV 처리**: 여러 이미지를 한 번의 Cloud Vision API 호출로 처리 (비용 절감)
- **2-Pass BFS 클러스터링**: Block → Line → Cluster 단계적 그룹화
- **청크 기반 매칭**: 개행 기준 청크로 문장-클러스터 정확 매칭
- **가중 글자수 시스템**: 실제 픽셀 너비 기반 렌더링 (한글 1.0, 띄어 0.5, 마침 0.3)
- **편차 기반 재배열**: 극단적 불균형 자동 감지 및 단어 재배치
- **코너 페이드 마스킹**: 우측 그래디언트로 자연스러운 텍스트 전환
- **의미 단위 줄바꿈**: 최소 4글자/줄, 띄어쓰기 기준 자연스러운 줄바꿈
- **말줄임표 처리**: `...` 또는 `…`을 별도 줄로 분리
- **짧은 텍스트 중앙 정렬**: 4글자 이하 텍스트는 원본 bbox 중간부터 렌더링
- **병렬 처리**: 최대 5개 이미지 동시 처리로 빠른 속도

---

## 빠른 시작

### 1. 환경 설정

```bash
# Python 3.10 이상 필요
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 패키지 설치
pip install -r requirements.txt
```

### 2. API 키 설정

`.env` 파일 생성:
```bash
GEMINI_API_KEY=your_gemini_api_key_here
GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/service-account-key.json
```

### 3. 폰트 설치

`Fonts/` 디렉토리에 `Freesentation-4Regular.ttf` 추가

### 4. 실행

```bash
python3 main.py
# 브라우저에서 http://localhost:7860 접속
```

---

## 상세 설치

### API 인증 설정

#### Google Gemini API
1. https://aistudio.google.com/app/apikey 방문
2. API 키 생성
3. `.env`에 `GEMINI_API_KEY=` 추가

#### Google Cloud Vision API
1. Google Cloud Console 접속
2. Service Account 생성
3. JSON 키 파일 다운로드
4. `.env`에 `GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` 추가

### 폰트 선택

`render_text_overlay.py`에서 폰트 변경 가능:
```python
font_path = "Fonts/YourFont.ttf"  # 다른 폰트 사용 가능
```

---

## 사용법

### Gradio UI (권장)

```bash
python3 main.py
```

**기능**:
- 이미지 업로드 (복수 선택 가능)
- 추가 번역 프롬프트 입력
- 배치 처리로 여러 이미지 동시 번역
- 결과 이미지 다운로드 및 ZIP 생성

### CLI (개별 스크립트)

```bash
# 1. Cloud Vision API
python3 pipeline/cloud_vision_api.py input.jpg output/image_cv.json

# 2. 클러스터링
python3 pipeline/cluster_cv_v5.py output/image_cv.json output/image_cv_clusters_v5.json

# 3. Gemini OCR + 번역 + integrated JSON 생성
python3 pipeline/gemini_ocr_translate_v5.py input.jpg output/image_cv_clusters_v5.json output/image_integrated.json

# 4. White Masking
python3 pipeline/apply_masking.py input.jpg output/image_integrated.json output/image_cv_clusters_v5.json output/image_masked.jpg

# 5. Text Overlay
python3 pipeline/render_text_overlay.py output/image_masked.jpg output/image_integrated.json output/image_cv_clusters_v5.json output/image_final.jpg
```

---

## 파이프라인 구조

### 전체 아키텍처

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
│ Step 2-6: 병렬 처리 (각 이미지)    │
│ ThreadPoolExecutor (max 5개)       │
└────────┬───────────────────────────┘
         │
    ├─ Step 2: 클러스터링 V5 (2-Pass BFS)
    │         출력: *_cv_clusters_v5.json
    │
    ├─ Step 3: Gemini OCR + 번역 + integrated JSON 생성
    │         출력: *_integrated.json
    │
    ├─ Step 4: White Masking (코너 페이드)
    │         출력: *_masked.jpg
    │
    └─ Step 5: Text Overlay (가중 글자수)
              출력: *_final.jpg (최종 결과)
         │
         ▼
┌─────────────────┐
│  최종 번역 이미지  │
│  (*_final.jpg)  │
└─────────────────┘
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

**비용 절감**:
- 기존: N개 이미지 = N회 API 호출
- Grid: N개 이미지 = 1회 API 호출
- **절감 비율**: ~N배

---

### Step 2: 클러스터링 V5

**목적**: CV bbox를 의미있는 텍스트 단위로 그룹화

#### 알고리즘: 2-Pass BFS Clustering

**Pass 1: Block → Line (Y축 확장)**
- CV에서 감지된 개별 텍스트 블록을 "행(Line)"으로 통합
- 같은 X 영역에서 세로(Y축)로 인접한 블록 병합
- 방법: BFS (Breadth-First Search)

```
Stage 1 조건: Y축 오버랩 + X 근접성
├─ Y_overlap_ratio > 0.5
└─ X_distance < avg_width * 1.5
```

**Pass 2: Line → Cluster (4-Stage Rule)**
- 라인들을 더 큰 단위(클러스터)로 그룹화

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

**Furigana Filtering**: 루비 텍스트(작은 글자) 필터링
- 조건: 라인 너비 < 상위 글자 너비의 60%

---

### Step 3: Gemini OCR + 번역 + Integrated JSON 생성

**목적**: 정확한 일본어 OCR, 문장 분리, 번역, cluster 매칭 결과를 한 번에 생성

**처리 과정**:
1. 전체 이미지 + cluster 좌표를 Gemini에 전달
2. 시각적으로 한 문장인 텍스트 조각을 병합
3. 일본어 → 한국어 번역
4. 문장별 `matched_cluster` 목록 생성
5. `union_bbox`와 `matched_cluster_details`를 포함한 integrated JSON 저장

**출력 형식** (`*_integrated.json`):
```json
{
  "clusters": [
    {
      "sentence_id": 1,
      "text": "日本語",
      "text_translated": "한국어",
      "matched_cluster": [1, 2],
      "matched_cluster_details": [],
      "union_bbox": [100, 120, 80, 240],
      "bounding_rect": [100, 120, 80, 240],
      "location": "in_bubble"
    }
  ],
  "unmatched_clusters": []
}
```

---

### Step 4: White Masking (코너 페이드)

**목적**: 원본 텍스트 영역을 마스킹

#### 마스킹 전략

**초기 구현의 위치 분류: 말풍선 내부 (`in_bubble`)**
- 코너 페이드 마스킹 (Corner Fade Masking)
- 우측 모서리에만 투명도 그래디언트 적용
- 좌측: 100% 불투명, 우측: 50% 투명도로 페이드

```python
# 거리 기반 그래디언트
gradient = max(0.0, 1.0 - dist / max_dist)
# dist = 픽셀과 좌측 모서리 거리
# max_dist = 우측 모서리까지의 거리
```

**초기 구현의 위치 분류: 대사 상자 외부**
- 전체 영역 50% 투명도
- 균일한 회색 처리

---

### Step 5: Text Overlay (텍스트 렌더링)

**목적**: 번역된 한국어 텍스트를 이미지에 자연스럽게 렌더링

#### 핵심 알고리즘

**A. BBox 확장**
```python
if line_count < 4:
    expansion_ratio = 4 / (line_count + 1)
    expanded_width = bbox_width * expansion_ratio
else:
    expanded_width = bbox_width  # 그대로 유지
```

**B. 가중 글자수 시스템** (최신 개선)

실제 픽셀 측정 결과:
```
한글 글자:     26px → 1.0
띄어쓰기:     25px → 0.5 (약 절반)
마침표(.!?…): 7px → 0.3 (약 1/3)
```

구현:
```python
def count_chars_weighted(text):
    punct = '.!?…'
    weight = 0
    for c in text:
        if c == ' ':
            weight += 0.5
        elif c in punct:
            weight += 0.3
        else:
            weight += 1.0
    return weight
```

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

**D. 편차 기반 재배열** (최신 개선)

극단적으로 짧은 줄을 자동 감지하고 균형잡기:

```python
# 예시
이전: ['방어도 할', '수', '없겠어.']  (불균형: 4.5 + 1 + 3)
      ↓
현재: ['방어도', '할 수', '없겠어.']  (균형: 3 + 2.5 + 3.3)
```

로직:
```python
# 극단적으로 짧은 줄 찾기 (< 1.5 가중수)
for i, w in enumerate(weights):
    if w < 1.5 and i > 0:
        # 앞줄의 마지막 단어를 현재 줄 앞에 추가
        prev_line_words = lines[i - 1].split()
        curr_line_words = lines[i].split()

        moved_word = prev_line_words.pop()
        curr_line_words.insert(0, moved_word)
```

**E. 의미 단위 줄바꿈**

우선순위:
1. **말줄임표 분리**: `...` 또는 `…`로 시작하면 별도 줄
2. **띄어쓰기 우선**: 단어 단위로 분리
3. **가중 글자수 기준**: 실제 너비 기반 줄바꿈
4. **균형 조정**: 극단적 불균형 자동 수정

**F. 수직 정렬**
- 텍스트가 bbox보다 짧으면 중앙 정렬
- Y 좌표 동적 계산

---

## 데이터 구조

### CV JSON (`*_cv.json`)
```json
[
  {
    "box_2d": [ymin, xmin, ymax, xmax],
    "label": "認識されたテキスト",
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
        "box_2d": [ymin, xmin, ymax, xmax]
      }
    ],
    "bounding_rect": [x, y, width, height]
  }
]
```

### Integrated V3 (`*_integrated.json`)
```json
{
  "clusters": [
    {
      "cluster_id": 1,
      "matched_sentence_id": 1,
      "text_original": "日本語",
      "text_translated": "한국어",
      "union_bbox": [x, y, w, h],
      "match_score": 0.9,
      "line_count": 2,
      "text_type": "dialogue",
      "location": "in_bubble"
    }
  ]
}
```

---

## 텍스트 렌더링 로직

### 렌더링 파이프라인

1. **BBox 확장** (Line 개수 기반)
2. **폰트 크기 계산** (Width, Height 기반)
3. **가중 글자수 계산** (실제 픽셀 너비 반영)
4. **의미 단위 줄바꿈** (띄어쓰기, 마침표 기준)
5. **편차 기반 재배열** (극단적 불균형 수정)
6. **수직 정렬** (중앙 또는 전체)
7. **렌더링** (PIL + 저장)

---

## 기술 결정사항

### 1. 2-Pass BFS Clustering (vs 3-Pass)
- **선택**: 2-Pass (Block → Line → Cluster)
- **이유**: 3-Pass는 과도한 분할 유발, 2-Pass가 최적 성능

### 2. 가중 글자수 시스템 (vs 단순 글자수)
- **선택**: 가중 글자수 (한글 1.0, 띄어 0.5, 마침 0.3)
- **이유**: 실제 픽셀 너비 반영, 더 정확한 레이아웃

### 3. 코너 페이드 마스킹 (vs 균일 마스킹)
- **선택**: 코너 페이드 (우측 그래디언트)
- **이유**: 자연스러운 텍스트 렌더링, 시각적 완성도 향상

### 4. 청크 기반 매칭 (vs 전체 문장 매칭)
- **선택**: 청크 기반 (개행 기준 분리)
- **이유**: 정확한 bbox 확장, CV 누락 글자 정확 감지

### 5. Gemini 통합 (OCR + 번역)
- **선택**: 한 번의 API 호출
- **이유**: 비용 절감 (30% 감소), 성능 향상 (50% 시간 단축)

---

## 성능 및 비용

### 처리 시간

| 단계 | 소요 시간 |
|------|----------|
| CV API | ~2초 |
| 클러스터링 | ~0.5초 |
| Gemini (OCR + 번역) | ~10-15초 |
| 통합 | ~0.1초 |
| Masking | ~0.5초 |
| Overlay | ~1초 |
| **총합** | **~15-20초 (단일), ~20-30초 (배치 5개)** |

### 비용 (Cloud Vision API)

- **기존**: N개 이미지 → N회 호출 → $1.50/1000 images
- **Grid**: N개 이미지 → 1회 호출 → $1.50/1000 grids
- **절감**: ~N배

### 메모리 사용

- **단일 이미지**: ~200MB
- **배치 (5개)**: ~500MB
- **Grid (9개)**: ~300MB

---

## 트러블슈팅

### API 오류

```
❌ GEMINI_API_KEY 환경변수 필요
```
→ `.env` 파일에 `GEMINI_API_KEY` 설정 확인

```
❌ GOOGLE_APPLICATION_CREDENTIALS 환경변수 필요
```
→ `.env` 파일에 `GOOGLE_APPLICATION_CREDENTIALS` 경로 확인

### 폰트 오류

```
⚠️ 폰트 로드 실패
```
→ `Fonts/Freesentation-4Regular.ttf` 파일 존재 확인

### 텍스트가 bbox를 벗어남

**원인**: Font size가 너무 크거나 줄바꿈 실패
**해결**:
- `min_chars` 조정 (기본 4글자)
- `usable_width` 조정 (기본 105%)
- BBox 확장 비율 증가

### CV Grid 처리 실패

```
❌ CV Grid 처리 실패
```
→ Google Cloud Vision API 할당량 확인
→ 이미지 크기 확인 (40MB payload 제한)

---

## 디렉토리 구조

```
NEW_COMIC_TRANSLATOR/
├── main.py                          # Gradio UI (메인 진입점)
├── scripts/run_debug_pipeline.py            # Debug 실행 wrapper
├── run_test_pipeline.py             # Test 실행 wrapper
├── scripts/visualize_debug.py               # bbox 시각화
├── pipeline/
│   ├── cloud_vision_api.py          # Cloud Vision API
│   ├── cluster_cv_v5.py             # V5 클러스터링 (2-Pass BFS)
│   ├── gemini_ocr_translate_v5.py   # OCR + 번역 + integrated JSON 생성
│   ├── apply_masking.py             # White Masking (코너 페이드)
│   ├── render_text_overlay.py       # Text Overlay (가중 글자수)
│   └── translate_cover.py           # 컬러 표지 번역
├── batch/                           # Gemini Batch API 보존 영역
├── Fonts/                           # 폰트 파일
│   └── Freesentation-4Regular.ttf
├── requirements.txt                 # Python 패키지
└── output/                          # 출력 디렉토리
```

---

## 최신 개선사항

### 최근 업데이트

1. **가중 글자수 시스템** (v89da0d9)
   - 한글: 1.0, 띄어쓰기: 0.5, 마침표: 0.3
   - 실제 픽셀 너비 기반 정확한 계산

2. **편차 기반 재배열** (v89da0d9)
   - 극단적으로 짧은 줄(< 1.5 가중수) 자동 감지
   - 앞줄에서 단어를 끌어와 균형잡기

3. **usable_width 최적화** (최신)
   - 85% → 105%로 변경
   - 좁은 말풍선도 자연스러운 렌더링

4. **다중 클러스터 처리**
   - Union BBox 계산
   - 동적 Expansion ratio

5. **Gemini integrated JSON 직접 사용**
   - `matched_cluster`, `matched_cluster_details`, `union_bbox` 기준 렌더링
   - 별도 integration 단계 없이 masking/overlay로 전달

---

## 참고 자료

- [Google Cloud Vision API](https://cloud.google.com/vision/docs)
- [Google Gemini API](https://ai.google.dev/docs)
- [OpenCV Documentation](https://docs.opencv.org/)
- [Pillow Documentation](https://pillow.readthedocs.io/)
- [Gradio Documentation](https://www.gradio.app/docs/)

---

## 라이선스

MIT License

## Credits

- Google Cloud Vision API
- Google Gemini API
- Freesentation Font
- OpenCV, Pillow, Gradio
