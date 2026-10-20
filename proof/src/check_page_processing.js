// 실제 페이지에서 설정 버튼을 왕복하고 처음 상태로 돌린다. 유료/분석 API는 차단한다.
async(page)=>{
 const preview=await page.context().newPage();const errors=[],unexpected=[];let original;
 const sid='20261020_075649',id='00002';
 preview.on('pageerror',e=>errors.push(e.message));
 await preview.route('**/api/**',route=>{
  if(route.request().method()!=='GET'&&!route.request().url().endsWith('/api/page-processing')){
   unexpected.push(route.request().url());return route.fulfill({status:400,contentType:'application/json',body:'{"detail":"검증 중 실행 차단"}'});
  }
  return route.continue();
 });
 try{
  await preview.setViewportSize({width:1600,height:1000});
  await preview.goto(`http://127.0.0.1:7873/?generation_review=${sid}&image_id=${id}#editor`);
  original=await preview.evaluate(async()=>Boolean((await fetch('/api/session/20261020_075649/page/00002').then(r=>r.json())).image.processing_excluded));
  await preview.getByRole('button',{name:original?'처리 대상으로 복원':'페이지 처리 제외',exact:true}).click();
  await preview.getByRole('button',{name:original?'페이지 처리 제외':'처리 대상으로 복원',exact:true}).waitFor();
  if(!original){
   await preview.getByText('페이지 처리 제외 · 원본 출력',{exact:true}).waitFor();
   const state=await preview.evaluate(async()=>{
    const image=(await fetch('/api/session/20261020_075649/page/00002').then(r=>r.json())).image;
    return {excluded:image.processing_excluded,originalOutput:image.final_url===image.input_url,guidance:image.guidance};
   });
   if(!state.excluded||!state.originalOutput)throw new Error(JSON.stringify(state));
   const box=await preview.locator('.guidance-editor-card').boundingBox();
   if(box.height<200)throw new Error('편집기 높이 부족');
   await preview.screenshot({path:(process.env.COMIC_TRANSLATOR_HOME||process.env.HOME+'/ComicTranslator')+'/legacy/proof_outputs/misc/page-processing-excluded.png',scale:'css'});
  }
  await preview.getByRole('button',{name:original?'페이지 처리 제외':'처리 대상으로 복원',exact:true}).click();
  await preview.getByRole('button',{name:original?'처리 대상으로 복원':'페이지 처리 제외',exact:true}).waitFor();
  if(errors.length||unexpected.length)throw new Error(JSON.stringify({errors,unexpected}));
  return {ok:true,restoredOriginalState:original,errors,unexpected};
 }finally{
  if(original!==undefined)await preview.evaluate(async(excluded)=>{await fetch('/api/page-processing',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:'20261020_075649',image_id:'00002',excluded})});},original).catch(()=>{});
  await preview.close();
  for(const p of page.context().pages()){p.removeAllListeners('filechooser');const c=await p.context().newCDPSession(p);await c.send('Page.setInterceptFileChooserDialog',{enabled:false});await c.detach();}
 }
}
