async(page)=>{
 const root='http://127.0.0.1:7873',sid='20260530_144110';const errors=[],mutations=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const request=route.request();
  if(request.method()==='GET')return route.continue();
  const body=request.postDataJSON();mutations.push(new URL(request.url()).pathname);
  if(request.url().endsWith('/api/sources/relink'))return route.fulfill({json:{matched:170,unmatched:0,status:{session_id:sid,connected:170,missing:0,fallback:0,owned:0}}});
  if(request.url().endsWith('/api/sources/preview'))return route.continue();
  return route.abort();
 });
 await page.goto(root+'/?generation_review='+sid+'&image_id=00003#editor',{waitUntil:'networkidle'});
 await page.locator('.guidance-editor-card canvas').first().waitFor();
 await page.locator('.source-link-control > summary').click();
 const field=page.getByLabel('원본 폴더 경로',{exact:true});await field.fill('/Users/changhyunpark/ComicTranslator/samples/test_image1/v02');
 await page.getByRole('button',{name:'원본 확인',exact:true}).click();
 const apply=page.getByRole('button',{name:'일치한 170장 연결',exact:true});await apply.waitFor();
 const box=await page.locator('.source-link-body').boundingBox();if(!box||box.height<100)throw Error('source popup missing');
 await page.locator('.source-link-body').screenshot({path:(process.env.HOME+'/ComicTranslator/legacy/proof_outputs/misc/source-links.png')});
 await apply.click();await page.getByText('원본 연결 170장',{exact:false}).waitFor();
 if(errors.length)throw Error(errors.join());
 return {ok:true,preview:'real hash comparison',apply:'mocked, project unmodified',mutations,errors};
}
