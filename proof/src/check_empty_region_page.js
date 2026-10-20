async(page)=>{
 const preview=await page.context().newPage();const errors=[],writes=[];
 await preview.route('**/api/**',route=>{
  if(route.request().method()!=='GET'){writes.push(route.request().url());return route.fulfill({status:400,contentType:'application/json',body:'{"detail":"읽기 전용 확인"}'});}
  return route.continue();
 });
 preview.on('pageerror',e=>errors.push(e.message));
 try{
  await preview.setViewportSize({width:1600,height:1000});
  await preview.goto('http://127.0.0.1:7873/?generation_review=20261020_075649&image_id=00002#editor');
  await preview.getByText('처리 대상 없음 · 원본 유지',{exact:true}).waitFor();
  const payload=await preview.evaluate(async()=>{
   const data=await fetch('/api/session/20261020_075649/page/00002').then(r=>r.json());
   return {regions:data.image.regions.regions.length,ocr_count:data.image.ocr_region_count,disabled:data.image.guidance.disabled_tg_ids,suppressed:data.image.guidance.suppressed_regions.length};
  });
  if(payload.regions!==0||payload.ocr_count!==0)throw new Error(JSON.stringify(payload));
  await preview.locator('.guidance-editor-card').screenshot({path:(process.env.COMIC_TRANSLATOR_HOME||process.env.HOME+'/ComicTranslator')+'/legacy/proof_outputs/misc/volume7-page2-no-regions.png'});
  if(errors.length||writes.length)throw new Error(JSON.stringify({errors,writes}));
  return {ok:true,...payload,errors,writes};
 }finally{
  await preview.close();
  for(const p of page.context().pages()){p.removeAllListeners('filechooser');const c=await p.context().newCDPSession(p);await c.send('Page.setInterceptFileChooserDialog',{enabled:false});await c.detach();}
 }
}
