function glyphIds(value) {
  if (Array.isArray(value)) return value.map(item => String(item));
  if (typeof value === 'string' && value.length) return value.split('|');
  return [];
}

function selected(value) {
  return value === true || Number(value) === 1;
}

const COMPLETE_TARGET_ALIGNED_CONDITIONS = new Set([
  'visual_aligned_silent',
  'visual_aligned_overlay',
  'visual_aligned_ir_audio',
]);

export function conditionRequiresTransformationInference(condition) {
  return !COMPLETE_TARGET_ALIGNED_CONDITIONS.has(condition);
}

export function scoreGlyphResponse(row) {
  const sourceIds = glyphIds(row?.source_ids);
  const targetIds = glyphIds(row?.target_ids);
  const responseIds = glyphIds(row?.response_target_ids);
  if (
    targetIds.length === 0
    || sourceIds.length !== targetIds.length
    || responseIds.length !== targetIds.length
  ) {
    return { valid: false };
  }

  let glyphCorrect = 0;
  let transformedTotal = 0;
  let transformedCorrect = 0;
  let unchangedTotal = 0;
  let unchangedCorrect = 0;
  for (let index = 0; index < targetIds.length; index += 1) {
    const correct = responseIds[index] === targetIds[index];
    const transformed = sourceIds[index] !== targetIds[index];
    glyphCorrect += correct ? 1 : 0;
    if (transformed) {
      transformedTotal += 1;
      transformedCorrect += correct ? 1 : 0;
    } else {
      unchangedTotal += 1;
      unchangedCorrect += correct ? 1 : 0;
    }
  }

  return {
    valid: true,
    glyphTotal: targetIds.length,
    glyphCorrect,
    transformedTotal,
    transformedCorrect,
    unchangedTotal,
    unchangedCorrect,
    missedGlyphs: targetIds.length - glyphCorrect,
  };
}

export function summarizeTrialRows(rows) {
  const result = {
    plateTotal: rows.length,
    plateCorrect: 0,
    glyphTotal: 0,
    glyphCorrect: 0,
    transformedTotal: 0,
    transformedCorrect: 0,
    unchangedTotal: 0,
    unchangedCorrect: 0,
    decoySelected: 0,
    correctRts: [],
    missedGlyphCounts: {},
    invalidGlyphRows: 0,
  };

  for (const row of rows) {
    const plateCorrect = selected(row?.correct);
    result.plateCorrect += plateCorrect ? 1 : 0;
    result.decoySelected += selected(row?.decoy_selected) ? 1 : 0;
    const responseTime = Number(row?.rt_choice_onset_ms);
    if (plateCorrect && Number.isFinite(responseTime)) {
      result.correctRts.push(responseTime);
    }

    const glyphScore = scoreGlyphResponse(row);
    if (!glyphScore.valid) {
      result.invalidGlyphRows += 1;
      continue;
    }
    result.glyphTotal += glyphScore.glyphTotal;
    result.glyphCorrect += glyphScore.glyphCorrect;
    result.transformedTotal += glyphScore.transformedTotal;
    result.transformedCorrect += glyphScore.transformedCorrect;
    result.unchangedTotal += glyphScore.unchangedTotal;
    result.unchangedCorrect += glyphScore.unchangedCorrect;
    const key = String(glyphScore.missedGlyphs);
    result.missedGlyphCounts[key] = (result.missedGlyphCounts[key] || 0) + 1;
  }

  return result;
}
