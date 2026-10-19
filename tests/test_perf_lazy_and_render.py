# Step 3의 LaMa 지연 로드(I2)와 overlay 렌더의 페이지 단위 PIL 변환(I5)을 검증한다.

import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import pipeline.dialogue_extractor as de
import pipeline.render_text_overlay as rto

ROOT = Path(__file__).resolve().parents[1]


def test_dialogue_extractor_import_does_not_load_torch():
    code = "import sys, pipeline.dialogue_extractor; print('torch' in sys.modules, 'simple_lama_inpainting' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "False False"


def test_get_lama_loads_model_once_across_threads(monkeypatch):
    created = []

    class FakeLama:
        def __init__(self, device=None):  # 실제 SimpleLama처럼 실행 장치를 받는다
            time.sleep(0.05)  # 동시에 들어온 스레드가 겹치도록 로드를 늦춘다.
            created.append(self)

    monkeypatch.setattr(de, "_lama", None)
    monkeypatch.setattr(de, "SimpleLama", FakeLama)
    monkeypatch.setattr(de, "_acquire_lama_process_slot", lambda: None)  # 테스트가 실제 슬롯을 잡지 않게
    results = []
    threads = [threading.Thread(target=lambda: results.append(de._get_lama())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    assert all(item is created[0] for item in results)


def test_get_lama_reports_missing_package(monkeypatch):
    monkeypatch.setattr(de, "_lama", None)
    monkeypatch.setattr(de, "SimpleLama", None)
    monkeypatch.setattr(de, "_load_simple_lama_class", lambda: None)
    try:
        de._get_lama()
    except RuntimeError as exc:
        assert "simple_lama_inpainting" in str(exc)
    else:
        raise AssertionError("RuntimeError expected")


def test_chained_pil_blocks_match_per_block_numpy_conversion():
    font = Path(rto.FONT_PATH)
    page = np.full((400, 300, 3), 235, dtype=np.uint8)
    page[50:150, 20:280] = (30, 60, 200)
    blocks = [
        ("안녕하세요 반갑습니다", [20, 40, 120, 140], dict(is_unboxed=False)),
        ("겹치는 두번째 블록", [60, 90, 150, 160], dict(is_unboxed=True, bg_alpha=200)),
        ("회전", [150, 250, 100, 100], dict(rotation_deg=15.0, text_stroke_width=2, text_stroke_fill=(255, 255, 255))),
    ]

    numpy_image = page.copy()
    for text, bbox, kwargs in blocks:
        numpy_image, _ = rto.draw_text_block(numpy_image, text, bbox, font, **kwargs)

    pil = Image.fromarray(cv2.cvtColor(page.copy(), cv2.COLOR_BGR2RGB))
    for text, bbox, kwargs in blocks:
        pil, _ = rto.draw_text_block_pil(pil, text, bbox, font, **kwargs)
    chained = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    assert np.array_equal(numpy_image, chained)


def test_tracking_cache_follows_global_offset(monkeypatch):
    font = rto.load_font(Path(rto.FONT_PATH), 24)
    base = rto.tracking_for_font(font)
    monkeypatch.setattr(rto, "_GLOBAL_TRACKING_OFFSET", rto._GLOBAL_TRACKING_OFFSET + 3)
    assert rto.tracking_for_font(font) == base + 3
