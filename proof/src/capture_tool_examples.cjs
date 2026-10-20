// 실제 앱 조작 → 격리 프로젝트에 같은 Step 3 적용. 모델 API·공유 캐시는 호출하지 않는다.
const capture = async (page) => {
 const fs=require('fs'),cp=require('child_process'),util=require('util'),exec=util.promisify(cp.execFile);
 const root='/Users/changhyunpark/Developer/NEW_COMIC_TRANSLATOR',out=root+'/proof/misc/tool_examples',sid='20261020_manual_tools';
 const p=await page.context().newPage(),errors=[],blocked=[],results=[];
 p.on('pageerror',e=>errors.push(e.message));
 let key;
 await p.route('**/api/**',async route=>{
  const r=route.request();if(r.method()==='GET')return route.continue();
  if(r.url().endsWith('/api/guidance')){
   const req=r.postDataJSON();if(req.session_id!==sid)throw Error('격리 프로젝트가 아님');
   const file=out+'/'+req.image_id+'-guidance.json';fs.writeFileSync(file,JSON.stringify(req.guidance));
   try{
    const process=await exec(root+'/.venv/bin/python',[root+'/proof/src/capture_tool_examples.py','apply',req.image_id,file],{cwd:root,timeout:240000,maxBuffer:8*1024*1024});
    fs.writeFileSync(out+'/'+req.image_id+'-engine.log',process.stdout+process.stderr);
    const response=await p.request.get(`http://127.0.0.1:7873/api/session/${sid}/page/${req.image_id}`);
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({ok:true,image:(await response.json()).image})});
   }catch(e){fs.writeFileSync(out+'/'+req.image_id+'-error.log',String(e)+e.stdout+e.stderr);return route.fulfill({status:500,json:{detail:String(e)}});}
  }
  blocked.push(r.url());return route.fulfill({status:409,json:{detail:'촬영 중 유료/부가 요청 차단'}});
 });
 const point=async(x,y)=>p.evaluate(async({x,y,sid,key})=>{
  const {image}=await fetch(`/api/session/${sid}/page/example_${key}`).then(r=>r.json());
  const img=await new Promise(res=>{const n=new Image();n.onload=()=>res(n);n.src=image.input_url;});
  const b=document.getElementById('stageHost').getBoundingClientRect(),s=Math.min(b.width/img.width,b.height/img.height)*.98;
  return {x:b.x+(b.width-img.width*s)/2+x*s,y:b.y+(b.height-img.height*s)/2+y*s};
 },{x,y,sid,key});
 const click=async(x,y)=>{const t=await point(x,y);await p.mouse.click(t.x,t.y);};
 const drag=async(a,b)=>{const s=await point(...a),e=await point(...b);await p.mouse.move(s.x,s.y);await p.mouse.down();await p.mouse.move(e.x,e.y,{steps:12});await p.mouse.up();};
 const shot=async(name)=>{await p.waitForTimeout(180);await p.screenshot({path:out+'/'+key+'-'+name+'.png',scale:'css'});};
 try{
  await p.setViewportSize({width:1600,height:1060});
  for(key of ['d','t','e','b','s','u']){
   await p.goto(`http://127.0.0.1:7873/?generation_review=${sid}&image_id=example_${key}#editor`);
   await p.locator('#stageHost canvas').first().waitFor();await p.waitForTimeout(450);
   if(key==='d'){
    await p.keyboard.press('d'); // 선택 없을 때 변화·모드 전환 없어야 한다.
    await click(1085,280);
   }
   if(key==='t')await click(290,1600);
   await shot('before');
   if(key==='d')await p.keyboard.press('d');
   if(key==='t'){await p.keyboard.press('t');await drag([270,1500],[270,1790]);}
   if(key==='e'){await p.keyboard.press('e');await drag([260,740],[378,950]);}
   if(key==='b'){await p.keyboard.press('b');await click(1080,150);}
   if(key==='s'){await p.keyboard.press('s');await drag([1138,130],[1138,625]);}
   if(key==='u'){await p.keyboard.press('u');await drag([585,1436],[612,1504]);}
   await shot('guide');
   if(key==='d'){
    await p.keyboard.press('Meta+z');await shot('undo');await click(1085,280);await p.keyboard.press('d');
   }
   const applied=p.waitForResponse(r=>r.url().endsWith('/api/guidance')&&r.request().method()==='POST',{timeout:260000});
   await p.keyboard.press('Meta+Enter');const response=await applied;if(!response.ok())throw Error(key+' 적용 실패 '+await response.text());
   await p.waitForTimeout(500);await shot('after');results.push({key,status:response.status()});
  }
  fs.writeFileSync(out+'/capture-report.json',JSON.stringify({results,errors,blocked},null,2));return {results,errors,blocked};
 }finally{
  await p.close();for(const tab of page.context().pages()){tab.removeAllListeners('filechooser');const c=await tab.context().newCDPSession(tab);await c.send('Page.setInterceptFileChooserDialog',{enabled:false});await c.detach();}
 }
}

const {chromium}=require('/Users/changhyunpark/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'/Users/changhyunpark/Library/Caches/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-mac-arm64/chrome-headless-shell'});try{const context=await browser.newContext();const page=await context.newPage();console.log(JSON.stringify(await capture(page)));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
