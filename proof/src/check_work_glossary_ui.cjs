// 검증 전용 서버의 가상 작품으로 UI 흐름을 점검한다. 실제 API 호출 없음.
const { chromium } = require('/Users/changhyunpark/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const path = require('path');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: "/Users/changhyunpark/Library/Caches/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-mac-arm64/chrome-headless-shell" });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:7889'); console.log('home loaded');
    await page.getByRole('button', { name: '용어집 UI 검증 이어서 작업' }).click();
    const dialog = page.getByRole('dialog', { name: '작품별 번역 용어집' });
    await page.screenshot({path:path.resolve('proof/misc/glossary_ui/start.png')}); console.log((await page.locator('body').innerText()).slice(0,2000)); console.log('errors', errors); await dialog.waitFor(); console.log('dialog opened');
    console.log((await dialog.innerText()).slice(0,1500)); await page.screenshot({path:path.resolve('proof/misc/glossary_ui/before.png')}); await dialog.getByLabel('조사할 작품명').fill('화면 검증 가상 작품');
    await dialog.getByLabel('작가 (동명 작품 구분)').fill('가상 작가');
    await dialog.getByRole('button', { name: '이 작품으로 확인 · 조사 시작' }).click();
    await dialog.getByRole('heading', { name: '충돌·불확실 항목만 검토' }).waitFor({ timeout: 15000 });
    await dialog.getByLabel('星 한국어 표기').fill('호시 확정');
    await dialog.getByRole('button', { name: '적용', exact: true }).first().click();
    await dialog.getByText('적용 2개 · 검토 필요 0개', { exact: false }).waitFor();
    await dialog.getByText('적용 중인 용어 2개', { exact: true }).click();
    if (await dialog.getByLabel('雪 한국어 표기').inputValue() !== '유키') throw new Error('자동 적용 표기 불일치');
    if (await dialog.getByLabel('星 한국어 표기').inputValue() !== '호시 확정') throw new Error('수동 확정 표기 불일치');
    await page.screenshot({ path: path.resolve('proof/misc/glossary_ui/review.png') });
    await dialog.getByRole('button', { name: '용어집 닫기' }).click();
    const topbar = await page.locator('.workspace-global-topbar').boundingBox();
    if (topbar.height > 90) throw new Error(`상단바 줄바꿈: ${topbar.height}`);
    await page.screenshot({ path: path.resolve('proof/misc/glossary_ui/editor.png') });
    await page.reload();
    await page.getByRole('button', { name: '용어집 UI 검증 이어서 작업' }).click();
    await page.getByRole('button', { name: '용어집 · 적용 2', exact: true }).waitFor();
    if (await page.getByRole('dialog').count()) throw new Error('작품 재확인 창이 다시 열림');
    console.log(JSON.stringify({ ui: 'passed', checks: ['작품 확인', '백그라운드 조사', '자동 적용', '불확실 용어 수정', '재접속 시 재확인 없음', '1440px 상단바'], errors, topbar }));
    if (errors.length) process.exitCode = 1;
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
