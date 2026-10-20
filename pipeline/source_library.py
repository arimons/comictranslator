"""원본 라이브러리 참조와 호스트별 재연결. 분석 캐시와 페이지 ID는 변경하지 않는다."""
from __future__ import annotations
from collections import OrderedDict
import hashlib
import copy
import json
import os
from pathlib import Path
import platform
import threading
from uuid import uuid4
from pipeline.manual_guidance import load_json, write_json
from pipeline.project_paths import manifest_files, manifest_path

EXTENSIONS={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff'}
_HASHES=OrderedDict()
_LOCK=threading.RLock()


def digest(path):
    path=Path(path).resolve();s=path.stat();key=(str(path),s.st_size,s.st_mtime_ns,s.st_ino)
    with _LOCK:
        if key in _HASHES:return _HASHES[key]
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    after=path.stat()
    if (s.st_size,s.st_mtime_ns,s.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise ValueError('원본이 변경 중입니다')
    with _LOCK:
        _HASHES[key]=h.hexdigest()
        if len(_HASHES)>8192:_HASHES.popitem(last=False)
    return h.hexdigest()


def derived(item):
    return item.get('input_storage')=='derived' or item.get('split_policy') in {'split','center_crop','crop'}


def atomic_link(target,alias):
    alias=Path(alias);target=Path(target)
    alias.parent.mkdir(parents=True,exist_ok=True)
    try:
        if alias.is_symlink() and alias.resolve()==target.resolve():return
    except OSError:pass
    if alias.exists() and not alias.is_symlink():raise ValueError('프로젝트 소유 입력은 먼저 보존해야 합니다')
    temporary=alias.with_name('.'+alias.name+'.'+uuid4().hex+'.source-link')
    try:
        temporary.symlink_to(os.path.relpath(target.resolve(),alias.parent.resolve()))
        os.replace(temporary,alias)
    finally:temporary.unlink(missing_ok=True)


class SourceLibrary:
    def __init__(self,home,host_id=None):
        self.home=Path(home).absolute()
        host_id=host_id or os.environ.get('COMIC_TRANSLATOR_HOST_ID') or platform.node()
        host=hashlib.sha256(host_id.encode()).hexdigest()[:20]
        self.registry=self.home/'local/hosts'/host/'source_roots.json'

    def roots(self):
        return load_json(self.registry,{}).get('roots',{})

    def register(self,root,preferred_id=None):
        root=Path(root).expanduser().resolve()
        if not root.is_dir():raise ValueError('원본 폴더가 없습니다')
        with _LOCK:
            roots=self.roots()
            key=preferred_id or next((k for k,v in roots.items() if v['path']==str(root)),None) or uuid4().hex[:24]
            if key not in roots or roots[key]['path']!=str(root):
                roots[key]={'path':str(root),'label':root.name}
                write_json(self.registry,{'version':1,'roots':roots})
        return key

    def target(self,ref,roots=None):
        root=(roots if roots is not None else self.roots()).get(ref.get('root_id'))
        if not root:return None
        base=Path(root['path']);relative=Path(ref.get('relative_path',''))
        if relative.is_absolute() or '..' in relative.parts:return None
        path=base/relative
        if not path.resolve().is_relative_to(base.resolve()):return None
        return path

    def refresh(self,data,base):
        """원본 경로가 달라져도 입력 별칭을 유지한다. 정상 상태의 반복 조회는 쓰지 않는다."""
        roots=self.roots()
        for item in data.get('images',[]):
            ref=item.get('source_ref')
            if not ref:continue
            alias=Path(item['input_path'])
            if not alias.absolute().is_relative_to((Path(base)/'input').absolute()) or not alias.parent.resolve().is_relative_to(Path(base).resolve()):
                raise ValueError('입력 별칭이 프로젝트 입력 폴더 밖에 있습니다')
            expected=item.get('image_sha256')
            candidate=self.target(ref,roots);target=None;status='missing'
            try:
                if candidate and candidate.is_file() and digest(candidate)==expected:target=candidate;status='connected'
            except (OSError,ValueError):pass
            if target is None:
                fallback=item.get('source_fallback_path')
                try:
                    if fallback and Path(fallback).is_file() and digest(Path(fallback))==expected:target=Path(fallback);status='fallback'
                except (OSError,ValueError):pass
            if target is None:
                # 변경된 원본에 예전 CV/분석을 잘못 붙이지 않도록 읽을 수 없는 프로젝트 내부 별칭으로 바꾼다.
                target=Path(base)/'input/.source-unavailable'/item['id']
            atomic_link(target,alias)
            item['source_status']=status
        return data

    def allowed_source(self,path,projects=None):
        path=Path(path).resolve()
        roots=self.roots()
        # 외부 라이브러리의 다른 이미지까지 공개하지 않고 등록된 페이지 파일만 허용한다.
        for metadata in manifest_files(Path(projects or self.home/'projects')):
            data=load_json(metadata,{})
            for item in data.get('images',[]):
                ref=item.get('source_ref')
                if not ref or ref.get('root_id') not in roots:continue
                base=Path(roots[ref["root_id"]]["path"])
                if not path.is_relative_to(base.resolve()):continue
                candidate=self.target(ref,roots)
                try:
                    if candidate and candidate.resolve()==path and digest(path)==item.get('image_sha256'):return True
                except (OSError,ValueError):continue
        return False

    def preview(self,data,folder):
        folder=Path(folder).expanduser().resolve()
        if not folder.is_dir():raise ValueError('원본 폴더가 없습니다')
        files=[p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in EXTENSIONS and p.resolve().is_relative_to(folder)]
        by_name={}
        for p in files:by_name.setdefault(p.name,[]).append(p)
        by_hash=None;pages=[]
        for item in data.get('images',[]):
            if derived(item):pages.append({'image_id':item['id'],'status':'owned'});continue
            expected=item.get('image_sha256')
            if not expected:
                pages.append({'image_id':item['id'],'status':'no_hash'});continue
            options=by_name.get(item.get('parent_name') or item.get('name',''),[])
            matches=[p for p in options if digest(p)==expected]
            if not matches:
                if by_hash is None:
                    by_hash={}
                    for p in files:by_hash.setdefault(digest(p),[]).append(p)
                matches=by_hash.get(expected,[])
            if matches:
                match=sorted(matches,key=lambda p:str(p))[0]
                pages.append({'image_id':item['id'],'status':'matched','relative_path':str(match.relative_to(folder)),'sha256':expected})
            else:pages.append({'image_id':item['id'],'status':'unmatched'})
        revision=hashlib.sha256(json.dumps({'folder':str(folder),'images':data.get('images',[]),'pages':pages},sort_keys=True).encode()).hexdigest()
        return {'folder':str(folder),'revision':revision,'pages':pages,'matched':sum(p['status']=='matched' for p in pages),'owned':sum(p['status']=='owned' for p in pages),'unmatched':sum(p['status'] in {'unmatched','no_hash'} for p in pages)}

    def apply(self,data,base,plan):
        with _LOCK:
            snapshot=copy.deepcopy(data)
            aliases=[]
            for item in data.get('images',[]):
                alias=Path(item['input_path'])
                aliases.append((alias, str(alias.readlink()) if alias.is_symlink() else None, alias.is_file() and not alias.is_symlink(), (Path(base)/"input_legacy"/alias.name).exists()))
            try:
                return self._apply(data,base,plan)
            except Exception:
                for (alias,link,physical,had_backup),old in zip(aliases,snapshot.get('images',[])):
                    if not alias.absolute().is_relative_to((Path(base)/'input').absolute()):continue
                    if physical:
                        moved=Path(base)/'input_legacy'/alias.name
                        if moved.exists() and not had_backup:
                            if alias.is_symlink():alias.unlink()
                            moved.replace(alias)
                    elif link is not None:
                        temp=alias.with_name('.'+alias.name+'.rollback-link')
                        temp.symlink_to(link);os.replace(temp,alias)
                    elif alias.is_symlink():alias.unlink()
                data.clear();data.update(snapshot)
                raise

    def _apply(self,data,base,plan):
        # 원본 변경/프로젝트 변경은 쓰기 전에 확인한다.
        current=self.preview(data,plan['folder'])
        if current['revision']!=plan['revision']:raise ValueError('원본 또는 프로젝트가 바뀌었습니다. 미리보기를 다시 확인하세요')
        if not plan['matched']:raise ValueError('동일 해시의 원본을 찾지 못했습니다. 기존 연결은 유지합니다')
        base=Path(base)
        history=base/'work/source_history'/uuid4().hex
        history.mkdir(parents=True)
        metadata=manifest_path(base)
        (history/'manifest-before.json').write_bytes(metadata.read_bytes())
        write_json(history/'links-before.json', {p['id']:{'link':str(Path(p['input_path']).readlink()) if Path(p['input_path']).is_symlink() else None,
            'owned_backup':'input_legacy/'+Path(p['input_path']).name} for p in data.get('images',[])})
        existing={p['id']:p.get('source_ref') for p in data.get('images',[])}
        previous=[existing[p['image_id']] for p in plan['pages'] if p['status']=='matched' and existing.get(p['image_id'])]
        same_layout=previous and all(existing[p['image_id']]['relative_path']==p['relative_path'] for p in plan['pages'] if p['status']=='matched' and existing.get(p['image_id']))
        ids={r['root_id'] for r in previous}
        preferred=next(iter(ids)) if same_layout and len(ids)==1 else None
        root_id=self.register(plan['folder'],preferred)
        matches={p['image_id']:p for p in plan['pages'] if p['status']=='matched'}
        for item in data.get('images',[]):
            match=matches.get(item['id'])
            if not match:continue
            alias=Path(item['input_path'])
            if not alias.absolute().is_relative_to((base/'input').absolute()) or not alias.parent.resolve().is_relative_to(base.resolve()):
                raise ValueError('입력 별칭이 프로젝트 입력 폴더 밖에 있습니다')
            if not item.get('source_ref') and not item.get('source_fallback_path') and alias.exists():
                if alias.is_symlink():item['source_fallback_path']=str(alias.resolve())
                else:
                    fallback=base/'input_legacy'/alias.name
                    fallback.parent.mkdir(parents=True,exist_ok=True)
                    if fallback.exists():raise ValueError('입력 보존 파일이 이미 있습니다')
                    alias.rename(fallback);item['source_fallback_path']=str(fallback)
            item['source_ref']={'root_id':root_id,'relative_path':match['relative_path']}
            item['input_storage']='source'
        self.refresh(data,base)
        clean={**data,'images':[{k:v for k,v in p.items() if k!='source_status'} for p in data['images']]}
        write_json(base/'project.json',clean)
        return current
