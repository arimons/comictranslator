async(page)=>{
 const root='http://127.0.0.1:7873';const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const writes=[];await page.route('**/api/**',async route=>{const r=route.request();if(r.method()!=='GET'){writes.push(r.url());await route.abort();return;}await route.continue();});
 await page.goto(root,{waitUntil:'networkidle'});await page.getByText('최근 프로젝트',{exact:true}).waitFor();
 const checks=[];
 for(const [sid,id] of [['20261020_075649','00002'],['20260530_144110','00003'],['20261020_083312_pages_13ba97f6','00001_001_p01_C']]){
  await page.goto(root+'/?generation_review='+sid+'&image_id='+id+'#editor',{waitUntil:'networkidle'});
  await page.locator('.guidance-editor-card canvas').first().waitFor({timeout:20000});
  const summary=await page.request.get(root+'/api/session/'+sid+'?summary=true');if(!summary.ok())throw Error('session '+summary.status());
  const record=await page.request.get(root+'/api/session/'+sid+'/page/'+id);if(!record.ok())throw Error('page '+record.status());
  checks.push({sid,id,canvases:await page.locator('canvas').count()});
 }
 const manual=await page.request.get(root+'/manual');if(!manual.ok())throw Error('manual');
 const generation=await page.request.get(root+'/api/image-generation/20261020_083312_pages_13ba97f6/00001_001_p01_C');const result=await generation.json();
 if(result.results.length!==1)throw Error('cover candidate missing');const preview=await page.request.get(root+result.results[0].preview_url);if(!preview.ok())throw Error('cover preview');
 if(errors.length||writes.length)throw Error(JSON.stringify({errors,writes}));return {ok:true,checks,manual:manual.status(),cover:preview.status(),writes};
}
