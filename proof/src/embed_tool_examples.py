"""현재 도구의 실제 전/후 캡처와 코드에서 확인한 동작을 매뉴얼에 내장한다."""
from pathlib import Path
import base64,html,io,json,re
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]; CAP=ROOT/'proof/misc/tool_examples'; MANUAL=ROOT/'docs/editor_manual.html'

def figure(key,phase,title):
 with Image.open(CAP/f'{key}-{phase}.png') as im:
  b=io.BytesIO();im.convert('RGB').save(b,'JPEG',quality=83,optimize=True)
  data=base64.b64encode(b.getvalue()).decode()
 return f'<figure style="margin:12px 0"><img src="data:image/jpeg;base64,{data}" width="1600" height="1060" loading="lazy" style="width:100%;height:auto" alt="{html.escape(title)}"><figcaption>{html.escape(title)}</figcaption></figure>'

def main():
 entries=[
 ('d','D · 선택한 TG 제외','번역에서 빼려는 TG를 V로 선택한 뒤 D를 누릅니다. D는 모드 진입키가 아니며 선택이 없으면 아무 항목도 제외하지 않습니다.','00006.jpg의 TG2를 선택해 제외했습니다. 상자와 연결된 자동 R은 즉시 숨겨지고, Cmd+Z로 복원됩니다. 분석 적용 후 유효 TG가 8개에서 7개가 됩니다.'),
 ('t','T · TG 분할','BFS로 만든 묶음을 수동으로 두 묶음으로 수정할 때 사용합니다. TG를 선택하고 조각 사이로 선을 긋습니다. 말풍선 마스크를 자르는 도구가 아닙니다.','00006.jpg의 TG7에 세로선을 그었습니다. 이 예시는 조작 검증을 위해 정상 대사를 인위적으로 나눈 것입니다. 원래 분리해야 하는 대사라는 뜻은 아닙니다. 유효 TG는 8→9개가 됐지만 TG7·TG9가 같은 R에 연결되어 R 개수는 7개로 유지됐습니다.'),
 ('e','E · Edge 장벽','경계 기반 검출의 범위를 제한하려고 사각형을 드래그합니다. 현재 E는 자유선이 아니라 네 변으로 된 가상 벽을 만듭니다.','00006.jpg 가운데 왼쪽 위 텍스트 주위에 박스를 추가했습니다. 적용 전 빨간 벽과 적용 후 실제 R 범위를 비교합니다. 이 박스가 적절한 경계라는 품질 판정은 하지 않습니다.'),
 ('b','B · 경계 기반 시작점','말풍선 내부에 검출 시작점이 필요한 경우 클릭합니다. 사각형을 그리는 동작이 아닙니다.','00006.jpg 오른쪽 위 말풍선의 흰 내부를 클릭했습니다. 기존 자동 영역과 수동 시작점 적용 후 영역을 비교합니다. 이미 검출된 영역에 찍는 조작 확인 예시입니다.'),
 ('s','S · 영역 분할','붙은 말풍선의 목 부분에 힌트 선을 긋습니다. 구현은 선 주변의 변곡점 후보를 찾아 실제 분리선을 보정합니다. 찾지 못하면 사각형·anchor 기반 fallback을 시도하므로, 입력 선을 그대로 칼처럼 자른다고 보장하지 않습니다.','00008.jpg 오른쪽 위 공유 영역에 선을 넣어 적용했습니다. 이 위치는 목이 있는 두 말풍선의 정답 예시가 아닙니다. 이번 실행은 변곡점 대신 manual_split_rect_fallback 경로를 사용했고, TG2·TG5를 담던 공유 R 하나가 두 개가 됐습니다. 변곡점 분할 성공 예시로 해석하지 않습니다.'),
 ('u','U · 텍스트 기반 영역','경계로 내부를 확보하기 어려운 글자의 범위를 사각형으로 지정합니다. 흰 바탕은 Telea, 복잡한 배경은 LaMa 등 기존 경로가 처리합니다.','00006.jpg의 작은 점 세 개 주변에 범위를 지정했습니다. 적용 전 가이드와 적용 후 텍스트 기반 R을 비교합니다. 박스 전체를 흰색으로 칠하는 기능이 아닙니다.'),
 ]
 content='''<h3 id="guidance-examples">실제 도구 조작 전·후</h3>
 <p>도구 순서는 <strong>V·H / D·T / E·B·S·U</strong>입니다. V는 TG·R 선택으로 돌아가는 기본 모드, H는 화면 이동 모드입니다. D는 선택에 대한 명령이고 나머지는 가이드를 입력하는 모드입니다.</p>
 <p class="note warning">원본 프로젝트를 보존한 별도 검증 복사본에서 촬영했습니다. 기존 CV·TG 결과를 사용하고 로컬 Step 3만 실행했습니다. 유료 CV·OCR·번역·이미지 생성은 실행하지 않았습니다. 아래는 도구의 실제 관찰 결과이며 분류 정확도·대사 분리 위치의 정답 예시는 아닙니다.</p>
 <h3>TG를 묶는 BFS와 T / S의 차이</h3>
 <p>CV 텍스트 조각 → TextLine(정렬·거리 병합과 Repair) → BFS TextGroup → 보수적인 X방향 확장 순서로 묶습니다. BFS는 거리·정렬 조건을 만족하는 연결을 따라갑니다. A-B, B-C가 연결되면 A와 C가 직접 조건을 만족하지 않아도 한 TG가 될 수 있습니다. 말풍선 의미를 인식하는 연결 규칙은 아닙니다.</p>
 <p><strong>T는 이 BFS 결과를 수정하는 후처리</strong>입니다. 선택한 TG 안의 fragment 중심점이 사용자가 그은 직선의 어느 쪽에 있는지로 두 묶음을 만듭니다. fragment는 CV 텍스트 조각이며 한 글자 단위로 항상 잘리지는 않습니다. 양쪽 모두 조각이 있어야 분리되고, 그렇지 않으면 원래 TG를 유지합니다. 오른쪽 묶음은 기존 ID, 다른 쪽은 새 TG ID를 받습니다. 선에서 3px 이내인 조각은 별도 <code>manual_orphans</code>로 기록되며 유효 TG에서 빠질 수 있으므로 글자 사이에 그은 뒤 누락을 확인해야 합니다. BFS를 자동으로 다시 돌려 잘못 합친 원인을 추론하는 기능이 아닙니다.</p>
 <p><strong>S는 TG가 아닌 처리 영역의 분리 힌트</strong>입니다. T로 두 TG를 만들어도 같은 말풍선으로 판정되면 하나의 R에 묶일 수 있습니다. 반대로 S는 TG 묶음을 다시 만드는 도구가 아닙니다.</p>'''
 for key,title,when,action in entries:
  result=json.loads((CAP/f'example_{key}-result.json').read_text());rep=result['report'];regions=result['regions']['regions']
  methods={}
  for r in regions:
   method=r.get('source_method','unknown');methods[method]=methods.get(method,0)+1
  metric=f"유효 TG {rep['source_count']} → {rep['effective_count']}개 · 적용 후 R {len(regions)}개. 생성 경로: "+', '.join(f'{k} {v}개' for k,v in methods.items())
  content+=f'<article><h3>{title}</h3><p><strong>언제·어떻게:</strong> {when}</p><p><strong>이번 조작:</strong> {action}</p><p><strong>실행 기록:</strong> {html.escape(metric)}</p>'
  content+='<details><summary>① 조작 전 → ② 미적용 가이드 → ③ 분석 적용 후</summary>'
  for phase,label in [('before','조작 전'),('guide','D는 즉시 미리보기, 나머지는 가이드 추가 직후 · 분석 적용 전'),('after','Cmd+Enter로 로컬 분석 적용 후')]:content+=figure(key,phase,f'{title} — {label}')
  if key=='d':content+=figure(key,'undo','Cmd+Z로 즉시 복원 · 재분석 없음')
  content+='</details></article>'
 content+='''<p>적용 후에는 R 외곽선뿐 아니라 상단의 <strong>마스크</strong> 보기와 OCR sheet도 확인합니다. 새 분석 결과를 번역·최종 출력에 쓰려면 이후 단계를 따로 실행합니다. E·B·S·U는 자동 적용하지 않으며 Cmd/Ctrl+Enter로 모아서 적용합니다.</p>'''
 s=MANUAL.read_text();s=re.sub(r'<!-- examples:guidance:start -->.*?<!-- examples:guidance:end -->','<!-- examples:guidance:start -->\n'+content+'\n<!-- examples:guidance:end -->',s,flags=re.S)
 s=s.replace('<kbd>D</kbd> 도구. 선택한 TG는 <kbd>Delete</kbd>로 바로 끕니다.','선택한 TG를 <kbd>D</kbd> 또는 <kbd>Delete</kbd>로 바로 제외합니다. 모드 전환은 하지 않습니다.')
 s=s.replace('하나의 텍스트 그룹이 여러 대사로 붙었을 때 분리 기준선을 긋습니다.','BFS로 만든 TG에 수동 분리선을 긋습니다. 선 양쪽의 텍스트 조각으로 유효 TG 두 개를 만들며, 말풍선 마스크 분할은 S를 사용합니다.')
 # 단축키 도움말도 E B S U 순서로 맞춘다.
 row=re.search(r'\s*<tr data-shortcut="guidance.tool.bubble".*?</tr>',s).group();s=s.replace(row,'');at=s.index('<tr data-shortcut="guidance.tool.region-split"');s=s[:at]+row.strip().replace('경계 기반 seed','경계 기반 시작점')+'\n          '+s[at:]
 MANUAL.write_text(s)

if __name__=='__main__':main()
