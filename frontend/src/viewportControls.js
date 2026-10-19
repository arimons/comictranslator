// Konva 캔버스 공용 보기 조작: 맞춤/확대/이동/클램프와 휠·Space·휠버튼 처리.
// 말풍선 분석(EditorCanvas)과 글자 입히기(OverlayCanvas)가 같은 규칙을 쓴다.
// - 휠·두 손가락 스크롤 = 이동, Shift+휠 = 가로 이동
// - 핀치(ctrlKey 휠)·Cmd/Ctrl+휠 = 포인터 기준 연속 확대 exp(-deltaY*k)
// - Space 누른 채 드래그, 휠 버튼 드래그, Hand 도구 = 이동

const ZOOM_WHEEL_K = 0.01;
const ZOOM_WHEEL_DELTA_LIMIT = 25;
const MAX_ZOOM_OVER_FIT = 10;
const PAN_MARGIN = 80;

// Space 누름 상태는 앱 전체에서 하나만 둔다. 붙어 있는 viewport가 있을 때만 기본 동작(스크롤·버튼 눌림)을 막는다.
const spaceState = { held: false, installed: false, viewports: new Set() };

function isTypingTarget(target) {
  const tag = target?.tagName?.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || Boolean(target?.isContentEditable);
}

function setSpaceHeld(next) {
  if (spaceState.held === next) return;
  spaceState.held = next;
  spaceState.viewports.forEach((vp) => vp.refreshCursor());
}

function installSpaceTracking() {
  if (spaceState.installed || typeof window === "undefined") return;
  spaceState.installed = true;
  window.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.isComposing || !spaceState.viewports.size) return;
    if (isTypingTarget(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
    event.preventDefault();
    setSpaceHeld(true);
  }, true);
  window.addEventListener("keyup", (event) => {
    if (event.code !== "Space") return;
    if (spaceState.held) event.preventDefault();
    setSpaceHeld(false);
  }, true);
  // 키를 뗀 순간 창이 포커스를 잃으면 keyup이 오지 않는다.
  window.addEventListener("blur", () => setSpaceHeld(false));
}

export function isSpaceHeld() {
  return spaceState.held;
}

function wheelDeltaPixels(event, host) {
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? (host?.clientHeight || 600) : 1;
  return { dx: event.deltaX * unit, dy: event.deltaY * unit };
}

