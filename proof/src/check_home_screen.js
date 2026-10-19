// Playwright browser_run_code_unsafe의 filename으로 실행한다. API 요청을 모두 가로채 실제 분석·업로드를 하지 않는다.
async (page) => {
  const testPage = await page.context().newPage();
  const calls = [];
  const errors = [];
  const image = { id: "page1", name: "page1.jpg", input_path: "/fixture/page1.jpg", has_analysis: true,
    detail_loaded: true, regions: { regions: [] }, text_groups: {}, guidance: {}, width: 100, height: 100 };
  const session = { session_id: "home_ui_fixture", project_name: "테스트 작품" };
  let recentFail = false;
  const check = (value, message) => { if (!value) throw new Error(message); };
  testPage.on("pageerror", error => errors.push(error.message));
  await testPage.route("**/api/**", async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/analyze-stream") {
      calls.push(request.postDataJSON());
      return route.fulfill({ contentType: "application/x-ndjson", body: JSON.stringify({type:"complete", images:[image]}) + "\n" });
    }
    if (pathname === "/api/sessions/recent" && recentFail) return route.fulfill({status:503, body:"fixture unavailable"});
    let body = {};
    if (pathname === "/api/sessions/recent") body = { sessions: [] };
    else if (pathname === "/api/upload") body = { session_id: session.session_id, imported:1, images:[image] };
    else if (pathname === `/api/session/${session.session_id}`) body = { session, images:[image] };
    else if (pathname === `/api/session/${session.session_id}/page/page1`) body = {image};
    else if (pathname === "/api/session/meta") body = {session};
    else if (pathname.endsWith("/restore-state")) body = {state:{patches:[]}};
    return route.fulfill({contentType:"application/json",body:JSON.stringify(body)});
  });
  const load = async () => {
    await testPage.goto("http://127.0.0.1:7874/#home-fixture");
    // 같은 hash URL로 goto하면 SPA 상태가 유지되므로 각 시나리오는 새로 고침으로 초기화한다.
    await testPage.reload();
    await testPage.getByText("아직 작업한 프로젝트가 없습니다",{exact:true}).waitFor();
  };
  const upload = async () => {
    const chooserEvent = testPage.waitForEvent("filechooser");
    await testPage.getByRole("button",{name:"파일 선택",exact:true}).click();
    await (await chooserEvent).setFiles({name:"page1.jpg",mimeType:"image/jpeg",buffer:Buffer.from("fixture")});
    await testPage.getByText("작업할 이미지 1장",{exact:true}).waitFor();
    await testPage.getByRole("button",{name:"분석하고 작업 시작",exact:false}).waitFor({state:"visible"});
  };
  try {
    await testPage.setViewportSize({width:1440,height:900});
    await load();
    check(await testPage.getByRole("button",{name:"분석하고 작업 시작"}).isDisabled(), "이미지 없이 작업을 시작할 수 없어야 한다");
    await upload();
    await testPage.getByText("선택한 이미지 확인·제외",{exact:true}).click();
    await testPage.getByRole("button",{name:"page1.jpg 목록에서 제외"}).waitFor();
    await testPage.getByRole("button",{name:"분석하고 작업 시작"}).click();
    await testPage.waitForFunction(() => Boolean(document.querySelector('.workspace-mode')));
    check(calls.length === 1 && calls[0].force === false, "기존 분석결과 활용이 force:false를 보내야 한다");
    await load();
    await upload();
    await testPage.getByRole("radio",{name:/처음부터 다시 분석/}).check();
    let notice = "";
    testPage.once("dialog", async dialog => { notice = dialog.message(); await dialog.dismiss(); });
    await testPage.getByRole("button",{name:"다시 분석하고 열기"}).click();
    check(calls.length === 1 && notice.includes("비용"), "재분석 비용 안내를 취소하면 분석 요청이 없어야 한다");
    testPage.once("dialog", dialog => dialog.accept());
    await testPage.getByRole("button",{name:"다시 분석하고 열기"}).click();
    await testPage.waitForFunction(() => Boolean(document.querySelector('.workspace-mode')));
    check(calls.length === 2 && calls[1].force === true, "재분석은 force:true를 보내야 한다");
    await load();
    recentFail = true;
    await testPage.reload();
    await testPage.getByText("최근 프로젝트를 불러오지 못했습니다",{exact:true}).waitFor();
    recentFail = false;
    await testPage.getByRole("button",{name:"다시 시도",exact:true}).click();
    await testPage.getByText("아직 작업한 프로젝트가 없습니다",{exact:true}).waitFor();
    check(errors.length === 0, errors.join("\n"));
    return {ok:true, checks:["빈 상태 시작 비활성화", "파일 선택 및 목록 펼치기", "기존 결과 활용 force:false", "재분석 취소 시 요청 없음", "재분석 force:true", "최근 프로젝트 오류 및 재시도", "브라우저 예외 없음"], analysisRequests:calls};
  } finally { await testPage.close(); }
}
