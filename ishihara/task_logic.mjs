export function buildSchedule(manifest, settings, rng) {
  const eligible = manifest.stimuli.filter(item => (
    item.split === settings.split
    && (settings.complexity === 'curriculum'
      || item.complexity_level === Number(settings.complexity))
    && (settings.channelRecipe === 'curriculum'
      || item.channel_recipe_id === settings.channelRecipe)
    && !(
      settings.complexity === 'curriculum'
      && settings.channelRecipe === 'curriculum'
      && manifest.curriculum_recipe_by_complexity[String(item.complexity_level)]
        !== item.channel_recipe_id
    )
  ));
  if (!eligible.length) throw new Error(`no stimuli found for ${settings.split} split`);

  if (settings.condition === 'mixed') {
    // Exact pairing: the diagnostic feature is presented once in a visible
    // probe colour and once in IR. Scaffold, background, and dot layout remain
    // identical. Presentations occupy opposite block halves with balanced order.
    if (settings.numTrials % 2) throw new Error('mixed blocks require an even trial count');
    const pairCount = settings.numTrials / 2;
    const selected = selectStimuli(eligible, pairCount, manifest, settings, rng);
    const firstConditions = Array.from(
      { length: pairCount },
      (_, index) => index % 2 === 0 ? 'visual-composite' : 'ir-composite',
    );
    shuffle(firstConditions, rng);

    const pairs = selected.map((stimulus, pairIndex) => {
      const firstCondition = firstConditions[pairIndex];
      const secondCondition = firstCondition === 'visual-composite'
        ? 'ir-composite'
        : 'visual-composite';
      const pairId = `${pairIndex + 1}:${stimulus.stimulus_id}`;
      return {
        first: {
          stimulus, condition: firstCondition, pairId, pairPosition: 1,
          pairConditionOrder: `${firstCondition}-${secondCondition}`,
        },
        second: {
          stimulus, condition: secondCondition, pairId, pairPosition: 2,
          pairConditionOrder: `${firstCondition}-${secondCondition}`,
        },
      };
    });

    const firstHalf = pairs.map(pair => pair.first);
    const secondHalf = pairs.map(pair => pair.second);
    return [...firstHalf, ...secondHalf].map(trial => ({
      ...trial,
      choices: choicesForStimulus(manifest, trial.stimulus, rng),
    }));
  }

  return selectStimuli(eligible, settings.numTrials, manifest, settings, rng).map(stimulus => ({
    stimulus,
    condition: settings.condition,
    choices: choicesForStimulus(manifest, stimulus, rng),
  }));
}

function choicesForStimulus(manifest, stimulus, rng) {
  return shuffle([...stimulus.response_choices], rng);
}

function selectStimuli(eligible, count, manifest, settings, rng) {
  if (settings.complexity !== 'curriculum' && settings.channelRecipe !== 'curriculum') {
    const pool = [];
    while (pool.length < count) pool.push(...shuffle([...eligible], rng));
    return pool.slice(0, count);
  }

  const recipeOrder = Object.keys(manifest.channel_recipes);
  const rankFor = stimulus => (
    settings.complexity === 'curriculum'
      ? stimulus.complexity_level
      : recipeOrder.indexOf(stimulus.channel_recipe_id) + 1
  );
  const ranks = [...new Set(eligible.map(rankFor))].sort((a, b) => a - b);
  const baseCount = Math.floor(count / ranks.length);
  let remainder = count % ranks.length;
  const selected = [];

  for (const rank of ranks) {
    const groupCount = baseCount + (remainder > 0 ? 1 : 0);
    remainder = Math.max(0, remainder - 1);
    const group = eligible.filter(stimulus => rankFor(stimulus) === rank);
    const pool = [];
    while (pool.length < groupCount) pool.push(...shuffle([...group], rng));
    selected.push(...pool.slice(0, groupCount));
  }
  return selected;
}

export function shuffle(items, rng) {
  for (let i = items.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    [items[i], items[j]] = [items[j], items[i]];
  }
  return items;
}

export function median(values) {
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
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
  if (
    !(nativeWidthPx > 0)
    || !(nativeHeightPx > 0)
  ) {
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
  const widthCm = physicalSizeForVisualAngle(
    targetWidthAngleDeg, viewingDistanceCm,
  );
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

export function repeatedStimulusDurationMs(sweepDurationMs, repetitions, intervalMs) {
  if (sweepDurationMs <= 0 || repetitions < 1 || intervalMs < 0) return 0;
  return sweepDurationMs * repetitions + intervalMs * (repetitions - 1);
}

export function repeatedSweepPosition(elapsedMs, sweepDurationMs, repetitions, intervalMs) {
  const totalDurationMs = repeatedStimulusDurationMs(
    sweepDurationMs, repetitions, intervalMs,
  );
  if (elapsedMs < 0 || totalDurationMs === 0) {
    return { complete: false, repetitionIndex: -1, sweepActive: false, sweepElapsedMs: 0 };
  }
  if (elapsedMs >= totalDurationMs) {
    return { complete: true, repetitionIndex: repetitions, sweepActive: false, sweepElapsedMs: 0 };
  }
  const strideMs = sweepDurationMs + intervalMs;
  const repetitionIndex = Math.min(repetitions - 1, Math.floor(elapsedMs / strideMs));
  const sweepElapsedMs = elapsedMs - repetitionIndex * strideMs;
  return {
    complete: false,
    repetitionIndex,
    sweepActive: sweepElapsedMs < sweepDurationMs,
    sweepElapsedMs,
  };
}

export function bsplineSweepWeights(sampleIndex, columns, samplesPerColumn) {
  const sample = Math.max(0, Math.floor(sampleIndex));
  const column = Math.min(columns - 1, Math.floor(sample / samplesPerColumn));
  const q = (sample % samplesPerColumn) / (samplesPerColumn - 1);
  const q2 = 0.5 * q * q;
  const previousWeight = q2 - q + 0.5;
  const currentWeight = 0.5 + q - q * q;

  if (column === 0) {
    return [{ column: 0, weight: 1 - q2 }, { column: 1, weight: q2 }];
  }
  if (column === columns - 1) {
    return [
      { column: column - 1, weight: previousWeight },
      { column, weight: currentWeight },
    ];
  }
  return [
    { column: column - 1, weight: previousWeight },
    { column, weight: currentWeight },
    { column: column + 1, weight: q2 },
  ];
}

export function mulberry32(seed) {
  return function random() {
    let value = seed += 0x6D2B79F5;
    value = Math.imul(value ^ value >>> 15, value | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}
