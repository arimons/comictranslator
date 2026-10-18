# Cloud Vision API 모듈 — REST API(API Key) 방식으로 단일화
#!/usr/bin/env python3

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

_VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def _load_api_key() -> str:
    key = os.getenv("CLOUD_VISION_API_KEY")
    if not key:
        raise EnvironmentError("CLOUD_VISION_API_KEY 환경 변수가 설정되지 않았습니다.")
    return key


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _clamp_box(box_2d: List[int], image_size: Tuple[int, int]) -> List[int]:
    width, height = image_size
    ymin, xmin, ymax, xmax = [int(v) for v in box_2d]
    return [
        max(0, min(height, ymin)),
        max(0, min(width, xmin)),
        max(0, min(height, ymax)),
        max(0, min(width, xmax)),
    ]


def _normalize_box(box_2d: List[int], scale: float, image_size: Tuple[int, int]) -> List[int]:
    if scale == 1.0:
        return _clamp_box(box_2d, image_size)
    normalized = [int(round(v / scale)) for v in box_2d]
    return _clamp_box(normalized, image_size)


def _prepare_upscale2x_clahe(image_path: str) -> Tuple[str, float, Tuple[int, int], str | None]:
    import cv2

    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(image_path)

    height, width = image.shape[:2]
    upscaled = cv2.resize(image, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    prepared = cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)

    tmp = tempfile.NamedTemporaryFile(
        prefix=f"{Path(image_path).stem}_cv_up2_clahe_",
        suffix=".jpg",
        delete=False,
    )
    tmp.close()
    cv2.imwrite(tmp.name, prepared)
    return tmp.name, 2.0, (width, height), tmp.name


def _prepare_request_image(
    image_path: str,
    preprocess: str | None,
) -> Tuple[str, float, Tuple[int, int], str | None]:
    if preprocess in (None, "", "none"):
        import cv2

        image = cv2.imread(image_path)
        if image is None:
            raise FileNotFoundError(image_path)
        height, width = image.shape[:2]
        return image_path, 1.0, (width, height), None

    if preprocess == "upscale2x_clahe":
        return _prepare_upscale2x_clahe(image_path)

    raise ValueError(f"지원하지 않는 Cloud Vision 전처리: {preprocess}")


