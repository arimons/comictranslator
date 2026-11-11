// 브라우저 기본 confirm()/prompt()를 대신하는 앱 내 확인창의 요청 창구.
// 어디서든 askConfirm()/askText()를 부르면 <DialogHost />가 창을 띄우고, 사용자가 고르면 Promise가 풀린다.
// 한 번에 하나만 띄운다. 창이 떠 있는 동안 새 요청이 오면 앞의 요청은 취소로 처리한다.

let current = null;
const listeners = new Set();

function publish(next) {
  current = next;
  listeners.forEach((listener) => listener(current));
}

export function subscribeDialog(listener) {
  listeners.add(listener);
  listener(current);
  return () => listeners.delete(listener);
}

function open(options) {
  if (current) current.resolve(current.kind === "text" ? null : false);
  return new Promise((resolve) => {
    publish({ ...options, resolve: (value) => { publish(null); resolve(value); } });
  });
}

// 확인하면 true, 취소·Esc·바깥 클릭이면 false.
// options: { title, message, confirmLabel, cancelLabel, danger }
export function askConfirm(options) {
  return open({ kind: "confirm", ...options });
}

// 입력값(문자열)을 돌려주고, 취소하면 null.
// options: { title, message, initialValue, confirmLabel, placeholder }
export function askText(options) {
  return open({ kind: "text", ...options });
}
