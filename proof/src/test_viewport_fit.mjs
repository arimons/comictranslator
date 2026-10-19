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
console.log("viewport: 작은 이미지 확대, 창 크기 변경 시 맞춤 재계산, 수동 확대 유지 통과");
