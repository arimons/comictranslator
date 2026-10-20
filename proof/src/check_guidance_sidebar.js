// 원본 페이지 읽기 + 응답 fixture. 분석/삭제/화자 저장은 실제 서버에 보내지 않는다.
async(page)=>{
 const p=await page.context().newPage(),sid='20261020_075649',id='00002',rect=[557,1561,293,93],errors=[],posts=[];
 const empty={disabled_tg_ids:[],tg_splits:[],virtual_walls:[],split_guides:[],manual_regions:[],manual_seed_points:[],suppressed_regions:[]};
 p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/api/**',async route=>{
  const req=route.request(),url=req.url();
  if(req.method()!=='GET'){posts.push(url);return route.fulfill({status:409,json:{detail:'UI 검증 중 실제 저장·분석·삭제 차단'}});}
  if(url.endsWith(`/api/session/${sid}/page/${id}`)){
   const res=await route.fetch(),body=await res.json();return route.fulfill({response:res,json:{image:{...body.image,processing_excluded:false,has_effective_text_groups:false,guidance:empty,effective_text_groups:[{text_group_id:3,fallback_rect:rect,text:"CV原文（認識誤りあり）"}],text_groups:[{text_group_id:3,fallback_rect:rect,text:"CV原文（認識誤りあり）"}],regions:{regions:[{region_id:1,type:'unboxed',anchor_ids:[3],unboxed_rect:rect}]}}}});
  }
  return route.continue();
 });
 try{
  await p.setViewportSize({width:1600,height:1000});await p.goto(`http://127.0.0.1:7873/?generation_review=${sid}&image_id=${id}#editor`);
  await p.locator('#stageHost canvas').first().waitFor();await p.waitForTimeout(250);
  const tools=p.locator('.tools');await tools.getByRole('heading',{name:'영역 편집',exact:true}).waitFor();
  if(await tools.locator('.guidance-disclosure[open]').count())throw Error('보조 기능이 기본으로 펼쳐짐');
  if(await tools.locator('textarea').isVisible())throw Error('JSON 상시 노출');
  if(await tools.getByRole('button',{name:'Clear Unsaved'}).count()||await tools.getByRole('button',{name:'Delete Outputs'}).count())throw Error('불명확한 이름 남음');
  if(await tools.locator('.speaker-section button').count())throw Error('미선택 상태에서 비활성 화자 버튼 노출');
  if(await p.locator('.workspace > .page-processing-bar,.workspace > .image-generation-bar').count())throw Error('캔버스 위 상태 행이 남음');
  if(await tools.locator('.image-generation-control').count())throw Error('일반 페이지에 생성 안내 노출');
  const bounds=await p.locator('#stageHost').boundingBox();if(bounds.height<800)throw Error('편집 높이 부족 '+bounds.height);
  await p.screenshot({path:'proof/misc/guidance-sidebar-idle.png',scale:'css'});
  const target=await p.evaluate(async rect=>{
   const {image}=await fetch('/api/session/20261020_075649/page/00002').then(r=>r.json());
   const n=await new Promise(resolve=>{const i=new Image();i.onload=()=>resolve(i);i.src=image.input_url;});
   const b=document.getElementById('stageHost').getBoundingClientRect(),s=Math.min(b.width/n.naturalWidth,b.height/n.naturalHeight)*.98;
   return {x:b.x+(b.width-n.naturalWidth*s)/2+(rect[0]+rect[2]/2)*s,y:b.y+(b.height-n.naturalHeight*s)/2+(rect[1]+rect[3]/2)*s};
  },rect);
  await p.mouse.click(target.x,target.y);
  await tools.locator('.speaker-section button').first().waitFor();
  if(await tools.locator('.speaker-section button:enabled').count()<2)throw Error('선택 후 화자 지정 없음');
  const cv=tools.getByRole('textbox',{name:'선택 영역 CV 텍스트',exact:true});
  if(await cv.inputValue()!=='CV原文（認識誤りあり）'||!await cv.evaluate(n=>n.readOnly))throw Error('CV 읽기 전용 표시 실패 '+await cv.inputValue());
  const fillAlpha=await p.locator('#stageHost canvas').last().evaluate((c,target)=>{const b=c.getBoundingClientRect();return c.getContext('2d').getImageData((target.x-b.x)*c.width/b.width,(target.y-b.y)*c.height/b.height,1,1).data[3];},target);
  if(fillAlpha<20)throw Error('선택 R 색칠 없음 '+fillAlpha);
  await p.screenshot({path:'proof/misc/guidance-sidebar-selected.png',scale:'css'});
  await tools.getByText('표시 설정',{exact:true}).click();
  const regions=tools.getByLabel('처리 영역 (R)',{exact:true});await regions.uncheck();await regions.check();
  await tools.getByText('표시 설정',{exact:true}).click();
  await tools.getByText('고급 편집·초기화',{exact:true}).click();
  await tools.getByRole('button',{name:'이 페이지 작업 결과 삭제…',exact:true}).click();
  await tools.getByRole('group',{name:'작업 결과 삭제 확인'}).waitFor();
  if(posts.length)throw Error('확인 전 서버 요청 '+posts.join());
  await tools.getByRole('button',{name:'취소',exact:true}).click();
  if(await tools.getByRole('group',{name:'작업 결과 삭제 확인'}).count())throw Error('삭제 취소 실패');
  await tools.getByText('가이드 JSON 직접 편집',{exact:true}).click();
  const json=tools.getByRole('textbox',{name:'영역 분석 JSON',exact:true});
  await json.fill(JSON.stringify({...empty,disabled_tg_ids:[3]}));
  await tools.getByText('미적용 변경 있음',{exact:true}).waitFor();
  await tools.getByRole('button',{name:'가이드 전체 비우기',exact:true}).click();
  if(JSON.parse(await json.inputValue()).disabled_tg_ids.length)throw Error('가이드 비우기 실패');
  await tools.getByRole('button',{name:'편집 실행 취소',exact:true}).click();
  if(JSON.parse(await json.inputValue()).disabled_tg_ids.join()!=='3')throw Error('가이드 비우기 Undo 실패');
  if(errors.length||posts.length)throw Error(JSON.stringify({errors,posts}));
  return {ok:true,checks:['일반 페이지 안내 없음/편집 높이 확보','R 선택 색칠','CV 원문 읽기 전용','필수 작업만 기본 표시','미선택 화자 버튼 숨김','선택 후 화자 지정','표시 설정 유지','JSON 접어서 편집','가이드 비우기와 Undo','작업 결과 삭제 확인/취소'],errors,posts};
 }finally{await p.close();}
}
