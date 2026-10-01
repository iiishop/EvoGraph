export type ViewportSize = { width: number; height: number };
export type GraphBox = { x: number; y: number; width: number; height: number };
export type GraphViewport = { x: number; y: number; zoom: number };

function innerViewport(size: ViewportSize) {
  const left = Math.min(24, size.width / 4);
  const top = Math.min(24, size.height / 4);
  const right = size.width - left;
  // Keep the bottom-left canvas controls outside the fitted node area.
  const bottom = size.height - Math.min(64, size.height / 4);
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}

export function graphBounds(boxes: GraphBox[]): GraphBox | null {
  if (!boxes.length) return null;
  const x = Math.min(...boxes.map((box) => box.x));
  const y = Math.min(...boxes.map((box) => box.y));
  return {
    x,
    y,
    width: Math.max(...boxes.map((box) => box.x + box.width)) - x,
    height: Math.max(...boxes.map((box) => box.y + box.height)) - y,
  };
}

export function fitMilestoneBounds(
  bounds: GraphBox | null,
  size: ViewportSize,
  maxZoom = 1,
): GraphViewport | null {
  if (!bounds || size.width <= 0 || size.height <= 0) return null;
  const inner = innerViewport(size);
  const zoom = Math.min(
    maxZoom,
    inner.width / Math.max(bounds.width, 1),
    inner.height / Math.max(bounds.height, 1),
  );
  return {
    x: inner.left + (inner.width - bounds.width * zoom) / 2 - bounds.x * zoom,
    y: inner.top + (inner.height - bounds.height * zoom) / 2 - bounds.y * zoom,
    zoom,
  };
}

export function keepMilestoneVisible(
  box: GraphBox,
  size: ViewportSize,
  current: GraphViewport,
): GraphViewport | null {
  const fitted = fitMilestoneBounds(box, size, current.zoom);
  if (!fitted) return null;
  if (fitted.zoom < current.zoom) return fitted;
  const inner = innerViewport(size);
  let { x, y } = current;
  const left = box.x * current.zoom + x;
  const right = left + box.width * current.zoom;
  const top = box.y * current.zoom + y;
  const bottom = top + box.height * current.zoom;
  if (left < inner.left) x += inner.left - left;
  else if (right > inner.right) x -= right - inner.right;
  if (top < inner.top) y += inner.top - top;
  else if (bottom > inner.bottom) y -= bottom - inner.bottom;
  return { x, y, zoom: current.zoom };
}
