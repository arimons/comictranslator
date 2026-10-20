// 검출 결과 fixture의 색상 계약만 확인한다. 실제 이미지의 분류 정확도는 판정하지 않는다.
async(page)=>{
 const p=await page.context().newPage(),sid='20261020_075649',id='00002',errors=[];
 const cases=[{type:'bubble',source_method:'flood',label:'경계 기반 · Bubble',rgb:[56,189,248]},{type:'bubble',source_method:'manual_edge_barrier_rescue',label:'경계 기반 · Edge',rgb:[34,197,94]},{type:'unboxed',source_method:'bbox_fallback',label:'텍스트 기반 · Unboxed',rgb:[234,179,8]}];
 const groups=cases.map((c,i)=>({text_group_id:i+1,fallback_rect:[400,400+i*220,200,100],text:`CV 원문 ${i+1}`}));
 const regions=cases.map((c,i)=>({...c,region_id:i+1,anchor_ids:[i+1],[c.type==='bubble'?'bubble_rect':'unboxed_rect']:groups[i].fallback_rect}));
 p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/api/**',async route=>{
  const req=route.request(),url=req.url();if(req.method()!=='GET')return route.fulfill({status:409,json:{detail:'색상 검사 중 저장/분석 차단'}});
  if(url.endsWith(`/api/session/${sid}/page/${id}`)){const res=await route.fetch(),body=await res.json();return route.fulfill({response:res,json:{image:{...body.image,processing_excluded:false,text_groups:groups,effective_text_groups:groups,guidance:{},regions:{regions}}}});}
  return route.continue();
 });
 try{
  await p.setViewportSize({width:1600,height:1000});await p.goto(`http://127.0.0.1:7873/?generation_review=${sid}&image_id=${id}#editor`);await p.locator('#stageHost canvas').first().waitFor();await p.waitForTimeout(250);
  for(let i=0;i<cases.length;i++){
   const target=await p.evaluate(async({i,groups})=>{
    const {image}=await fetch('/api/session/20261020_075649/page/00002').then(r=>r.json());const img=await new Promise(resolve=>{const n=new Image();n.onload=()=>resolve(n);n.src=image.input_url;});
    const b=document.getElementById('stageHost').getBoundingClientRect(),s=Math.min(b.width/img.width,b.height/img.height)*.98,r=groups[i].fallback_rect;
    return {x:b.x+(b.width-img.width*s)/2+(r[0]+20)*s,y:b.y+(b.height-img.height*s)/2+(r[1]+20)*s};
   },{i,groups});
   await p.mouse.click(target.x,target.y);await p.mouse.click(target.x,target.y);await p.locator('.selected-region-method').getByText(cases[i].label,{exact:false}).waitFor();
   const pixel=await p.locator('#stageHost canvas').last().evaluate((canvas,target)=>{const b=canvas.getBoundingClientRect();return Array.from(canvas.getContext('2d').getImageData((target.x-b.x)*canvas.width/b.width,(target.y-b.y)*canvas.height/b.height,1,1).data);},target);
   if(pixel[3]<25||pixel[3]>80||pixel.slice(0,3).some((v,j)=>Math.abs(v-cases[i].rgb[j])>24))throw Error(cases[i].label+' tint '+pixel);
  }
  await p.screenshot({path:'proof/misc/region-color-palette.png',scale:'css'});if(errors.length)throw Error(errors.join());return {ok:true,checks:['Bubble 파랑','Edge 초록','Unboxed 노랑','투명 채움/선택 정보'],errors};
 }finally{await p.close();}
}
