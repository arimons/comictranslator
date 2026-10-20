// 응답 fixture 안에서만 삭제/Undo/메뉴/적용을 검증한다. 실제 분석 POST는 서버에 전달하지 않는다.
async(page)=>{
 const preview=await page.context().newPage();const errors=[],unexpected=[];let applyCount=0,fixture;
 const sid='20261020_075649',id='00002',rect=[557,1561,293,93];
 preview.on('pageerror',e=>errors.push(e.message));
 const empty=()=>({disabled_tg_ids:[],tg_splits:[],virtual_walls:[],split_guides:[],manual_regions:[],manual_seed_points:[],suppressed_regions:[]});
 await preview.route('**/api/**',async route=>{
  const request=route.request(),url=request.url();
  if(request.method()==='POST'&&url.endsWith('/api/guidance')){
   applyCount++; const guidance=request.postDataJSON().guidance;
   fixture={...fixture,guidance,regions:{regions:[]},ocr_region_count:0};
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({ok:true,image:fixture})});
  }
  if(request.method()!=='GET'){unexpected.push(url);return route.fulfill({status:400,contentType:'application/json',body:'{"detail":"실제 실행 차단"}'});}
  if(url.endsWith(`/api/session/${sid}`)){
   const response=await route.fetch(),body=await response.json();
   Object.assign(body.images.find(i=>i.id===id),{processing_excluded:false,ocr_region_count:fixture?.ocr_region_count??1,has_analysis:true,has_effective_text_groups:false});
   return route.fulfill({response,json:body});
  }
  if(url.endsWith(`/api/session/${sid}/page/${id}`)){
   const response=await route.fetch(),body=await response.json();
   fixture ||= {...body.image,processing_excluded:false,ocr_region_count:1,has_analysis:true,has_effective_text_groups:false,
    text_groups:[{text_group_id:3,fallback_rect:rect}],guidance:empty(),
    regions:{regions:[{region_id:1,type:'unboxed',source_method:'bbox_fallback',source_order:1,anchor_ids:[3],unboxed_rect:rect}]}};
   return route.fulfill({response,json:{image:fixture}});
  }
  return route.continue();
 });
 const read=()=>preview.evaluate(()=>Array.from(document.querySelectorAll('.tools textarea')).map(n=>{try{return JSON.parse(n.value);}catch{return null;}}).find(v=>Array.isArray(v?.disabled_tg_ids)));
 const pixels=()=>preview.locator('#stageHost canvas').last().evaluate(c=>c.toDataURL());
 try{
  await preview.setViewportSize({width:1600,height:1000});
  await preview.goto(`http://127.0.0.1:7873/?generation_review=${sid}&image_id=${id}#editor`);
  await preview.waitForFunction(()=>Array.from(document.querySelectorAll('.tools textarea')).some(n=>{try{return JSON.parse(n.value).disabled_tg_ids?.length===0;}catch{return false;}}));
  await preview.waitForTimeout(350);
  const target=await preview.evaluate(async(rect)=>{
   const data=await fetch('/api/session/20261020_075649/page/00002').then(r=>r.json());
   const image=await new Promise(resolve=>{const i=new Image();i.onload=()=>resolve(i);i.src=data.image.input_url;});
   const box=document.getElementById('stageHost').getBoundingClientRect(),s=Math.min(box.width/image.naturalWidth,box.height/image.naturalHeight)*.98;
   return {x:box.x+(box.width-image.naturalWidth*s)/2+(rect[0]+rect[2]/2)*s,y:box.y+(box.height-image.naturalHeight*s)/2+(rect[1]+rect[3]/2)*s};
  },rect);
  const initial=await pixels();
  await preview.mouse.click(target.x,target.y);await preview.keyboard.press('Backspace');
  await preview.getByText(/미적용 변경/).waitFor();
  if((await read()).disabled_tg_ids.join()!=='3')throw new Error('TG 삭제 미반영');
  const deleted=await pixels();if(initial===deleted)throw new Error('캔버스 삭제 미반영');
  await preview.keyboard.press('Meta+z');
  await preview.waitForTimeout(80);
  if((await read()).disabled_tg_ids.length||(await pixels())!==initial)throw new Error('Undo 캔버스 복원 실패');
  await preview.mouse.click(target.x,target.y,{button:'right'});
  await preview.getByRole('menu',{name:'영역 편집 메뉴'}).waitFor();
  if((await read()).suppressed_regions.length)throw new Error('우클릭만으로 삭제됨');
  await preview.screenshot({path:'proof/misc/toolbar-contextmenu.png',scale:'css'});
  await preview.getByRole('menuitem',{name:'이 처리 영역 제외',exact:true}).click();
  if((await read()).suppressed_regions.length!==1)throw new Error('R 제외 미반영');
  if((await pixels())!==deleted)throw new Error('R 제외 시 TG가 남거나 삭제 상자가 남음');
  await preview.keyboard.press('Meta+z');await preview.waitForTimeout(80);
  if((await pixels())!==initial)throw new Error('R 제외 Undo 복원 실패');
  await preview.mouse.click(target.x,target.y);await preview.keyboard.press('Backspace');
  const apply=preview.getByRole('button',{name:/^분석 적용/}).last();
  await apply.focus();await preview.keyboard.press('Enter');await preview.waitForTimeout(100);
  if(applyCount)throw new Error('일반 Enter가 분석을 실행함');
  await preview.keyboard.press('Meta+Enter');
  await preview.waitForFunction(()=>!Array.from(document.querySelectorAll('.editor-meta [role="status"]')).some(n=>n.textContent.includes('미적용 변경')));
  if(applyCount!==1)throw new Error(`명시적 적용 횟수 ${applyCount}`);
  if(await preview.locator('.floating-toolbox button').count()!==8)throw new Error('R 제거 도구가 툴바에 남음');
  if(errors.length||unexpected.length)throw new Error(JSON.stringify({errors,unexpected}));
  return {ok:true,applyCount,checks:['TG 삭제와 R 숨김','Cmd+Z 픽셀 복원','우클릭만으로 삭제 안 함','R 제외와 연결 TG 숨김','일반 Enter 차단','Cmd+Enter 한 번만 실행','툴바 8개'],errors,unexpected};
 }finally{
  await preview.close();
  for(const p of page.context().pages()){p.removeAllListeners('filechooser');const c=await p.context().newCDPSession(p);await c.send('Page.setInterceptFileChooserDialog',{enabled:false});await c.detach();}
 }
}
