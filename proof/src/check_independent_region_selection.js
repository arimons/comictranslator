// 분리된 선택/삭제/도구 1회 입력/R 화자 저장 계약. 실제 분석·저장은 요청하지 않는다.
async(page)=>{
 const p=await page.context().newPage(),sid='20261020_075649',id='00002',errors=[],posts=[],byRegion={};
 const groups=[{text_group_id:1,fallback_rect:[650,450,80,60],text:'CV 원문'}];
 const regions=[{region_id:1,type:'unboxed',source_method:'manual_ubox_guide',unboxed_rect:[100,100,250,180],anchor_ids:[]},{region_id:2,type:'bubble',source_method:'flood',bubble_rect:[600,400,200,180],anchor_ids:[1]},{region_id:3,type:'bubble',source_method:'manual_edge_barrier_rescue',bubble_rect:[900,700,220,200],anchor_ids:[1]}];
 const guidance={disabled_tg_ids:[],tg_splits:[],virtual_walls:[],split_guides:[],manual_regions:[],manual_seed_points:[],suppressed_regions:[]};
 p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/api/**',async route=>{
  const req=route.request(),url=req.url();
  if(req.method()==='POST'){
   const body=req.postDataJSON();posts.push({url,body});
   if(url.endsWith('/api/speakers')){if(body.tg_ids?.length||!body.region_id||!body.region_key)throw Error('R 전용 화자 요청 아님');byRegion[body.region_key]=body.character_id;return route.fulfill({json:{by_tg:{},by_region:byRegion}});}
   throw Error('허용 안 된 실행 '+url);
  }
  if(url.endsWith(`/api/session/${sid}/page/${id}`)){const res=await route.fetch(),body=await res.json();return route.fulfill({response:res,json:{image:{...body.image,processing_excluded:false,text_groups:groups,effective_text_groups:groups,guidance,regions:{regions}}}});}
  if(url.endsWith(`/api/speakers/${sid}/${id}`))return route.fulfill({json:{by_tg:{},by_region:byRegion}});
  return route.continue();
 });
 const read=()=>p.locator('textarea[aria-label="영역 분석 JSON"]').inputValue().then(JSON.parse);
 const point=async(x,y)=>p.evaluate(async({x,y})=>{const {image}=await fetch('/api/session/20261020_075649/page/00002').then(r=>r.json());const n=await new Promise(resolve=>{const i=new Image();i.onload=()=>resolve(i);i.src=image.input_url;});const b=document.getElementById('stageHost').getBoundingClientRect(),s=Math.min(b.width/n.width,b.height/n.height)*.98;return {x:b.x+(b.width-n.width*s)/2+x*s,y:b.y+(b.height-n.height*s)/2+y*s};},{x,y});
 const click=async(x,y)=>{const q=await point(x,y);await p.mouse.click(q.x,q.y);};
 const drag=async(a,b)=>{const q=await point(...a),r=await point(...b);await p.mouse.move(q.x,q.y);await p.mouse.down();await p.mouse.move(r.x,r.y,{steps:8});await p.mouse.up();};
 const title=()=>p.locator('.guidance-editor-card .editor-title strong').innerText();
 try{
  await p.setViewportSize({width:1600,height:1000});await p.goto(`http://127.0.0.1:7873/?generation_review=${sid}&image_id=${id}#editor`);await p.locator('#stageHost canvas').first().waitFor();await p.waitForTimeout(200);
  await click(680,470);if(await title()!=='TG1')throw Error('TG 독립 선택 실패 '+await title());
  await click(680,470);if(await title()!=='R2')throw Error('겹친 위치 R 순환 실패 '+await title());
  await p.keyboard.press('Delete');if((await read()).suppressed_regions.length!==1)throw Error('선택 R 삭제 실패');
  await p.keyboard.press('Meta+z');await click(150,150);if(await title()!=='R1')throw Error('anchor 없는 수동 R 선택 실패');
  await p.keyboard.press('d');if(!(await read()).suppressed_regions[0]?.target_key)throw Error('수동 R 정확한 제외 실패');
  await p.keyboard.press('Meta+z');await click(150,150);await p.keyboard.press('n');
  await p.waitForTimeout(120);
  if(!posts.some(x=>x.body.region_id===1))throw Error('CV 누락 R 화자 요청 없음');
  await click(680,470);await p.keyboard.press('d');if((await read()).disabled_tg_ids.join()!=='1')throw Error('TG 제외 실패');
  await click(980,780);if(await title()!=='R3')throw Error('TG 제외 후 수동 R 선택 실패');
  await p.keyboard.press('n');await p.waitForTimeout(80);
  if(!posts.some(x=>x.body.region_id===3))throw Error('TG 제외 후 R 화자 지정 실패');
  await p.keyboard.press('Meta+z'); // TG 제외 초안을 복원
  for(const [key,a,b] of [['e',[300,1000],[430,1100]],['u',[300,1200],[430,1300]],['s',[300,1400],[440,1500]]]){
   await p.keyboard.press(key);await drag(a,b);if(await p.getByRole('button',{name:'선택',exact:true}).getAttribute('aria-pressed')!=='true')throw Error(key+' 도구 V 복귀 실패');
  }
  await p.keyboard.press('b');await click(500,1000);if(await p.getByRole('button',{name:'선택',exact:true}).getAttribute('aria-pressed')!=='true')throw Error('B V 복귀 실패');
  await p.keyboard.press('Escape');await click(680,470);await p.keyboard.press('t');await drag([675,440],[675,520]);if(await p.getByRole('button',{name:'선택',exact:true}).getAttribute('aria-pressed')!=='true')throw Error('T V 복귀 실패');
  if(errors.length)throw Error(errors.join());await p.screenshot({path:'proof/misc/independent-region-selection.png',scale:'css'});
  return {ok:true,checks:['V에서 TG/R 독립 선택·겹친 위치 순환','R Delete·수동 R D·Undo','CV 누락 R 화자','TG 제외 후 남은 R 화자','E U S B T 1회 입력 후 V'],speakerRequests:posts.map(x=>x.body.region_id),errors};
 }finally{await p.close();}
}
