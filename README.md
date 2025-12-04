# Comic Translator V5

일본어 만화를 한국어로 자동 번역하는 AI 기반 번역 시스템입니다.

## ✨ 주요 기능

- **Grid CV 처리**: 여러 이미지를 한 번의 Cloud Vision API 호출로 처리 (비용 절감)
- **V5 클러스터링**: Orientation, lines 구조를 포함한 고급 텍스트 그룹화
- **Gemini OCR**: 정확한 일본어 OCR 및 bubble_overlap 감지
- **의미 단위 줄바꿈**: 최소 4글자/줄, 띄어쓰기 및 조사 기준 자연스러운 줄바꿈
- **말줄임표 처리**: `...` 또는 `…`을 별도 줄로 분리
- **짧은 텍스트 중앙 정렬**: 4글자 이하 텍스트는 원본 bbox 중간부터 렌더링
- **병렬 처리**: 최대 5개 이미지 동시 처리로 빠른 속도
- **White Masking**: 원본 텍스트 영역을 자동으로 가리고 번역 텍스트 오버레이

## 📋 파이프라인 단계

1. **Cloud Vision Grid API** → `cv.json` (1회 API 호출로 N개 이미지 처리)
2. **클러스터링 V5** → `cv_cluster_v5.json` (병렬)
3. **Gemini OCR** → `ocr_v5.json` (병렬)
4. **텍스트 통합** → `integrated.json` (병렬)
5. **Gemini Translation** → `integrated_translated.json` (병렬)
6. **White Masking** → `_masked.jpg` (병렬)
7. **Text Overlay** → `_final.jpg` (병렬)

자세한 기술 문서는 [PIPELINE.md](PIPELINE.md)를 참조하세요.

## 🚀 설치

### 1. Python 환경 설정

```bash
# Python 3.10 이상 필요
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 필요한 패키지 설치
pip install -r requirements.txt
```

### 2. API 키 설정

`.env` 파일을 생성하고 다음 내용을 입력:

```bash
# Gemini API Key (https://aistudio.google.com/app/apikey)
GEMINI_API_KEY=your_gemini_api_key_here

# Google Cloud Vision API 인증 파일 경로
GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/service-account-key.json
```

### 3. 폰트 설치

`Fonts/` 디렉토리에 다음 폰트 파일 추가:
- `Freesentation-4Regular.ttf` (기본 폰트)

## 💻 사용법

### Gradio UI (권장)

```bash
python3 main.py
```

브라우저에서 `http://localhost:7860` 접속

### 배치 처리

1. 이미지 업로드 (복수 가능)
2. 추가 번역 프롬프트 입력 (선택)
3. "배치 번역 시작" 클릭
4. 결과 이미지 확인 및 ZIP 다운로드

### CLI (개별 스크립트)

```bash
# 1. Cloud Vision API
python3 cloud_vision_api.py input.jpg output/image_cv.json

# 2. 클러스터링 V5
python3 cluster_cv_v5.py output/image_cv.json output/image_cv_clusters_v5.json

# 3. Gemini OCR
python3 test_cluster_ocr.py input.jpg output/image_cv_clusters_v5.json output/image_ocr_v5.json

# 4. 텍스트 통합
python3 integrate_ocr.py output/image_cv_clusters_v5.json output/image_ocr_v5.json output/image_integrated.json

# 5. 번역
python3 translate_integrated.py output/image_integrated.json output/image_integrated_translated.json

# 6. White Masking
python3 apply_text_overlay.py input.jpg output/image_integrated.json output/image_cv_clusters_v5.json output/image_masked.jpg

# 7. Text Overlay
python3 render_text_overlay.py output/image_masked.jpg output/image_integrated_translated.json output/image_cv_clusters_v5.json output/image_final.jpg
```

## 📂 디렉토리 구조

