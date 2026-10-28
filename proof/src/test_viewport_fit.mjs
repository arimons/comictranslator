import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const { createViewport } = await import(`data:text/javascript;base64,${readFileSync('frontend/src/viewportControls.js').toString('base64')}`);
const host = { clientWidth: 200, clientHeight: 200 };
const image = { width: 100, height: 200 };
const viewport = createViewport({ getHost: () => host, getImage: () => image, getKey: () => "page" });
assert.equal(viewport.ensure(200, 200, image).scale, .98);
host.clientWidth = host.clientHeight = 800;
assert.equal(viewport.ensure(800, 800, image).scale, 3.92);
viewport.zoomBy(2);
assert.equal(viewport.view.mode, "manual");
const manual = viewport.view.scale;
host.clientWidth = host.clientHeight = 900;
assert.equal(viewport.ensure(900, 900, image).scale, manual);
viewport.fit();
assert.equal(viewport.view.mode, "fit");
// 왼쪽 정렬: 넓은 캔버스는 오른쪽 패널 자리를 비우고, 비우면 15% 넘게 작아지는 좁은 캔버스는 비우지 않는다
const left = createViewport({ getHost: () => host, getImage: () => image, getKey: () => "left", fitPadding: 1, alignLeft: 70, reserveRight: 324 });
assert.deepEqual([left.ensure(1600, 1000, image).x, left.ensure(1600, 1000, image).scale], [70, 5]);
const narrow = createViewport({ getHost: () => host, getImage: () => image, getKey: () => "narrow", fitPadding: 1, alignLeft: 70, reserveRight: 324 });
assert.equal(narrow.ensure(700, 2000, image).scale, 5.6);
console.log("viewport: 작은 이미지 확대, 창 크기 변경 시 맞춤 재계산, 수동 확대 유지, 왼쪽 정렬·패널 자리 통과");
