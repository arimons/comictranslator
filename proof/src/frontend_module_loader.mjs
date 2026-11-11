// frontend/src의 ES 모듈을 node --test에서 불러오는 도우미.
// frontend/package.json에 "type": "module"이 없어 .js를 바로 import할 수 없으므로,
// 상대 경로 import("./x.js")를 재귀적으로 data URL로 바꿔 넣은 뒤 data URL로 불러온다.
// 테스트 파일 이름(test_*.mjs)이 아니라서 npm test가 이 파일 자체를 실행하지는 않는다.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";

function inlineModule(path, cache) {
  if (cache.has(path)) return cache.get(path);
  const source = readFileSync(path, "utf8").replace(/from\s+"(\.\/[^"]+\.js)"/g, (_match, rel) => (
    `from "${inlineModule(join(dirname(path), rel), cache)}"`
  ));
  const url = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
  cache.set(path, url);
  return url;
}

// 저장소 루트 기준 경로(예: "frontend/src/storage.js")를 받는다. npm test는 저장소 루트에서 돈다.
export async function loadFrontendModule(path) {
  return import(inlineModule(path, new Map()));
}
