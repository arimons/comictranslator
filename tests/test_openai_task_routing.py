"""모델 자동 라우팅·재시도 비용·구조화 출력 계약."""
from types import SimpleNamespace as NS
import json

import pytest
import openai
from pipeline.openai_tasks import route_task, response_json, SOL_MODEL, LUNA_MODEL


def test_routes_by_workload_without_classifier_calls():
    assert route_task('glossary', text_chars=1000, source_count=1)['model'] == LUNA_MODEL
    assert route_task('glossary', text_chars=12001, source_count=1)['model'] == SOL_MODEL
    assert route_task('glossary', text_chars=5000, source_count=4)['model'] == SOL_MODEL
    assert route_task('ocr_translate')['model'] == SOL_MODEL
    assert route_task('image_translation_guide')['model'] == SOL_MODEL
    assert route_task('glossary', override=SOL_MODEL)['model'] == SOL_MODEL
    with pytest.raises(ValueError): route_task('glossary', override='gemini-3-flash-preview')


def fake_provider(monkeypatch, responses):
    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['max_retries'] == 0
            self.responses = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            calls.append(kwargs)
            value = responses.pop(0)
            if isinstance(value, Exception): raise value
            return NS(output_text=value, status='completed')
    monkeypatch.setenv('OPENAI_API_KEY','test-key')
    monkeypatch.setattr(openai,'OpenAI',Client)
    return calls


def test_luna_format_failure_escalates_once_and_records_model(monkeypatch):
    calls = fake_provider(monkeypatch, ['invalid JSON', '{"entries":[]}'])
    result, route = response_json('JSON only', route_task('glossary'), validator=lambda v:isinstance(v.get('entries'),list))
    assert [call['model'] for call in calls] == [LUNA_MODEL,SOL_MODEL]
    assert route['attempted_models'] == [LUNA_MODEL,SOL_MODEL]
    assert route['model'] == SOL_MODEL
    assert calls[0]['reasoning']['effort'] == 'low'
    assert not calls[0]['store'] and 'temperature' not in calls[0] and 'tools' not in calls[0]


def test_credit_or_auth_errors_do_not_trigger_another_model(monkeypatch):
    calls = fake_provider(monkeypatch, [RuntimeError('quota')])
    with pytest.raises(RuntimeError): response_json('JSON',route_task('glossary'))
    assert len(calls) == 1


def test_valid_empty_entries_do_not_trigger_paid_escalation(monkeypatch):
    calls = fake_provider(monkeypatch,['{"work_matches":true,"entries":[]}'])
    value, route = response_json('JSON',route_task('glossary'))
    assert len(calls)==1 and value['entries']==[]
