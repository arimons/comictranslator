"""실제 앱 캡처를 매뉴얼에 내장한다. 기존 디자인과 본문은 유지한다."""
from pathlib import Path
import base64
import html
import io
import re

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / "proof/misc/editor_manual"
MANUAL = ROOT / "docs/editor_manual.html"


def figure(name, caption, folded=False):
    with Image.open(CAPTURES / name) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
        width, height = image.size
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    content = (f'<figure style="margin:16px 0"><img src="data:image/jpeg;base64,{data}" '
               f'width="{width}" height="{height}" loading="lazy" style="display:block;width:100%;height:auto" '
               f'alt="{html.escape(caption, quote=True)}"><figcaption>{html.escape(caption)}</figcaption></figure>')
    if folded:
        return '<details><summary>실제 앱 조작 화면 펼치기</summary>' + content + '</details>'
    return content


def insert_before(text, marker, key, content):
    start, end = f"<!-- examples:{key}:start -->", f"<!-- examples:{key}:end -->"
    text = re.sub(re.escape(start) + r".*?" + re.escape(end) + r"(?:\r?\n)*", "", text, flags=re.S)
    assert marker in text, marker
    return text.replace(marker, f"{start}\n{content}\n{end}\n\n{marker}", 1)


def main():
    text = MANUAL.read_text()
    if 'href="#region-types"' not in text:
        text = text.replace('<a href="#guidance">', '<a href="#region-types">R / U와 도구 선택</a>\n    <a href="#guidance">', 1)

    taxonomy = '''<section id="region-types">
      <h2>R 번호와 처리 기준 배지를 읽습니다</h2>
      <p>모든 결과 영역은 <strong>R1, R2, R3…</strong>로 표시하고, 옆의 배지로 <strong>경계 기반 / 텍스트 기반</strong>을 구분합니다.
      <strong>Edge는 세 번째 타입이 아닙니다.</strong> 일반 FloodFill과 Edge barrier는 경계 기반 영역을 만드는 방법입니다.
      TG는 앞 단계의 텍스트 그룹이며, R 번호는 그 결과 영역의 ID입니다.</p>
      <table><thead><tr><th>분석 화면 표시</th><th>뜻</th><th>처리 방법과의 관계</th></tr></thead><tbody>
        <tr><td>R 번호 + 경계 기반</td><td>검출한 경계나 수동 Edge를 기준으로 처리 범위를 확보하는 영역</td><td>일반 FloodFill, Edge barrier 검출·구제 등이 포함됩니다. 처리 기준은 <code>boundary_based</code>입니다.</td></tr>
        <tr><td>R 번호 + 텍스트 기반</td><td>텍스트 위치·획을 중심으로 처리 범위를 구성하는 영역</td><td>경계를 확보하기 어려운 대상도 포함합니다. 처리 기준은 <code>text_based</code>입니다.</td></tr>
      </tbody></table>
      <p class="note">배지는 처리 기준이며, “실제 말풍선인가”라는 시각 판정이나 품질 합격 표시가 아닙니다.
      원본 경계·마스크·OCR sheet를 함께 봅니다. <code>processing_basis</code>는 새 처리 기준이고 <code>source_method</code>는 검출 방법입니다.</p>
      <p>기존 JSON·캐시와 호환되도록 저장된 <code>type: bubble</code>은 경계 기반, <code>type: unboxed</code>는 텍스트 기반으로 읽습니다.
      과거 좌표 키·캐시·가이드 ID는 유지합니다. 기존 OCR sheet·분석 이미지에 남은 U 번호는 과거 표기이며, 현재 앱에서는 같은 영역을 R 번호와 배지로 표시합니다.
      단축키 B와 U도 그대로입니다.</p>
      <h3>실제 저장 결과 읽기 — 00008.jpg</h3>
      <p>오른쪽 분석 화면에서 모든 ID가 R로 표시되고, 배지가 처리 기준을 보여 줍니다. 분류 정확도를 확정한 정답 예시는 아닙니다.</p>
      {{taxonomy-image}}
      <table><thead><tr><th>저장된 영역</th><th>type</th><th>source_method</th></tr></thead><tbody>
        <tr><td>R1·R2·R3 등</td><td><code>bubble</code></td><td><code>flood</code></td></tr>
        <tr><td>R5·R6</td><td><code>bubble</code></td><td><code>manual_edge_barrier_rescue</code></td></tr>
        <tr><td>R4 · 텍스트 기반</td><td><code>unboxed</code></td><td><code>bbox_fallback</code></td></tr>
      </tbody></table>
      <h3>어느 도구부터 볼까요?</h3>
      <p>아래는 도구를 고르기 위한 질문입니다. 한 화면의 표시만 보고 자동으로 수정을 확정하는 규칙은 아닙니다.</p>
      <table><thead><tr><th>확인할 문제</th><th>먼저 검토할 도구</th><th>적용 후 확인</th></tr></thead><tbody>
        <tr><td>번역에서 제외할 원본 텍스트 그룹인가?</td><td>TG Off · D</td><td>필요한 대사까지 빠지지 않았는지</td></tr>
        <tr><td>서로 다른 대사가 TG 하나에 묶였나?</td><td>TG Split · T</td><td>유효 TG와 OCR sheet의 단위가 나뉘었는지</td></tr>
        <tr><td>TG는 나뉘었는데 공유 영역이 붙어 있나?</td><td>Region Split · L</td><td>분리된 R, 마스크, 대사 대응</td></tr>
        <tr><td>R로 잡을 수 있는 경계가 있고 검출 시작점이 필요한가?</td><td>Bubble seed · B</td><td>seed가 만든 영역이 의도한 내부에 머무는지</td></tr>
        <tr><td>R을 만들 때 경계가 열려 영역이 새나?</td><td>Edge · E</td><td>다시 얻은 영역과 인접 영역의 영향</td></tr>
        <tr><td>경계를 확보하기 어려워 텍스트 중심으로 범위를 지정해야 하나?</td><td>텍스트 기반 영역 · U</td><td>글자 누락, 선화·망점 손상</td></tr>
        <tr><td>TG가 아니라 자동 결과 영역 자체를 제외할 것인가?</td><td>Region Off · X</td><td>대상 R/U와 다른 결과 영역의 변화</td></tr>
        <tr><td>영역은 맞고 번역문·줄바꿈·배치가 문제인가?</td><td>글자 입히기</td><td>원문과 뜻, 잘림, 말풍선 경계</td></tr>
        <tr><td>흰 바탕의 작은 잔상인가, 배경 무늬 위 잔상인가?</td><td>흰 바탕은 Overlay 지우개, 배경 무늬는 배경 복원 검토</td><td>원문 흔적과 배경 손상을 함께 확인</td></tr>
      </tbody></table>
    </section>'''
    taxonomy = taxonomy.replace('{{taxonomy-image}}', figure('02_edge.png', '00008.jpg: R5·R6은 Edge barrier로 얻은 경계 기반 영역, R4는 텍스트 기반 영역입니다.'))
    taxonomy = taxonomy.replace('manual_edge_barrier_rescue</code>', 'manual_<wbr>edge_<wbr>barrier_<wbr>rescue</code>')
    taxonomy = taxonomy.replace('<table>', '<div style="overflow-x:auto"><table>').replace('</table>', '</table></div>')
    text = insert_before(text, '    <section id="guidance">', 'taxonomy', taxonomy)

    text = text.replace('말풍선 영역은 Bubble로, 말풍선 밖 일반 대사는 Ubox로 지정합니다. 작은 클릭은 seed point, 드래그는 box guide가 됩니다.',
                        'R(Bubble) 검출의 시작점을 추가할 때는 Bubble을 클릭합니다. 현재 Bubble 도구는 클릭 seed 방식입니다. 경계가 애매한 U(Unboxed) 대상의 글자 범위를 직접 잡을 때는 Ubox로 사각형을 드래그합니다.')
    old = '''            <td><span class="swatch blue"></span>파란 rect</td>
            <td>자동 인식된 region 또는 선택된 분석 대상입니다.</td>
            <td>결과가 맞으면 그대로 둡니다.</td>'''
    new = '''            <td>파란 TG 박스 / 청록 R / 갈색 점선 U</td>
            <td>TG는 원본 텍스트 그룹, R은 Bubble, U는 Unboxed 결과입니다.</td>
            <td>표시 색과 함께 TG·R·U 라벨을 읽습니다. 타입과 검출 방법을 혼동하지 않습니다.</td>'''
    text = text.replace(old, new)
    text = text.replace('주황 split', '갈색 split').replace('같은 bubble 안에서 대사가 두 개 이상으로 갈라질 때 사용합니다.', 'TG 분리선과 Region 분리선을 도구 이름으로 구분합니다. 선을 그린 뒤 Apply Analysis 결과를 확인합니다.')

    examples = '''<h3 id="guidance-examples">실제 조작 예시 — 무엇을 바꾸는 도구인가</h3>
      <p class="note warning">아래 00006.jpg 화면은 복사본에서 도구를 조작한 예시입니다.
      원래 대사를 분리·제외해야 한다는 뜻이 아닙니다. 가이드를 그린 화면은 적용 전 상태이며,
      해당 위치의 보정 효과나 R/U 분류의 정답을 검증한 사례로 사용하지 않습니다.</p>'''
    cases = [
        ('TG Off · 원본 텍스트 그룹을 제외', '번역하지 않을 글자나 잘못 감지한 TG를 입력에서 제외하려고 할 때 검토합니다.',
         'D를 고르고 TG2를 클릭했습니다. Action History에 TG2 off가 생기는 것을 확인했습니다. Undo로 되돌릴 수 있습니다.',
         'Apply Analysis 후 필요한 원문이 OCR sheet에서 함께 빠지지 않았는지 봅니다. 결과 영역만 제외하려는 경우에는 Region Off와 구분합니다.', '03_tg_off.png'),
        ('TG Split · 텍스트 묶음 자체를 나누기', '서로 다른 대사가 한 TG로 묶였을 때 검토합니다.',
         'T를 고르고 TG2를 가로지르는 선을 그었습니다. TG2 split 이력이 생겼습니다. 이 선은 조작 설명용이며 실제 대사의 분리 기준이 아닙니다.',
         'Apply Analysis 뒤 유효 TG와 OCR sheet에서 대사 단위가 나뉘었는지 확인합니다. 선이 보인다는 사실만으로 분리 성공을 판단하지 않습니다.', '04_tg_split.png'),
        ('Region Split · 공유된 영역을 나누기', 'TG가 이미 따로 있는데 검출된 Bubble 영역을 분리할 필요가 있을 때 검토합니다.',
         'L을 고르고 화면 가운데 왼쪽 영역에 분리선을 그었습니다. 이력에는 Split line으로 표시됩니다. TG Split은 TG를, 이 도구는 영역 분리를 대상으로 합니다.',
         'Apply Analysis 뒤 영역과 OCR sheet, 인접 대사 대응을 확인합니다. 그림의 선은 분리 위치의 정답 예시가 아닙니다.', '05_region_split.png'),
        ('Bubble · R 검출 시작점 추가', 'Bubble로 처리할 내부가 보이는데 자동 검출이 놓친 경우 seed 추가를 검토합니다.',
         'B를 고르고 가운데 왼쪽 흰 영역을 한 번 클릭해 Seed를 추가했습니다. 현재 도구는 사각형을 드래그하는 방식이 아닙니다.',
         'Apply Analysis 후 생긴 영역이 의도한 내부에 머무는지 확인합니다. 경계가 열려 있으면 seed만 늘리지 말고 Edge 필요 여부를 검토합니다.', '06_bubble.png'),
        ('Ubox · U 대상의 글자 범위 지정', 'Bubble 경계를 확보하기 어렵고 지울 글자의 범위를 직접 지정하려고 할 때 검토합니다.',
         'U를 고르고 아래쪽 작은 원문 주위로 사각형을 그었습니다. Action History에 Ubox 1과 LaMa 선택 버튼이 생깁니다.',
         'Apply Analysis 뒤 글자 획이 남았는지, 속도선·머리카락·망점이 같이 지워졌는지 봅니다. LaMa 강제는 R/U 타입 전환이 아니라 지우기 엔진 선택입니다.', '07_ubox.png'),
        ('Region Off · 결과 영역을 제외', '자동 생성된 결과 영역 자체를 처리 대상에서 제외하려고 할 때 검토합니다.',
         'X를 고르고 위쪽 R2를 클릭했습니다. R2 off가 이력에 생깁니다. 원본 TG를 끄는 TG Off와 대상이 다릅니다.',
         'Apply Analysis 뒤 제외한 결과와 남은 OCR 단위를 확인합니다. 새로 만든 수동 guide를 지우는 동작과도 구분합니다.', '08_region_off.png'),
    ]
    for title, when, action, check, image in cases:
        examples += f'<article><h3>{title}</h3><p><strong>언제:</strong> {when}</p><p><strong>화면에서 한 일:</strong> {action}</p><p><strong>확인:</strong> {check}</p>'
        examples += figure(image, f'00006.jpg · 적용 전 조작 예시: {title}', folded=True) + '</article>'
    examples += '''<h3>Edge · R을 만들 경계를 제한하는 가이드</h3>
      <p><strong>언제:</strong> Bubble 내부를 구하려는데 경계가 열려 FloodFill이 새는 경우 검토합니다. E를 고르고 사각형을 그리면 네 변이 가상의 벽이 됩니다.</p>
      <p><strong>저장 결과 관찰:</strong> 위 00008.jpg의 R5·R6은 Edge barrier 경로로 생성된 Bubble입니다. 이것만으로 경계가 적절하다거나 구제가 성공했다고 판정하지 않습니다.</p>
      <p><strong>확인:</strong> Apply Analysis 뒤 R의 범위, 마스크와 인접 영역을 비교합니다. R/U 타입과 “일반 FloodFill / Edge barrier”라는 생성 방법은 별개로 기록합니다.</p>'''
    text = insert_before(text, '      <h3>Guidance 시각화 색상</h3>', 'guidance', examples)

    ocr = '''<h3>실제 화면 — OCR을 다시 실행할 때</h3>
      <p>아래 화면은 기존 결과가 있는 6장이 Success Files에 놓인 상태입니다. 이 매뉴얼을 만들면서 유료 OCR/번역은 새로 요청하지 않았습니다.</p>
      <p>가이드를 바꿔 OCR의 입력 단위가 달라졌다면 먼저 말풍선 분석 화면의 <strong>OCR Sheet</strong>에서 crop·누락·대사 묶음을 확인합니다.
      입력 이미지가 잘못 잘렸다면 Guidance로 돌아가고, 입력은 맞는데 인식·번역이 잘못됐다면 모델·추가 프롬프트와 재실행을 검토합니다.
      문구 몇 개나 줄바꿈만 고치려면 글자 입히기의 대사 편집을 이용할 수 있습니다.</p>'''
    ocr += figure('09_ocr.png', 'OCR/Translation 실제 화면: Run Current, Force Current, Retry Failed와 추가 번역 프롬프트의 위치.', folded=True)
    # 다음 섹션 직전이 아니라 OCR 섹션의 닫는 태그 안에 삽입한다.
    marker = '    </section>\n\n    <section id="overlay">'
    text = insert_before(text, marker, 'ocr', ocr)

    overlay = '''<h3>실제 화면 — 내용과 배치를 고치는 경우</h3>
      <p>00006.jpg의 R2를 선택한 화면입니다. 원문·번역문 대응을 확인한 뒤, 내용이 맞으면 Selected Region의 X/Y/W/H와 쓰기 방향,
      Selected Style의 글꼴·크기를 조정합니다. 이 화면은 기존 렌더 결과이며, 배치가 적절하다는 품질 판정은 아닙니다.</p>'''
    overlay += figure('10_overlay.png', '선택된 R2와 오른쪽의 위치·크기·쓰기 방향·스타일 조절부.')
    overlay += '<p>대사나 줄바꿈을 바꾸려면 해당 영역을 더블클릭합니다. 아래는 실제로 열린 대사 편집 팝업입니다. Cancel은 초안을 버리고, Apply는 대사를 반영합니다.</p>'
    overlay += figure('11_text_editor.png', 'R2를 더블클릭해 연 대사 편집 팝업. 문구와 줄바꿈을 고치는 위치.', folded=True)
    overlay += figure('17_type_badges.png', '00008.jpg의 R4 선택: Selected Region과 Region List에서도 텍스트 기반 배지가 표시됩니다.', folded=True)
    overlay += '<p><strong>지우개 선택 기준:</strong> 흰색을 덧칠해도 되는 바탕의 작은 원문 흔적에 사용합니다. 무늬·망점·선화 위에 흰색 사각형을 올리면 배경이 사라질 수 있으므로 배경 복원을 검토합니다. 큰 영역 검출 문제는 Guidance에서 먼저 확인합니다.</p>'
    text = insert_before(text, '      <h3>자동 미리보기</h3>', 'overlay', overlay)

    restore = '''<h3>실제 실행 — U4의 제거 마스크와 문맥을 따로 지정</h3>
      <p>복사본 00008.jpg에서 자동 마스크를 만든 뒤 C로 문맥 사각형을 그려 LaMa를 실제 실행했습니다.
      빨간색은 제거할 픽셀이고 파란 사각형은 복원에 참고할 문맥입니다. 문맥 사각형 전체를 지우는 동작이 아닙니다.</p>
      <p>이 실행은 입력 페이지를 <strong>원본</strong>으로 선택했습니다. 위쪽의 바탕 이미지 선택은 화면에 무엇을 보여 줄지 정하고,
      오른쪽의 입력 페이지 선택은 복원 엔진이 무엇을 처리할지 정합니다. 둘을 구분해서 확인합니다.</p>'''
    restore += figure('14_restore_context.png', '실제 실행 전: U4에서 만든 제거 마스크와 C로 지정한 문맥. 선택 범위의 적절성은 별도 검토 대상입니다.')
    restore += '''<p>실행 기록은 마스크 <strong>4,959px</strong>, 문맥 밖 제외 마스크 <strong>0px</strong>, 마스크 밖 변경 픽셀 <strong>0px</strong>입니다.
      이 값은 픽셀 보존 검사이며 복원 품질의 합격 판정은 아닙니다. 아래는 앱이 저장한 실제 산출물입니다. <strong>이 결과 확정</strong>은 누르지 않았습니다.</p>'''
    job = next((ROOT/'temp_output/v2_sessions/20261019_222000_manual/work/restore/00008/jobs').glob('*/meta.json')).parent
    for file, label in [('crop_before.png','복원 전 원본 문맥'),('crop_mask.png','제거 마스크 · 흰 픽셀만 제거 대상'),('crop_after.png','LaMa 실행 결과 · 품질 확인 전, 미확정')]:
        # 캡처 파일과 동일하게 실제 산출물 자체를 문서에 내장한다.
        with Image.open(job/file) as image:
            buffer=io.BytesIO(); image.convert('RGB').save(buffer,format='JPEG',quality=90,optimize=True)
            uri=base64.b64encode(buffer.getvalue()).decode('ascii')
        restore += f'<figure style="margin:16px 0"><img src="data:image/jpeg;base64,{uri}" alt="{label}" loading="lazy" style="display:block;width:100%;height:auto"><figcaption>{label}</figcaption></figure>'
    restore += '<p>마지막으로 원문 잔상, 선화 끊김, 망점 번짐, 글자처럼 보이는 새 무늬가 있는지 원본과 비교합니다. 결과가 적절한지 확인한 뒤 확정하고 글자 입히기를 다시 렌더합니다. 이 예시의 품질 판정은 미확정입니다.</p>'
    text = insert_before(text, '    </section>\n\n    <section id="shortcuts">', 'restore', restore)
    # 대화에서 합의한 새 화면 용어. 저장 JSON의 소문자 호환 키는 유지한다.
    text = text.replace('말풍선 분석', '영역 분석').replace('R / U와 도구 선택', '영역 타입과 도구 선택')
    text = text.replace('Bubble / Ubox', '경계 기반 / 텍스트 기반').replace('Bubble', '경계 기반').replace('Ubox', '텍스트 기반 영역').replace('Unboxed', '텍스트 기반')
    text = text.replace('R/U', '처리 기준').replace('U4', 'R4')
    text = text.replace('파란 TG 박스 / 청록 R / 갈색 점선 U', '파란 TG 박스 / 청록 경계 기반 / 갈색 점선 텍스트 기반')
    text = text.replace('TG는 원본 텍스트 그룹, R은 경계 기반, U는 텍스트 기반 결과입니다.', 'TG는 원본 텍스트 그룹, R은 공통 영역 번호입니다. 배지가 처리 기준을 표시합니다.')
    text = text.replace('R(경계 기반) 검출의 시작점을 추가할 때는 경계 기반을 클릭합니다. 현재 경계 기반 도구는 클릭 seed 방식입니다. 경계가 애매한 U(텍스트 기반) 대상의 글자 범위를 직접 잡을 때는 텍스트 기반 영역로 사각형을 드래그합니다.', '경계 기반 검출의 시작점을 추가할 때는 B 도구로 클릭 seed를 찍습니다. 텍스트 기반 처리의 글자 범위를 지정할 때는 U 도구로 사각형을 드래그합니다.')
    text = text.replace('edge wall, bubble, unboxed 영역', 'Edge와 경계 기반·텍스트 기반 영역')
    text = text.replace('경계 기반 · R 검출 시작점 추가', '경계 기반 seed · 검출 시작점 추가')
    text = text.replace('텍스트 기반 영역는', '텍스트 기반 영역은').replace('텍스트 기반 영역로', '텍스트 기반 영역으로')
    text = text.replace('대상 처리 기준와 다른 결과 영역의 변화', '제외한 영역과 다른 결과 영역의 변화')
    text = text.replace('처리 기준 타입과', '처리 기준과')
    MANUAL.write_text(text)
    print(f'{MANUAL}: {MANUAL.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
