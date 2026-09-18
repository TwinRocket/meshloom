export interface ViewPoint {
  x: number;
  y: number;
}

export function pointInViewport(point: ViewPoint, width: number, height: number): boolean {
  if (width <= 0 || height <= 0) return false;
  return point.x >= 0 && point.x <= width && point.y >= 0 && point.y <= height;
}

function outCode(x: number, y: number, width: number, height: number): number {
  let code = 0;
  if (x < 0) code |= 1;
  else if (x > width) code |= 2;
  if (y < 0) code |= 4;
  else if (y > height) code |= 8;
  return code;
}

export function segmentIntersectsViewport(
  from: ViewPoint,
  to: ViewPoint,
  width: number,
  height: number
): boolean {
  if (width <= 0 || height <= 0) return false;
  if (pointInViewport(from, width, height) || pointInViewport(to, width, height)) return true;

  let x0 = from.x;
  let y0 = from.y;
  let x1 = to.x;
  let y1 = to.y;
  let out0 = outCode(x0, y0, width, height);
  let out1 = outCode(x1, y1, width, height);

  while (out0 | out1) {
    if (out0 & out1) return false;
    const out = out0 || out1;
    let x = 0;
    let y = 0;
    if (out & 8) {
      x = x0 + ((x1 - x0) * (height - y0)) / (y1 - y0 || 1);
      y = height;
    } else if (out & 4) {
      x = x0 + ((x1 - x0) * (0 - y0)) / (y1 - y0 || 1);
      y = 0;
    } else if (out & 2) {
      y = y0 + ((y1 - y0) * (width - x0)) / (x1 - x0 || 1);
      x = width;
    } else {
      y = y0 + ((y1 - y0) * (0 - x0)) / (x1 - x0 || 1);
      x = 0;
    }
    if (out === out0) {
      x0 = x;
      y0 = y;
      out0 = outCode(x0, y0, width, height);
    } else {
      x1 = x;
      y1 = y;
      out1 = outCode(x1, y1, width, height);
    }
  }
  return true;
}

export function laserEdgeVisible(
  from: ViewPoint,
  to: ViewPoint | null,
  width: number,
  height: number
): boolean {
  if (!to || (from.x === to.x && from.y === to.y)) {
    return pointInViewport(from, width, height);
  }
  return segmentIntersectsViewport(from, to, width, height);
}
