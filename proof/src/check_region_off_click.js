// 브라우저 응답만 되돌린 재현 fixture. 실제 프로젝트/분석 적용 API는 수정하지 않는다.
async(page)=>{
 const preview=await page.context().newPage();const writes=[],errors=[];
 const sid='20261020_075649',id='00002',rect=[557,1561,293,93];
 preview.on('pageerror',e=>errors.push(e.message));
 await preview.route('**/api/**',async route=>{
  const request=route.request();
  if(request.method()!=='GET'){writes.push(request.url());return route.fulfill({status:400,contentType:'application/json',body:'{"detail":"읽기 전용 확인"}'});}
  if(request.url().endsWith(`/api/session/${sid}`)){
   const response=await route.fetch();const body=await response.json();
   const image=body.images.find(i=>i.id===id);Object.assign(image,{ocr_region_count:1,has_analysis:true,has_effective_text_groups:false});
   return route.fulfill({response,json:body});
  }
  if(request.url().endsWith(`/api/session/${sid}/page/${id}`)){
   const response=await route.fetch();const body=await response.json();
   Object.assign(body.image,{ocr_region_count:1,has_analysis:true,has_effective_text_groups:false,
    text_groups:[{text_group_id:3,fallback_rect:rect}],guidance:{disabled_tg_ids:[],suppressed_regions:[]},
    regions:{regions:[{region_id:1,type:'unboxed',source_method:'bbox_fallback',source_order:1,anchor_ids:[3],unboxed_rect:rect}]}});
   return route.fulfill({response,json:body});
  }
  return route.continue();
 });
 try{
  await preview.setViewportSize({width:1600,height:1000});
  await preview.goto(`http://127.0.0.1:7873/?generation_review=${sid}&image_id=${id}#editor`);
  await preview.waitForFunction(()=>Array.from(document.querySelectorAll('.tools textarea')).some(n=>{try{return JSON.parse(n.value).disabled_tg_ids?.length===0;}catch{return false;}}));
  const target=await preview.evaluate(async(rect)=>{
   const data=await fetch('/api/session/20261020_075649/page/00002').then(r=>r.json());
   const image=await new Promise(resolve=>{const i=new Image();i.onload=()=>resolve(i);i.src=data.image.input_url;});
   const host=document.getElementById('stageHost');const box=host.getBoundingClientRect();const s=Math.min(box.width/image.naturalWidth,box.height/image.naturalHeight)*.98;
   return {x:box.x+(box.width-image.naturalWidth*s)/2+(rect[0]+rect[2]/2)*s,y:box.y+(box.height-image.naturalHeight*s)/2+(rect[1]+rect[3]/2)*s};
  },rect);
  await preview.keyboard.press('x');
  await preview.mouse.click(target.x,target.y);
  await preview.waitForFunction(()=>Array.from(document.querySelectorAll('.tools textarea')).some(n=>{try{return JSON.parse(n.value).suppressed_regions?.length===1;}catch{return false;}}));
  const guidance=await preview.evaluate(()=>Array.from(document.querySelectorAll('.tools textarea')).map(n=>{try{return JSON.parse(n.value);}catch{return null;}}).find(v=>v?.suppressed_regions?.length===1));
  if(guidance.disabled_tg_ids.length)throw new Error('Region Off 클릭이 TG 삭제로 처리됨');
  if(writes.length||errors.length)throw new Error(JSON.stringify({writes,errors}));
  return {ok:true,excludedRegion:guidance.suppressed_regions[0].display_region_id,disabledTg:guidance.disabled_tg_ids,writes,errors};
 }finally{
  await preview.close();
  for(const p of page.context().pages()){p.removeAllListeners('filechooser');const c=await p.context().newCDPSession(p);await c.send('Page.setInterceptFileChooserDialog',{enabled:false});await c.detach();}
 }
}
