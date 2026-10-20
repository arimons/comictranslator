// 원본 좌표가 아닌 화면 좌표에서 라벨끼리와 영역 상자를 피한다. 이미지/CV 판정은 하지 않는다.
export function promotedTgIds(regions) {
  return new Set(regions.flatMap(region => region.anchor_ids || []).map(Number).filter(id => Number.isFinite(id) && id > 0));
}

function overlap(a, b) {
  return Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x))
    * Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
}

export function placeCanvasLabels(labels, obstacles, image, viewport) {
  const placed = [];
  // Region 우선, 같은 높이에서는 ID 순서로 고정해 확대/다시 그리기 때 불필요한 이동을 줄인다.
  const ordered = [...labels].sort((a, b) => (a.kind === 'region' ? 0 : 1) - (b.kind === 'region' ? 0 : 1) || a.rect.y - b.rect.y || String(a.id).localeCompare(String(b.id), undefined, {numeric:true}));
  for (const item of ordered) {
    const w = Math.min(item.width, Math.max(1, viewport.width - 8)), h = item.height;
    const inlineWidth = Math.min(item.inlineWidth || w, Math.max(1, viewport.width - 8));
    const r = item.rect;
    if (r.x + r.width < 0 || r.y + r.height < 0 || r.x > viewport.width || r.y > viewport.height) continue;
    const right = image.x + image.width + 12;
    const gutter = right >= 0 && viewport.width - right >= w + 8;
    const clamp = (x, y, labelWidth = w) => ({x:Math.max(4, Math.min(viewport.width - labelWidth - 4, x)), y:Math.max(4, Math.min(viewport.height - h - 4, y)), width:labelWidth, height:h});
    const candidates = [];
    if (gutter) {
      for (let y = r.y - h / 2, step = 0; step < Math.ceil(viewport.height / (h + 4)); step++) {
        const shift = Math.ceil(step / 2) * (h + 4) * (step % 2 ? 1 : -1);
        candidates.push({...clamp(right, y + shift), outside:true});
      }
    }
    for (const [x,y] of [[r.x,r.y-h-4],[r.x,r.y+r.height+4],[r.x-inlineWidth-5,r.y],[r.x+r.width+5,r.y],
      [r.x+r.width-inlineWidth,r.y-h-4],[r.x+r.width-inlineWidth,r.y+r.height+4]]) {
      candidates.push({...clamp(x,y,inlineWidth),outside:false});
    }
    let best, score = Infinity;
    for (const candidate of candidates) {
      const collisions = placed.reduce((sum, other) => sum + overlap(candidate, other), 0);
      const covered = obstacles.reduce((sum, other) => sum + overlap(candidate, other), 0);
      const cost = collisions * 1000 + covered * 20 + (candidate.outside ? 0 : 600)
        + Math.abs(candidate.y - r.y) + Math.abs(candidate.x - r.x) * .03;
      if (cost < score) { score = cost; best = candidate; }
    }
    placed.push({...item, ...best});
  }
  return placed;
}
