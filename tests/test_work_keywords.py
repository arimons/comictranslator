"""한국어 우선 키워드, 별칭·교정 분리, OCR/번역 전달 계약."""
from pipeline.manual_guidance import write_json
from pipeline.work_glossary import GlossaryStore, build_entries, glossary_snapshot


def result():
    return dict(format='korean-keywords-v1', work_matches=True, evidence=[
        dict(text='해롤드 스톡스\n해롤드라 부른다. 스토크스 표기도 있다.\n노먼\n코렛트',
             url='https://namu.wiki/w/work', title='작품'),
    ], entries=[
        dict(target='해롤드 스톡스', kind='인물', aliases=['해롤드'],
             variants=[dict(from_='invalid'), {'from':'스토크스','to':'스톡스'}],
             source_forms=[], ambiguous=False, evidence_indices=[999]),
        dict(target='노먼', kind='인물', aliases=[], variants=[], source_forms=[], ambiguous=False),
        dict(target='코렛트', kind='인물', aliases=[], variants=[], source_forms=[], ambiguous=True, note='동명이인 가능성'),
    ])


def test_korean_only_keywords_recover_citation_numbers_and_keep_uncertain_identity_review():
    entries = build_entries(result(), [])
    assert len(entries) == 3
    assert [e['status'] for e in entries] == ['auto', 'auto', 'review']
    assert all(e['evidence'] for e in entries)
    assert all(not e['source_forms'] for e in entries)
    snapshot = glossary_snapshot({'entries':entries})
    assert len(snapshot['entries']) == 2
    hero = snapshot['entries'][0]
    assert hero['aliases'] == ['해롤드']
    assert hero['variants'] == [{'from':'스토크스','to':'스톡스'}]
    assert '일본어 대응이 없는 항목도 참고' in snapshot['prompt']
    assert '전체 이름으로 늘리지' in snapshot['prompt']
    assert '화자·성격·말투를 추정하지' in snapshot['prompt']
    assert '동명이인 가능성' not in snapshot['prompt']


def test_absent_keywords_and_invented_aliases_are_not_saved():
    data = result()
    data['entries'][0]['aliases'].append('없는별명')
    data['entries'][0]['source_forms'] = ['資料にない名前']
    data['entries'].append(dict(target='없는인물', ambiguous=False))
    entries = build_entries(data, [])
    assert len(entries) == 3 and entries[0]['aliases'] == ['해롤드']
    assert entries[0]['source_forms'] == []


def test_legacy_short_name_merges_into_alias_and_manual_choice_survives(tmp_path):
    old = dict(source='ハロルド', target='해롤드', id='legacy', status='auto')
    entries = build_entries(result(), [old])
    assert len(entries) == 3
    assert entries[0]['source_forms'] == ['ハロルド']
    service = GlossaryStore(tmp_path)
    try:
        wid = service.confirm({'title':'작품'})
        data = service.read(wid)
        data.update(entries=entries)
        write_json(service.path(wid), data)
        chosen = service.review(wid, entries[0]['id'], 'accepted', '해럴드 스톡스')
        assert chosen['entries'][1]['status'] == 'auto'  # 일본어 없는 다른 항목을 제외하지 않는다.
        rebuilt = build_entries(result(), chosen['entries'])
        assert len(rebuilt) == 3
        assert any(e['target']=='해럴드 스톡스' and e['status']=='accepted' for e in rebuilt)
    finally:
        service.pool.shutdown()
