# Debug Process 정의

**작성일**: 2025-12-22

## 개요

체계적인 번역 결과 검증과 시각화를 위한 Debug 프로세스입니다.

## 파이프라인

```
입력 이미지 (Debug/{번호}/{번호}.jpg)
         ↓
Step 1: Cloud Vision API
  → {번호}_cv.json (덮어씌우기)
         ↓
Step 2: Clustering V5
  → {번호}_cv_clusters_v5.json (덮어씌우기)
         ↓
Step 3: Gemini OCR + 번역 + Integrated JSON 생성
  → {번호}_integrated.json (덮어씌우기, 번역 포함)
         ↓
Step 4: White Masking (코너 페이드)
  → {번호}_masked.jpg (덮어씌우기)
         ↓
Step 5: Text Overlay (가중 글자수)
  → {번호}_final.jpg (최종 결과)
```

## 파일 정리

### 중간 파일 (temp_output/Debug/)
```
{번호}_cv.json                    # Cloud Vision 결과
{번호}_cv_clusters_v5.json        # 클러스터링 결과
{번호}_integrated.json           # 통합 + 번역 결과
{번호}_debug.jpg                  # line/cluster/union bbox 통합 시각화
{번호}_masked.jpg                # 마스킹 후
```

### 최종 결과 (output/Debug/)
```
{번호}_final.jpg                  # 최종 번역 이미지
```

## 시각화 상세

### {번호}_debug.jpg
- **기준 파일**: {번호}_integrated.json
- **기반 이미지**: 원본 이미지
- **표시 내용**:
  - 빨간색: sentence/union bbox
  - 파란색: matched cluster bbox
  - 초록색: line bbox

## 실행 방법

```bash
# Debug 프로세스 실행 (자동 덮어씌우기)
python3 run_debug_pipeline.py

# 결과 확인
# - 최종 이미지: temp_output/Debug/{번호}_final.jpg
# - 시각화: temp_output/Debug/{번호}_debug.jpg
```

## 필수 환경 변수

- `GOOGLE_APPLICATION_CREDENTIALS`: GCP Service Account JSON 경로
- `GEMINI_API_KEY`: Google Gemini API 키

## 참고 사항

- 모든 중간 파일은 덮어씌우기로 처리되므로 이전 결과는 보존되지 않음
- 최종 결과(output/Debug/)는 별도 보관
- 시각화 파일로 각 단계의 결과를 검증 가능