```
NEW_COMIC_TRANSLATOR/
├── main.py                          # Gradio UI (메인 진입점)
├── cloud_vision_api.py              # Google Cloud Vision API
├── cluster_cv_v5.py                 # V5 클러스터링
├── test_cluster_ocr.py              # Gemini OCR
├── integrate_ocr.py                 # CV + Gemini 통합
├── translate_integrated.py          # Gemini 번역
├── apply_text_overlay.py            # White Masking
├── render_text_overlay.py           # Text Overlay
├── gemini_api.py                    # Gemini API 래퍼
├── output/                          # 출력 디렉토리
│   ├── *_cv.json                    # Cloud Vision 결과
│   ├── *_cv_clusters_v5.json        # 클러스터링 결과
│   ├── *_ocr_v5.json                # Gemini OCR 결과
│   ├── *_integrated.json            # 통합 결과
│   ├── *_integrated_translated.json # 번역 결과
│   ├── *_masked.jpg                 # 마스킹된 이미지
│   ├── *_final.jpg                  # 최종 번역 이미지
│   └── translated_comics.zip        # 배치 결과 ZIP
├── Fonts/                           # 폰트 파일
│   └── Freesentation-4Regular.ttf
└── requirements.txt                 # Python 패키지
```

## ⚙️ 설정

### 폰트 설정

`render_text_overlay.py`에서 폰트 변경:

```python
font_path = "Fonts/YourFont.ttf"
```

### 텍스트 렌더링 설정

- **폰트 크기**: 30px (±3px 범위)
- **최소 글자/줄**: 4글자
- **줄 간격**: 1.3배
- **정렬**: 중앙 정렬
- **짧은 텍스트**: 원본 bbox 중간부터 시작

### 번역 프롬프트 예시

```
반말을 사용하고, 소년 만화 스타일로 박진감 넘치게 번역해줘
```

```
공손한 말투로 번역하고, 고급스러운 어휘를 사용해줘
```

## 🔧 트러블슈팅

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

### CV Grid 처리 실패

```
❌ CV Grid 처리 실패
```

→ Google Cloud Vision API 할당량 확인
→ 이미지 크기 확인 (너무 크면 리사이즈 필요)

## 📊 성능 및 비용

### Smart Grid 처리

시스템이 자동으로 파일 크기에 따라 Grid 크기를 조정합니다:

**Cloud Vision API 제한:**
- 파일 크기: 최대 20MB
- 이미지 해상도: 최대 75 Megapixels (75,000,000 픽셀)
- 초과 시 자동 리사이즈

**Grid 크기 자동 결정:**
- **작은 파일 (평균 <1.2MB)**: 4x4 Grid (최대 16개/배치, ~19.2MB)
- **중간 파일 (1.2~2MB)**: 3x3 Grid (최대 9개/배치, ~18MB)
- **큰 파일 (≥2MB)**: 2x2 Grid (최대 4개/배치, ~8MB, 안전 마진)
- **자동 배치 분할**: 제한 초과 시 여러 배치로 분할

**예시: 100개 이미지 처리**
- 평균 1.0MB: 4x4 Grid, 7개 배치 (16+16+16+16+16+16+4)
  - CV API 호출: 7회 (93% 절감!)
- 평균 1.5MB: 3x3 Grid, 12개 배치
  - CV API 호출: 12회 (88% 절감!)
- 평균 3.0MB: 2x2 Grid, 25개 배치
  - CV API 호출: 25회 (75% 절감!)


### Grid 처리의 이점

- **기존**: N개 이미지 → N회 CV API 호출 ($1.50/1000 images)
- **Grid**: N개 이미지 → 1회 CV API 호출 ($1.50/1000 images)
- **절감**: 약 N배 비용 절감

예: 9개 이미지 처리 시
- 기존: 9회 호출
- Grid: 1회 호출 (9배 절감)

### 처리 속도

- **단일 이미지**: 약 2-3분
- **배치 (병렬)**: 약 3-5분 (최대 5개 동시)

## 🤝 기여

버그 리포트, 기능 제안, PR 환영합니다!

## 📝 라이선스

MIT License

## 🙏 Credits

- Google Cloud Vision API
- Google Gemini API
- Freesentation Font
- OpenCV, Pillow, Gradio
