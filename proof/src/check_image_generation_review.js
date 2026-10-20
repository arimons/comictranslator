// 실제 프로젝트는 읽기만 하며 모델/분석/번역을 호출하는 POST는 모두 차단한다.
async (page) => {
  const preview = await page.context().newPage();
  const errors = [], writes = [];
  preview.on('pageerror', error => errors.push(error.message));
  await preview.route('**/api/**', async route => {
    const request = route.request();
    if (request.method() !== 'GET') {
      writes.push(request.url());
      return route.fulfill({status:400,contentType:'application/json',body:'{"detail":"읽기 전용 확인"}'});
    }
    return route.continue();
  });
  try {
    await preview.setViewportSize({width:1440,height:1000});
    await preview.goto('http://127.0.0.1:7873/?generation_review=20261020_083312_pages_13ba97f6&image_id=00001_001_p01_C#editor');
    const badge = preview.getByRole('button', {name:'이미지 생성 권장 · 후보 1',exact:true});
    await badge.waitFor();
    await badge.click();
    const dialog = preview.getByRole('dialog', {name:'이미지 생성 검토'});
    await dialog.waitFor();
    await dialog.getByText('미검토 후보 · 최종 출력 미반영',{exact:true}).waitFor();
    await preview.waitForFunction(()=>Array.from(document.querySelectorAll('.generation-compare img')).length===2 && Array.from(document.querySelectorAll('.generation-compare img')).every(i=>i.naturalWidth>0));
    const images = await dialog.locator('img').evaluateAll(nodes => nodes.map(node=>({width:node.naturalWidth,height:node.naturalHeight,visible:node.getBoundingClientRect().height})));
    if(images.some(i=>i.visible < 200)) throw new Error('비교 이미지 표시 크기가 너무 작음');
    await preview.screenshot({path:(process.env.COMIC_TRANSLATOR_HOME||process.env.HOME+'/ComicTranslator')+'/legacy/proof_outputs/misc/ore3-cover-generation/editor-comparison.png',scale:'css'});
    await preview.setViewportSize({width:600,height:900});
    const overflow = await dialog.evaluate(node => node.scrollWidth > node.clientWidth+2);
    if(overflow) throw new Error('생성 비교 팝업 가로 넘침');
    await preview.keyboard.press('Escape');
    await dialog.waitFor({state:'hidden'});
    if(errors.length || writes.length) throw new Error(JSON.stringify({errors,writes}));
    return {ok:true,images,actualWrites:writes,errors};
  } finally {
    await preview.close();
    for(const p of page.context().pages()) {
      p.removeAllListeners('filechooser');
      const c = await p.context().newCDPSession(p);
      await c.send('Page.setInterceptFileChooserDialog',{enabled:false});
      await c.detach();
    }
  }
}
