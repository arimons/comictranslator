"""선택 배치 SDK와 모델 캐시 경계를 네트워크 없이 확인한다."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import os
from pipeline import paths


def test_model_cache_defaults_and_explicit_overrides(tmp_path,monkeypatch):
    model=tmp_path/'torch/hub/checkpoints/big-lama.pt';model.parent.mkdir(parents=True);model.write_bytes(b'fixture')
    monkeypatch.setattr(paths,'MODEL_CACHE_DIR',tmp_path)
    monkeypatch.setattr(paths,'LAMA_MODEL_PATH',model)
    monkeypatch.delenv('TORCH_HOME',raising=False);monkeypatch.delenv('LAMA_MODEL',raising=False)
    paths.configure_model_cache()
    assert os.environ['TORCH_HOME']==str(tmp_path/'torch')
    assert os.environ['LAMA_MODEL']==str(model)
    monkeypatch.setenv('LAMA_MODEL','/custom/model.pt');monkeypatch.setenv('TORCH_HOME','/custom/cache')
    paths.configure_model_cache()
    assert os.environ['LAMA_MODEL']=='/custom/model.pt' and os.environ['TORCH_HOME']=='/custom/cache'


def test_batch_upload_new_sdk_keeps_file_mime_without_network(monkeypatch):
    import pytest
    pytest.importorskip('google.genai')
    path=Path(__file__).resolve().parents[1]/'batch/submit_batch_folder.py'
    spec=importlib.util.spec_from_file_location('batch_submit_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[];returned=object()
    monkeypatch.setattr(module,'_CLIENT',SimpleNamespace(files=SimpleNamespace(upload=lambda **kw:(calls.append(kw),returned)[1])))
    monkeypatch.setattr(module,'API_KEY','fixture-key')
    assert module.upload_with_retry(Path('requests.jsonl'),mime_type='application/jsonl') is returned
    assert calls[0]['file']=='requests.jsonl' and calls[0]['config'].mime_type=='application/jsonl'
