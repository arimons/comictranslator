// 실제 Ore_3는 미리보기만 읽는다. 새 프로젝트 생성·분석·번역은 실행하지 않는다.
async (page) => {
  const preview = await page.context().newPage();
  const health = await page.context().newPage();
  const errors = [];
  let writes = 0;
  preview.on("pageerror", error => errors.push(error.message));
  await preview.route("**/api/pages/prepare", route => { writes += 1; return route.abort(); });
  await preview.route("**/api/analyze-stream", route => { writes += 1; return route.abort(); });
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  try {
    await preview.setViewportSize({width:1440,height:1000});
    await preview.goto("http://127.0.0.1:7874/?page_preview=20260531_153718#home");
    await preview.getByText("원본 83장 → 정리 후 165장",{exact:true}).waitFor();
    await preview.waitForLoadState("networkidle");
    check(await preview.getByRole("img",{name:"001.jpeg 1번째 결과",exact:true}).count() === 1,"표지는 중앙 한 장");
    await preview.screenshot({path:"proof/misc/ore3-cover-preview.png",scale:"css"});
    await preview.getByRole("combobox",{name:"분할 미리보기 페이지"}).selectOption("1");
    await preview.waitForLoadState("networkidle");
    check(await preview.getByRole("img",{name:"002.jpeg 2번째 결과",exact:true}).count() === 1,"본문은 두 장");
    await preview.screenshot({path:"proof/misc/ore3-spread-preview.png",scale:"css"});
    await preview.setViewportSize({width:390,height:844});
    check(await preview.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),"미리보기 모바일 가로 넘침");
    let mode = "ok";
    await health.route("**/api/health", route => route.fulfill({status:mode === "ok" ? 200 : 503,contentType:"application/json",body:JSON.stringify({status:mode})}));
    await health.goto("http://127.0.0.1:7874/#home");
    await health.locator('.server-status[data-health="green"]').waitFor();
    mode = "bad";
    await health.locator('.server-status').click();
    await health.locator('.server-status[data-health="yellow"]').waitFor();
    await health.locator('.server-status').click();
    await health.locator('.server-status[data-health="red"]').waitFor();
    mode = "ok";
    await health.locator('.server-status').click();
    await health.locator('.server-status[data-health="green"]').waitFor();
    check(await health.locator('.task-progress-toast').count() === 0,"진행 팝업 제거");
    check(writes === 0,"실제 프로젝트 생성이나 분석 요청이 없어야 한다");
    check(errors.length === 0,errors.join("\n"));
    return {ok:true,source:83,prepared:165,actualWrites:writes,checks:["중앙 표지 1장","본문 오른쪽→왼쪽 2장","모바일 넘침 없음","상태등 초록→노랑→빨강→초록","작업 팝업 없음","실제 분석·생성 없음"]};
  } finally { await preview.close(); await health.close(); }
}
