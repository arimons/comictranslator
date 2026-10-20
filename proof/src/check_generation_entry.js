// 실제 등록 후보를 표시하고, 유료 분석 요청은 fixture로 가로채 전환 계약만 검증한다.
async(page)=>{
 const p=await page.context().newPage(),sid='20261020_083312_pages_13ba97f6',id='00001_001_p01_C';
 const errors=[],posts=[];let fixture=null,failOriginal=false;
 p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/api/**',async route=>{
  const req=route.request(),url=req.url();
  if(req.method()==='GET'){
   if(failOriginal&&url.includes('/api/file?')&&url.includes('/input/'))return route.fulfill({status:503,body:'preview unavailable'});
   if(fixture&&url.endsWith(`/api/session/${sid}/page/${id}`))return route.fulfill({json:{image:fixture}});
   return route.continue();
  }
  posts.push({url,body:req.postDataJSON()});
  if(url.endsWith('/api/glossary/bind'))return route.fulfill({status:409,json:{detail:'QA 중 작품 연결 생략'}});
  if(url.endsWith('/api/page-analysis-route')){
   if(req.postDataJSON().route!=='standard')throw Error('전환 경로');
   const res=await p.request.get(`http://127.0.0.1:7873/api/session/${sid}/page/${id}`);
   fixture={...(await res.json()).image,analysis_route:'standard'};
   return route.fulfill({json:{image:fixture}});
  }
  if(url.endsWith('/api/analyze-stream')){
   const body=req.postDataJSON();if(body.image_id!==id||body.force!==false)throw Error('선택 페이지/캐시 재사용 옵션 오류');
   fixture={...fixture,has_analysis:true,ocr_region_count:1,has_effective_text_groups:false,
    text_groups:[{text_group_id:1,fallback_rect:[15,15,100,40]}],
    regions:{regions:[{region_id:1,type:'unboxed',anchor_ids:[1],unboxed_rect:[15,15,100,40]}]},masked_url:fixture.input_url};
   return route.fulfill({contentType:'application/x-ndjson',body:[{type:'image_status',image_id:id,status:'done',image:fixture},{type:'complete',ok:true,images:[fixture]}].map(e=>JSON.stringify(e)).join('\n')+'\n'});
  }
  throw Error('예상하지 못한 POST '+url);
 });
 try{
  await p.setViewportSize({width:1600,height:1060});
  await p.goto(`http://127.0.0.1:7873/?generation_review=${sid}&image_id=${id}#editor`);
  const card=p.locator('.guidance-preview-card'),candidate=card.getByRole('button',{name:'생성 결과',exact:true});
  await candidate.waitFor();await p.waitForFunction(()=>Array.from(document.querySelectorAll('.guidance-preview-card img')).some(i=>i.complete&&i.naturalWidth&&i.src.includes('result-')));
  await card.locator('.generation-result-strip').waitFor();
  if(await card.locator('.generation-preview-notice').count())throw Error('결과가 있는데 큰 안내가 남음');
  await p.screenshot({path:(process.env.COMIC_TRANSLATOR_HOME||process.env.HOME+'/ComicTranslator')+'/legacy/proof_outputs/misc/generation-entry-preview.png',scale:'css'});
  // 캐시된 원본/후보를 여러 번 바꿔도 영구 로딩 상태가 남지 않는다.
  for(let i=0;i<4;i++){
   await card.getByRole('button',{name:'원본',exact:true}).click();
   await p.waitForFunction(()=>{const n=document.querySelector('.guidance-preview-card img');return n?.complete&&n.naturalWidth>0&&n.style.display!=='none';});
   await candidate.click();
   await p.waitForFunction(()=>{const n=document.querySelector('.guidance-preview-card img');return n?.complete&&n.naturalWidth>0&&n.style.display!=='none';});
  }
  failOriginal=true;await card.getByRole('button',{name:'원본',exact:true}).click();
  // 오류 이벤트로 실패 표시를 확인하고 실제 URL 재시도를 검증한다.
  await p.evaluate(()=>{const n=document.querySelector('.guidance-preview-card img');n.src += '&qa_failure=1';});
  await card.getByText('미리보기를 불러오지 못했습니다.',{exact:false}).waitFor();
  failOriginal=false;await card.getByRole('button',{name:'다시 불러오기',exact:true}).click();
  await p.waitForFunction(()=>{const n=document.querySelector('.guidance-preview-card img');return n?.complete&&n.naturalWidth>0&&n.style.display!=='none';});
  const switched=p.waitForResponse(r=>r.url().endsWith('/api/analyze-stream'));
  await card.locator('.generation-result-strip summary').click();
  await card.getByRole('button',{name:'기존 방식으로 처리 · CV/자동 분석',exact:true}).click();await switched;
  await card.getByText('기존 방식으로 처리 중',{exact:true}).waitFor();
  if(!await card.getByRole('button',{name:'생성 결과',exact:true}).isEnabled())throw Error('전환 후 생성 후보 유실');
  if(posts.filter(x=>x.url.endsWith('/api/analyze-stream')).length!==1)throw Error('분석 중복 요청');
  // 결과가 없는 페이지는 큰 안내와 원본 표시를 유지한다.
  fixture=null;
  await p.route('**/api/image-generation/**',async route=>{const res=await route.fetch();const body=await res.json();return route.fulfill({response:res,json:{...body,results:[]}});});
  await p.reload();
  await card.getByText('이미지 생성 권장 페이지',{exact:true}).waitFor();
  if(await card.locator('.generation-result-strip').count())throw Error('결과 없는 페이지에 결과 표시');
  await card.getByRole('button',{name:'기존 방식으로 처리 · CV/자동 분석',exact:true}).waitFor();
  if(errors.length)throw Error(errors.join());
  return {ok:true,candidateRegistered:true,checks:['등록 결과는 큰 안내 대신 바로 표시','결과가 없으면 권장 안내와 전환 버튼 유지','캐시 이미지 전환 4회','미리보기 오류/재시도','선택 페이지 기존 분석 1회','CV 캐시 재사용 옵션','전환 뒤 후보 보존'],errors};
 }finally{await p.close();for(const tab of page.context().pages()){tab.removeAllListeners('filechooser');const c=await tab.context().newCDPSession(tab);await c.send('Page.setInterceptFileChooserDialog',{enabled:false});await c.detach();}}
}
