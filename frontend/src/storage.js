// 브라우저 저장소(localStorage)에 화면 설정을 JSON으로 읽고 쓴다.
// 다른 모듈을 import하지 않는다(proof/src 테스트가 data URL로 불러온다).

// 브라우저 저장소는 막혀 있을 수 있어 모든 읽기·쓰기를 감싼다. 값이 없거나 올바르지 않으면 기본값을 쓴다.
export function readStored(key, fallback, valid = () => true) {
  try {
    const value = JSON.parse(window.localStorage.getItem(key));
    return value !== null && valid(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

export function writeStored(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* 저장소를 쓸 수 없어도 기능은 동작한다 */ }
}
