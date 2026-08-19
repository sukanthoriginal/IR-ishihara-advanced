/** Shared timing, sizing, and deterministic-random helpers. */

export function median(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2
    ? ordered[middle]
    : (ordered[middle - 1] + ordered[middle]) / 2;
}

export function shuffle(items, rng) {
  for (let index = items.length - 1; index > 0; index -= 1) {
    const other = Math.floor(rng() * (index + 1));
    [items[index], items[other]] = [items[other], items[index]];
  }
  return items;
}

export function mulberry32(seed) {
  return function random() {
    let value = seed += 0x6D2B79F5;
    value = Math.imul(value ^ value >>> 15, value | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}

export function repeatedStimulusDurationMs(sweepDurationMs, repetitions, intervalMs) {
  if (sweepDurationMs <= 0 || repetitions < 1 || intervalMs < 0) return 0;
  return sweepDurationMs * repetitions + intervalMs * (repetitions - 1);
}

export function fitAspectRatio(availableWidth, availableHeight, aspectRatio) {
  if (availableWidth <= 0 || availableHeight <= 0 || aspectRatio <= 0) {
    return { width: 0, height: 0 };
  }
  const width = Math.min(availableWidth, availableHeight * aspectRatio);
  return { width, height: width / aspectRatio };
}

export function compactStageSize({
  availableWidthCssPx,
  availableHeightCssPx,
  nativeWidthPx,
  nativeHeightPx,
  aspectRatio,
}) {
  if (!(nativeWidthPx > 0) || !(nativeHeightPx > 0)) {
    return { width: 0, height: 0 };
  }
  return fitAspectRatio(
    Math.min(availableWidthCssPx, nativeWidthPx),
    Math.min(availableHeightCssPx, nativeHeightPx),
    aspectRatio,
  );
}

export function physicalSizeForVisualAngle(angleDeg, distanceCm) {
  if (!(angleDeg > 0) || !(angleDeg < 180) || !(distanceCm > 0)) return 0;
  return 2 * distanceCm * Math.tan(angleDeg * Math.PI / 360);
}

export function calibratedStageSize({
  targetWidthAngleDeg,
  viewingDistanceCm,
  displayWidthCm,
  screenWidthCssPx,
  availableWidthCssPx,
  availableHeightCssPx,
  aspectRatio,
}) {
  const widthCm = physicalSizeForVisualAngle(targetWidthAngleDeg, viewingDistanceCm);
  if (
    widthCm <= 0
    || !(displayWidthCm > 0)
    || !(screenWidthCssPx > 0)
    || !(availableWidthCssPx > 0)
    || !(availableHeightCssPx > 0)
    || !(aspectRatio > 0)
  ) {
    return { width: 0, height: 0, widthCm: 0, heightCm: 0, fits: false };
  }
  const width = widthCm / displayWidthCm * screenWidthCssPx;
  const height = width / aspectRatio;
  return {
    width,
    height,
    widthCm,
    heightCm: widthCm / aspectRatio,
    fits: width <= availableWidthCssPx + 0.5 && height <= availableHeightCssPx + 0.5,
  };
}

export function visualAngleDeg(sizeCm, distanceCm) {
  if (!(sizeCm > 0) || !(distanceCm > 0)) return null;
  return 2 * Math.atan(sizeCm / (2 * distanceCm)) * 180 / Math.PI;
}
