import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path
"""확인된 실제 작품의 같은 OCR 입력을 용어집 전/후로 비교한다. 원래 프로젝트는 수정하지 않는다."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from pipeline.gemini_ocr_translate_v5 import MODEL_NAME
from pipeline.manual_guidance import write_json
from pipeline.pipeline_runner import DEFAULT_FONT
from pipeline.render_text_overlay import apply_text_overlay
from pipeline.work_glossary import glossary_snapshot

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def region_rows(data):
    rows = {}
    for item in data.get('clusters', []):
        rid = str(item.get('source_region_id', item.get('sentence_id')))
        row = rows.setdefault(rid, {'original': [], 'translated': []})
        row['original'].append(item.get('text', ''))
        row['translated'].append(item.get('text_translated', ''))
    return {rid: {key: '\n'.join(value) for key, value in row.items()} for rid, row in rows.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', required=True)
    parser.add_argument('--pages', nargs='+', required=True)
    parser.add_argument('--glossary', required=True)
    parser.add_argument('--confirmed-title', required=True)
    parser.add_argument('--model', default=MODEL_NAME)
    parser.add_argument('--extra-prompt', default='')
    parser.add_argument('--variants', nargs='+', choices=['before', 'after'], default=['before', 'after'])
    parser.add_argument('--output-dir')
    args = parser.parse_args()
    source = PROJECTS_DIR / args.session
    glossary = json.loads(Path(args.glossary).read_text())
    if args.confirmed_title != glossary['work']['title'] or ('after' in args.variants and glossary['status'] != 'ready'):
        raise ValueError('사용자가 확인한 작품과 조사 완료된 용어집이 필요합니다.')
    context = glossary_snapshot(glossary)
    target = ROOT / args.output_dir if args.output_dir else PROOF_DIR / 'glossary_live' / f'{args.session}_{datetime.now():%Y%m%d_%H%M%S}'
    for folder in ('json', 'sheets', 'logs', 'final'):
        (target / folder).mkdir(parents=True, exist_ok=True)
    write_json(target / 'json/glossary.json', glossary)
    write_json(target / 'json/glossary_snapshot.json', context)
    results = []
    fingerprints = {}
    for page in args.pages:
        original = next((source/'input').glob(page + '.*'))
        tg = source / f'work/json/{page}_text_groups.json'
        regions = source / f'work/json/{page}_regions.json'
        sheet = source / f'work/sheets/{page}_indexed_sheet.jpg'
        if not sheet.exists():
            sheet = source / f'work/ocr_sheets/{page}_indexed_sheet.jpg'
        masked = source / f'work/masked/{page}_masked.jpg'
        for path in (original, tg, regions, sheet, masked):
            fingerprints[str(path)] = digest(path)
        local_sheet = target / 'sheets' / sheet.name
        shutil.copy2(sheet, local_sheet)
        local_regions = target / 'json' / regions.name
        shutil.copy2(regions, local_regions)
        local_tg = target / 'json' / tg.name
        shutil.copy2(tg, local_tg)
        variants = {}
        for variant in args.variants:
            output = target / 'json' / f'{page}_{variant}_integrated.json'
            prompt = '\n\n'.join(p for p in ([context['prompt'], args.extra_prompt] if variant == 'after' else [args.extra_prompt]) if p)
            cmd = [sys.executable, '-m', 'pipeline.gemini_ocr_translate_v5', str(original), str(local_tg), str(output),
                   '--ocr-sheet-image', str(local_sheet), '--ocr-sheet-json', str(local_regions), '--model', args.model,
                   '--extra-prompt=' + prompt]
            print(f'{page} {variant}: OCR/번역 시작', flush=True)
            with (target / 'logs' / f'{page}_{variant}.log').open('w') as log:
                subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=360, check=True)
            variants[variant] = json.loads(output.read_text())
            apply_text_overlay(masked, output, target / 'final' / f'{page}_{variant}_final.jpg',
                               regions_json=local_regions, font_path=Path(DEFAULT_FONT), original_image_path=original,
                               overlay_layout_output=target/'json'/f'{page}_{variant}_layout.json')
            print(f'{page} {variant}: 완료', flush=True)
        before_path = target / 'json' / f'{page}_before_integrated.json'
        after_path = target / 'json' / f'{page}_after_integrated.json'
        if not (before_path.exists() and after_path.exists()):
            continue
        before, after = region_rows(json.loads(before_path.read_text())), region_rows(json.loads(after_path.read_text()))
        for rid in sorted(set(before) | set(after), key=lambda value: int(value)):
            b, a = before.get(rid, {}), after.get(rid, {})
            results.append({'page': page, 'region': rid, 'before': b, 'after': a,
                            'ocr_changed': b.get('original') != a.get('original'),
                            'translation_changed': b.get('translated') != a.get('translated')})
    assert all(digest(Path(path)) == sha for path, sha in fingerprints.items()), '원래 입력이 변경됐습니다.'
    write_json(target / 'json/comparison.json', {'model': args.model, 'context': context, 'rows': results, 'source_sha256': fingerprints})
    lines = ['# 용어집 적용 전후 번역 비교', '', f'작품: {args.confirmed_title}', f'모델: {args.model}',
             f'자동/확정 적용 용어: {len(context["entries"])}개 · 버전 {context["version"]}', '',
             '같은 OCR sheet와 모델로 각각 새로 번역했다. 모델 응답의 변동도 포함되므로 차이를 모두 용어집 효과로 단정하지 않는다.', '',
             '| 페이지/영역 | 원문 | 적용 전 | 적용 후 | OCR 변경 |', '|---|---|---|---|---|']
    for row in results:
        cell = lambda text: str(text or '').replace('|', '\\|').replace('\n', '<br>')
        lines.append('| ' + ' | '.join(map(cell, [row['page']+'/R'+row['region'], row['after'].get('original'), row['before'].get('translated'), row['after'].get('translated'), '있음' if row['ocr_changed'] else '없음'])) + ' |')
    (target / 'comparison.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'output': str(target), 'rows': len(results), 'changed': sum(r['translation_changed'] for r in results), 'applied_terms': len(context['entries'])}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