def _post(api_key: str, body: dict) -> dict:
    try:
        resp = requests.post(
            f"{_VISION_ENDPOINT}?key={api_key}",
            json=body,
            timeout=60,
        )
    except requests.RequestException as exc:
        # 연결 오류 메시지에는 요청 URL(키 포함)이 그대로 들어가 UI 로그로 흘러간다.
        raise RuntimeError(str(exc).replace(api_key, "***")) from None
    if resp.status_code != 200:
        raise RuntimeError(f"Cloud Vision HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


class CloudVisionAPI:
    def __init__(
        self,
        api_key: Optional[str] = None,
        language_hints: Optional[List[str]] = None,
    ):
        self.api_key = api_key or _load_api_key()
        self.language_hints = language_hints or ["ja", "en"]

    # ── 공통 요청 헬퍼 ──────────────────────────────────────────────────────────

    def _request(self, image_path: str, feature_type: str) -> dict:
        body = {
            "requests": [{
                "image": {"content": _encode_image(image_path)},
                "features": [{"type": feature_type}],
                "imageContext": {"languageHints": self.language_hints},
            }]
        }
        result = _post(self.api_key, body)
        responses = result.get("responses", [{}])
        if not responses:
            return {}
        resp = responses[0]
        if resp.get("error"):
            raise RuntimeError(f"Cloud Vision API 오류: {resp['error']}")
        return resp

    # ── TEXT_DETECTION ─────────────────────────────────────────────────────────

    def detect_text(self, image_path: str, preprocess: str | None = None) -> Optional[List[Dict]]:
        """TEXT_DETECTION — 행/단어 단위 bbox 추출."""
        request_path = image_path
        scale = 1.0
        image_size = (0, 0)
        tmp_path = None
        try:
            request_path, scale, image_size, tmp_path = _prepare_request_image(image_path, preprocess)
            resp = self._request(request_path, "TEXT_DETECTION")
            annotations = resp.get("textAnnotations", [])
            if not annotations:
                _safe_print("⚠️ Cloud Vision: 텍스트 없음")
                return []

            results = []
            for text in annotations[1:]:   # 첫 항목은 전체 텍스트
                verts = text["boundingPoly"]["vertices"]
                xs = [v.get("x", 0) for v in verts]
                ys = [v.get("y", 0) for v in verts]
                results.append({
                    "box_2d": _normalize_box([min(ys), min(xs), max(ys), max(xs)], scale, image_size),
                    "label": text["description"],
                    "confidence": 1.0,
                })
            suffix = f" ({preprocess})" if preprocess not in (None, "", "none") else ""
            _safe_print(f"[OK] Cloud Vision TEXT_DETECTION{suffix}: {len(results)}개")
            return results
        except Exception as e:
            _safe_print(f"[ERR] detect_text: {e}")
            return None
        finally:
            if tmp_path is not None:
                Path(tmp_path).unlink(missing_ok=True)

    # ── DOCUMENT_TEXT_DETECTION ────────────────────────────────────────────────

    def detect_document_text(self, image_path: str, preprocess: str | None = None) -> Optional[List[Dict]]:
        """DOCUMENT_TEXT_DETECTION — 단어 단위 bbox + confidence."""
        request_path = image_path
        scale = 1.0
        image_size = (0, 0)
        tmp_path = None
        try:
            request_path, scale, image_size, tmp_path = _prepare_request_image(image_path, preprocess)
            resp = self._request(request_path, "DOCUMENT_TEXT_DETECTION")
            full = resp.get("fullTextAnnotation")
            if not full:
                _safe_print("⚠️ Cloud Vision: 문서 텍스트 없음")
                return []

            results = []
            for page in full.get("pages", []):
                for block in page.get("blocks", []):
                    for para in block.get("paragraphs", []):
                        for word in para.get("words", []):
                            word_text = "".join(
                                s["text"] for s in word.get("symbols", [])
                            )
                            verts = word["boundingBox"]["vertices"]
                            xs = [v.get("x", 0) for v in verts]
                            ys = [v.get("y", 0) for v in verts]
                            results.append({
                                "box_2d": _normalize_box([min(ys), min(xs), max(ys), max(xs)], scale, image_size),
                                "label": word_text,
                                "confidence": word.get("confidence", 1.0),
                            })
            suffix = f" ({preprocess})" if preprocess not in (None, "", "none") else ""
            _safe_print(f"[OK] Cloud Vision DOCUMENT_TEXT_DETECTION{suffix}: {len(results)}개")
            return results
        except Exception as e:
            _safe_print(f"[ERR] detect_document_text: {e}")
            return None
        finally:
            if tmp_path is not None:
                Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    import sys

    api = CloudVisionAPI()

    if len(sys.argv) >= 3:
        image_path, output_path = sys.argv[1], sys.argv[2]
        if not Path(image_path).exists():
            print(f"❌ 이미지 없음: {image_path}")
            sys.exit(1)

        results = api.detect_document_text(image_path, preprocess="upscale2x_clahe")
        if results is None:
            # 호출 실패를 "텍스트 0개"로 저장하면 이후 단계와 전역 CV 캐시가 빈 결과를 성공으로 굳힌다.
            print("❌ CV API 호출 실패: 결과 파일을 쓰지 않습니다.")
            sys.exit(1)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results or [], f, ensure_ascii=False, indent=2)
        print(f"✅ CV API 완료: {len(results or [])}개 텍스트 감지 (upscale2x_clahe)")

    else:
        print("Usage:")
        print("  python3 cloud_vision_api.py <image_path> <output_path>")
        sys.exit(1)