// options
//   getHost(): stage container 요소
//   getImage(): {width, height}를 가진 이미지 (없으면 null)
//   getKey(): 페이지 key. 바뀌면 보기를 맞춤으로 되돌린다.
//   fitPadding: 맞춤 배율에 곱하는 여유 (0.98 등)
//   redraw(): 보기가 바뀌면 호출
//   onStatus(text): 상태 표시
//   isPanTool(): Hand 도구 여부
export function createViewport(options) {
  const fitPadding = options.fitPadding ?? 0.98;
  let view = { key: "", scale: 1, x: 0, y: 0, hostWidth: 0, hostHeight: 0 };
  let pan = null;
  let detach = null;

  function hostSize() {
    const host = options.getHost?.();
    return { width: host?.clientWidth || 800, height: host?.clientHeight || 600 };
  }

  function fitTransform(width, height, img) {
    const scale = Math.min(width / img.width, height / img.height) * fitPadding;
    return { scale, x: (width - img.width * scale) / 2, y: (height - img.height * scale) / 2 };
  }

  function scaleLimits(width, height, img) {
    const fit = fitTransform(width, height, img).scale;
    return { min: fit, max: fit * MAX_ZOOM_OVER_FIT, fit };
  }

  function clamp(next, width, height, img) {
    const limits = scaleLimits(width, height, img);
    const scale = Math.min(limits.max, Math.max(limits.min, next.scale));
    const scaledW = img.width * scale;
    const scaledH = img.height * scale;
    return {
      ...next,
      scale,
      x: scaledW <= width ? (width - scaledW) / 2 : Math.min(PAN_MARGIN, Math.max(width - scaledW - PAN_MARGIN, next.x)),
      y: scaledH <= height ? (height - scaledH) / 2 : Math.min(PAN_MARGIN, Math.max(height - scaledH - PAN_MARGIN, next.y)),
    };
  }

  // draw 시점에 호출한다. 페이지가 바뀌면 맞춤, 크기만 바뀌면 현재 보기를 클램프한다.
  function ensure(width, height, img) {
    const key = options.getKey?.() || "";
    if (view.key !== key || !Number.isFinite(view.scale)) {
      const fit = fitTransform(width, height, img);
      view = { key, ...fit, hostWidth: width, hostHeight: height };
    } else if (view.hostWidth !== width || view.hostHeight !== height) {
      view = clamp({ ...view, hostWidth: width, hostHeight: height }, width, height, img);
    }
    return view;
  }

  function apply(next, statusText) {
    const img = options.getImage?.();
    if (!img) return false;
    const { width, height } = hostSize();
    view = clamp({ ...view, ...next, key: options.getKey?.() || "", hostWidth: width, hostHeight: height }, width, height, img);
    if (statusText) options.onStatus?.(statusText(view));
    options.redraw?.();
    return true;
  }

  const zoomStatus = (v) => `Zoom ${Math.round(v.scale * 100)}%`;

  function zoomAt(point, factor) {
    const img = options.getImage?.();
    if (!img || !Number.isFinite(factor) || factor <= 0) return false;
    const { width, height } = hostSize();
    const limits = scaleLimits(width, height, img);
    const anchor = point || { x: width / 2, y: height / 2 };
    const nextScale = Math.min(limits.max, Math.max(limits.min, view.scale * factor));
    const imagePoint = { x: (anchor.x - view.x) / view.scale, y: (anchor.y - view.y) / view.scale };
    return apply({
      scale: nextScale,
      x: anchor.x - imagePoint.x * nextScale,
      y: anchor.y - imagePoint.y * nextScale,
    }, zoomStatus);
  }

  function fit() {
    const img = options.getImage?.();
    if (!img) return false;
    const { width, height } = hostSize();
    return apply(fitTransform(width, height, img), () => "Zoom fit");
  }

  function zoomTo(scale, point = null) {
    return zoomAt(point, scale / (view.scale || 1));
  }

  function panBy(dx, dy) {
    return apply({ x: view.x + dx, y: view.y + dy });
  }

  // rect(이미지 좌표)가 화면에 꽉 차게 확대한다.
  function zoomToRect(rect, padding = 0.7) {
    const img = options.getImage?.();
    if (!img || !Array.isArray(rect) || rect.length < 4) return false;
    const { width, height } = hostSize();
    const [rx, ry, rw, rh] = rect.map(Number);
    const scale = Math.min(width / Math.max(1, rw), height / Math.max(1, rh)) * padding;
    const limits = scaleLimits(width, height, img);
    const nextScale = Math.min(limits.max, Math.max(limits.min, scale));
    return apply({
      scale: nextScale,
      x: width / 2 - (rx + rw / 2) * nextScale,
      y: height / 2 - (ry + rh / 2) * nextScale,
    }, zoomStatus);
  }

  // rect가 화면 밖에 있으면 배율은 그대로 두고 가운데로 옮긴다.
  function ensureVisible(rect, margin = 24) {
    const img = options.getImage?.();
    if (!img || !Array.isArray(rect) || rect.length < 4) return false;
    const { width, height } = hostSize();
    const [rx, ry, rw, rh] = rect.map(Number);
    const left = view.x + rx * view.scale;
    const top = view.y + ry * view.scale;
    const right = left + rw * view.scale;
    const bottom = top + rh * view.scale;
    if (left >= margin && top >= margin && right <= width - margin && bottom <= height - margin) return false;
    return apply({
      x: width / 2 - (rx + rw / 2) * view.scale,
      y: height / 2 - (ry + rh / 2) * view.scale,
    });
  }

  function refreshCursor() {
    const host = options.getHost?.();
    if (!host) return;
    const wantsGrab = spaceState.held || Boolean(options.isPanTool?.());
    host.classList.toggle("vp-grab", wantsGrab && !pan);
    host.classList.toggle("vp-grabbing", Boolean(pan));
  }

  function localPoint(event, host) {
    const bounds = host.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  }

  function attach() {
    const host = options.getHost?.();
    if (!host || detach) return;
    installSpaceTracking();
    spaceState.viewports.add(api);

    const onWheel = (event) => {
      event.preventDefault();
      const { dx, dy } = wheelDeltaPixels(event, host);
      if (event.ctrlKey || event.metaKey) {
        const limited = Math.max(-ZOOM_WHEEL_DELTA_LIMIT, Math.min(ZOOM_WHEEL_DELTA_LIMIT, dy));
        zoomAt(localPoint(event, host), Math.exp(-limited * ZOOM_WHEEL_K));
        return;
      }
      if (event.shiftKey && !dx) panBy(-dy, 0);
      else panBy(-dx, -dy);
    };

    const onWindowMove = (event) => {
      if (!pan) return;
      event.preventDefault();
      const dx = event.clientX - pan.lastX;
      const dy = event.clientY - pan.lastY;
      pan.lastX = event.clientX;
      pan.lastY = event.clientY;
      if (dx || dy) panBy(dx, dy);
    };
    const endPan = () => {
      if (!pan) return;
      pan = null;
      window.removeEventListener("mousemove", onWindowMove, true);
      window.removeEventListener("mouseup", endPan, true);
      window.removeEventListener("blur", endPan);
      refreshCursor();
    };
    // capture 단계에서 가로채 Konva 쪽 선택·그리기 핸들러가 같은 드래그를 받지 않게 한다.
    const onDown = (event) => {
      const isMiddle = event.button === 1;
      const isPanLeft = event.button === 0 && (spaceState.held || options.isPanTool?.());
      if (!isMiddle && !isPanLeft) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.type !== "mousedown" || pan) return;
      pan = { lastX: event.clientX, lastY: event.clientY };
      window.addEventListener("mousemove", onWindowMove, true);
      window.addEventListener("mouseup", endPan, true);
      window.addEventListener("blur", endPan);
      refreshCursor();
    };
    const blockAux = (event) => {
      if (event.button === 1) event.preventDefault();
    };

    host.addEventListener("wheel", onWheel, { passive: false });
    host.addEventListener("pointerdown", onDown, true);
    host.addEventListener("mousedown", onDown, true);
    host.addEventListener("auxclick", blockAux, true);
    refreshCursor();
    detach = () => {
      endPan();
      host.removeEventListener("wheel", onWheel);
      host.removeEventListener("pointerdown", onDown, true);
      host.removeEventListener("mousedown", onDown, true);
      host.removeEventListener("auxclick", blockAux, true);
      host.classList.remove("vp-grab", "vp-grabbing");
      spaceState.viewports.delete(api);
    };
  }

  const api = {
    get view() {
      return view;
    },
    ensure,
    fit,
    zoom100: (point = null) => zoomTo(1, point),
    zoomBy: (factor, point = null) => zoomAt(point, factor),
    zoomAt,
    panBy,
    zoomToRect,
    ensureVisible,
    refreshCursor,
    isPanning: () => Boolean(pan),
    attach,
    detach: () => {
      detach?.();
      detach = null;
    },
  };
  return api;
}
