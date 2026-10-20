async(page)=>{
 const preview=await page.context().newPage(); const errors=[],writes=[];
 preview.on('pageerror',err=>errors.push(err.message));
 await preview.route('**/api/**',route=>{
  if(route.request().method()!=='GET'){writes.push(route.request().url()); return route.fulfill({status:400,contentType:'application/json',body:'{"detail":"읽기 전용 확인"}'});}
  return route.continue();
 });
 try{
  await preview.setViewportSize({width:1600,height:1050});
  await preview.goto('http://127.0.0.1:7873/?generation_review=20260531_153718&image_id=002#editor');
  await preview.locator('.guidance-editor-card canvas').first().waitFor();
  await preview.waitForResponse(r=>r.url().includes('/page/002')).catch(()=>{});
  await preview.waitForTimeout(700);
  await preview.locator('.guidance-editor-card').screenshot({path:(process.env.COMIC_TRANSLATOR_HOME||process.env.HOME+'/ComicTranslator')+'/legacy/proof_outputs/misc/canvas-labels-compact.png'});
  await preview.setViewportSize({width:3000,height:800});
  await preview.waitForTimeout(400);
  await preview.locator('.guidance-editor-card').screenshot({path:(process.env.COMIC_TRANSLATOR_HOME||process.env.HOME+'/ComicTranslator')+'/legacy/proof_outputs/misc/canvas-labels-gutter.png'});
  if(errors.length||writes.length)throw new Error(JSON.stringify({errors,writes}));
  return {ok:true,errors,writes,screenshots:[(process.env.COMIC_TRANSLATOR_HOME||process.env.HOME+'/ComicTranslator')+'/legacy/proof_outputs/misc/canvas-labels-compact.png',(process.env.COMIC_TRANSLATOR_HOME||process.env.HOME+'/ComicTranslator')+'/legacy/proof_outputs/misc/canvas-labels-gutter.png']};
 }finally{
  await preview.close();
  for(const p of page.context().pages()){p.removeAllListeners('filechooser');const c=await p.context().newCDPSession(p);await c.send('Page.setInterceptFileChooserDialog',{enabled:false});await c.detach();}
 }
}
