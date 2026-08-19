import {
  calibratedStageSize,
  compactStageSize,
  fitAspectRatio,
  median,
  visualAngleDeg,
} from '../shared/timing.mjs';
import { safeFilenamePart, saveCsv } from '../shared/csv.mjs';

const ASPECT_RATIO = 178 / 64;
const NATIVE_WIDTH = 712;
const NATIVE_HEIGHT = 256;
const MAX_UINT32 = 0xFFFFFFFF;
const ESTIMATED_RESPONSE_SECONDS = 2;

const setupScreen = document.getElementById('setup-screen');
const trialScreen = document.getElementById('trial-screen');
const endScreen = document.getElementById('end-screen');
const prepareButton = document.getElementById('prepare-btn');
const prepareStatus = document.getElementById('prepare-status');
const feedbackWarning = document.getElementById('feedback-warning');
const preparedPanel = document.getElementById('prepared-panel');
const preparedSummary = document.getElementById('prepared-summary');
const startButton = document.getElementById('start-btn');
const stageShell = document.getElementById('stage-shell');
const stimulusImage = document.getElementById('stimulus-image');
const mask = document.getElementById('mask');
const readyOverlay = document.getElementById('ready-overlay');
const readyCopy = document.getElementById('ready-copy');
const readyButton = document.getElementById('ready-btn');
const choices = document.getElementById('choices');
const feedback = document.getElementById('feedback');
const trialStatus = document.getElementById('trial-status');
const conditionStatus = document.getElementById('condition-status');
const summary = document.getElementById('summary');
const saveButton = document.getElementById('save-btn');
const saveStatus = document.getElementById('save-status');

let manifest = null;
let manifestUrl = null;
let manifestBaseUrl = null;
let stimulusById = new Map();
let session = null;
let trialIndex = 0;
let phase = 'setup';
let trialRows = [];
let audioContext = null;
let audioBuffers = new Map();
let activeAudioSources = [];
let phaseTimer = null;
let responseOnsetMs = null;
let stimulusOnsetMs = null;
let audioPlannedOnsetMs = null;
let currentAttempt = null;
let invalidAttempts = 0;

prepareButton.addEventListener('click', prepareBlock);
startButton.addEventListener('click', startBlock);
readyButton.addEventListener('click', () => beginTrial('pointer'));
saveButton.addEventListener('click', saveResults);
document.getElementById('new-btn').addEventListener('click', resetApp);
document.getElementById('presentation').addEventListener('change', updateCalibrationVisibility);
document.getElementById('new-seed-btn').addEventListener('click', () => {
  setFreshRunCode();
  updateSetupPreview();
  invalidatePreparedBlock();
});
for (const id of [
  'split', 'signal-mode', 'base-stimulus-count', 'progression',
  'feedback-enabled', 'glyph-composition', 'seed',
]) {
  document.getElementById(id).addEventListener('input', () => {
    updateSetupPreview();
    invalidatePreparedBlock();
  });
}
window.addEventListener('resize', () => {
  fitStage();
  if (['stimulus', 'mask', 'response'].includes(phase)) invalidateAttempt('viewport_resized');
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' && ['stimulus', 'mask', 'response'].includes(phase)) {
    invalidateAttempt('page_hidden');
  }
});
document.addEventListener('keydown', event => {
  if (phase === 'ready' && session?.responseDevice === 'keyboard') {
    event.preventDefault();
    beginTrial('keyboard');
    return;
  }
  if (
    phase === 'response'
    && session?.responseDevice === 'keyboard'
    && /^[1-4]$/.test(event.key)
  ) {
    const responsePosition = Number(event.key);
    const choice = currentAttempt?.responseChoices?.[responsePosition - 1];
    if (!choice) return;
    event.preventDefault();
    recordChoice(choice, responsePosition, 'keyboard');
  }
});

setFreshRunCode();
updateCalibrationVisibility();
updateSetupPreview();
fitStage();

async function prepareBlock() {
  const participantId = document.getElementById('participant-id').value.trim();
  const split = document.getElementById('split').value;
  const signalMode = document.getElementById('signal-mode').value;
  const baseStimulusCount = Number(document.getElementById('base-stimulus-count').value);
  const glyphComposition = document.getElementById('glyph-composition').value;
  const progression = document.getElementById('progression').value;
  const feedbackEnabled = document.getElementById('feedback-enabled').value === 'on';
  const seedValue = document.getElementById('seed').value.trim();
  const seed = seedValue === '' ? NaN : Number(seedValue);
  if (!participantId) return showPrepareError('Participant ID is required.');
  if (!Number.isInteger(baseStimulusCount) || baseStimulusCount < 4 || baseStimulusCount > 96) {
    return showPrepareError('Stimuli must be an integer from 4 to 96.');
  }
  if (!Number.isInteger(seed) || seed < 0 || seed > MAX_UINT32) {
    return showPrepareError('Run code must be a whole number from 0 to 4294967295.');
  }

  prepareButton.disabled = true;
  preparedPanel.classList.add('hidden');
  prepareStatus.className = 'status';
  prepareStatus.textContent = {
    mixed: 'Generating the carrier-controlled visual/IR schedule and audio cache…',
    visual: 'Generating the frozen silent visual schedule…',
    ir: 'Generating the frozen IR-audio schedule and audio cache…',
    paired: 'Generating the repeated-pair schedule and complete audio cache…',
  }[signalMode];
  try {
    const response = await fetch('/api/prepare-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        split,
        signalMode,
        baseStimulusCount,
        glyphComposition,
        progression,
        feedbackEnabled,
        seed,
      }),
    });
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || `server returned ${response.status}`);
    manifestUrl = new URL(info.manifestUrl, window.location.href).href;
    manifestBaseUrl = new URL('.', manifestUrl).href;
    const manifestResponse = await fetch(manifestUrl, { cache: 'no-store' });
    if (!manifestResponse.ok) throw new Error('Generated manifest could not be loaded.');
    manifest = await manifestResponse.json();
    stimulusById = new Map(manifest.stimuli.map(item => [item.stimulus_id, item]));
    prepareStatus.textContent = 'Preloading every image and audio buffer…';
    await preloadAssets();
    prepareStatus.className = 'status good';
    prepareStatus.textContent = 'Block is frozen and fully preloaded.';
    const preparedStimuli = manifest.settings.baseStimulusCount;
    preparedSummary.textContent = `${preparedStimuli} stimuli · ${manifest.trials.length} presentations · ${humanSignalMode(manifest.settings.signalMode)}`;
    preparedPanel.classList.remove('hidden');
  } catch (error) {
    showPrepareError(String(error.message || error));
  } finally {
    prepareButton.disabled = false;
  }
}

function updateSetupPreview() {
  const split = document.getElementById('split').value;
  const signalMode = document.getElementById('signal-mode').value;
  const baseStimulusCount = Number(document.getElementById('base-stimulus-count').value);
  const glyphComposition = document.getElementById('glyph-composition').value;
  const feedbackEnabled = document.getElementById('feedback-enabled').value === 'on';
  const seedValue = document.getElementById('seed').value.trim();
  const seed = seedValue === '' ? NaN : Number(seedValue);
  const validCount = Number.isInteger(baseStimulusCount)
    && baseStimulusCount >= 4
    && baseStimulusCount <= 96;
  const validSeed = Number.isInteger(seed) && seed >= 0 && seed <= MAX_UINT32;
  const presentationCount = validCount
    ? baseStimulusCount * (signalMode === 'paired' ? 2 : 1)
    : null;

  document.getElementById('preview-stimuli').textContent = validCount
    ? String(baseStimulusCount)
    : 'Enter 4–96';
  document.getElementById('preview-presentations').textContent = presentationCount === null
    ? '—'
    : signalMode === 'paired'
      ? `${baseStimulusCount} × 2 = ${presentationCount}`
      : String(presentationCount);
  document.getElementById('preview-glyphs').textContent = validCount && validSeed
    ? formatGlyphDistribution(baseStimulusCount, glyphComposition, seed)
    : '—';
  document.getElementById('preview-conditions').textContent = (
    validCount && (signalMode !== 'mixed' || validSeed)
  )
    ? formatConditionDistribution(baseStimulusCount, signalMode, seed)
    : '—';
  document.getElementById('preview-duration').textContent = presentationCount === null
    ? '—'
    : formatEstimatedDuration(presentationCount, feedbackEnabled);
  document.getElementById('preview-source').textContent = split === 'train'
    ? 'Training · 13/19 families'
    : 'Held-out · 6/19 families';
  document.getElementById('preview-feedback').textContent = feedbackEnabled ? 'On' : 'Off';
  document.getElementById('preview-run-code').textContent = validSeed ? String(seed) : 'Invalid';

  const warnings = [];
  if (split === 'test' && feedbackEnabled) {
    warnings.push('Feedback exposes held-out mappings and may invalidate generalisation testing.');
  }
  if (signalMode === 'paired' && feedbackEnabled) {
    warnings.push('In paired mode, feedback can reveal an answer before its repeated presentation.');
  }
  feedbackWarning.textContent = `${warnings.join(' ')}${warnings.length ? ' You can still generate this block.' : ''}`;
  feedbackWarning.classList.toggle('hidden', warnings.length === 0);
}

function formatGlyphDistribution(count, glyphComposition, seed) {
  if (glyphComposition !== 'automatic') {
    return `${count} × ${glyphComposition}-glyph`;
  }
  const counts = [Math.floor(count / 3), Math.floor(count / 3), Math.floor(count / 3)];
  for (let index = 0; index < count % 3; index += 1) {
    counts[(seed % 3 + index) % 3] += 1;
  }
  return counts.map((value, index) => `${value} × ${index + 1}`).join(' · ');
}

function formatConditionDistribution(count, signalMode, seed) {
  if (signalMode === 'visual') return `${count} visual baseline · silent`;
  if (signalMode === 'ir') return `${count} source scaffold + IR audio`;
  if (signalMode === 'paired') {
    return `${count} visual + neutral carrier · ${count} scaffold + IR · repeated`;
  }
  const extraIsVisual = seed % 2 === 0;
  const visualCount = Math.floor(count / 2) + (count % 2 && extraIsVisual ? 1 : 0);
  const irCount = count - visualCount;
  const seededExtra = count % 2
    ? ` · seeded extra: ${extraIsVisual ? 'visual + neutral carrier' : 'scaffold + IR'}`
    : '';
  return `${visualCount} visual + neutral carrier · ${irCount} scaffold + IR${seededExtra}`;
}

function formatEstimatedDuration(presentationCount, feedbackEnabled) {
  const feedbackSeconds = feedbackEnabled ? 0.65 : 0.18;
  const totalSeconds = Math.round(presentationCount * (
    3.65 + 0.22 + ESTIMATED_RESPONSE_SECONDS + feedbackSeconds
  ));
  if (totalSeconds < 60) return `≈ ${totalSeconds} sec`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `≈ ${minutes} min${seconds ? ` ${seconds} sec` : ''}`;
}

function setFreshRunCode() {
  const values = new Uint32Array(1);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(values);
  else values[0] = Math.floor(Math.random() * (MAX_UINT32 + 1));
  document.getElementById('seed').value = String(values[0]);
}

async function preloadAssets() {
  const imagePaths = new Set();
  const audioPaths = new Set();
  for (const trial of manifest.trials) imagePaths.add(trial.plate_png);
  for (const stimulus of manifest.stimuli) {
    for (const choice of stimulus.response_choices) imagePaths.add(choice.png);
  }
  await Promise.all([...imagePaths].map(path => preloadImage(assetUrl(path))));

  for (const trial of manifest.trials) {
    if (trial.audio_wav) audioPaths.add(trial.audio_wav);
  }
  audioBuffers = new Map();
  if (!audioPaths.size) return;
  audioContext ||= new AudioContext();
  await audioContext.resume();
  await Promise.all([...audioPaths].map(async path => {
    const response = await fetch(assetUrl(path));
    if (!response.ok) throw new Error(`Audio preload failed: ${path}`);
    const buffer = await audioContext.decodeAudioData(await response.arrayBuffer());
    audioBuffers.set(path, buffer);
  }));
  if (audioBuffers.size !== audioPaths.size) throw new Error('Not every soundscape decoded.');
}

async function startBlock() {
  if (!manifest) return;
  session = readSessionSettings();
  const calibrationError = validateCalibration(session);
  if (calibrationError) return showPrepareError(calibrationError);
  if (audioContext) await audioContext.resume();
  if (session.presentation !== 'compact' && !document.fullscreenElement) {
    await document.documentElement.requestFullscreen().catch(() => {});
  }
  session.startedAt = new Date().toISOString();
  trialRows = [];
  trialIndex = 0;
  invalidAttempts = 0;
  setupScreen.classList.add('hidden');
  endScreen.classList.add('hidden');
  trialScreen.classList.remove('hidden');
  fitStage();
  showReady();
}

function readSessionSettings() {
  return {
    participantId: document.getElementById('participant-id').value.trim(),
    split: manifest.settings.split,
    signalMode: manifest.settings.signalMode,
    baseStimulusCount: manifest.settings.baseStimulusCount,
    glyphComposition: manifest.settings.glyphComposition,
    progression: manifest.settings.progression,
    feedbackEnabled: manifest.settings.feedbackEnabled,
    responseDevice: document.getElementById('response-device').value,
    presentation: document.getElementById('presentation').value,
    displayId: document.getElementById('display-id').value.trim(),
    displayWidthCm: numberOrNull('display-width-cm'),
    viewingDistanceCm: numberOrNull('viewing-distance-cm'),
    targetAngleDeg: numberOrNull('target-angle-deg'),
    seed: manifest.settings.seed,
    difficultyModelVersion: manifest.difficulty_model_version,
  };
}

function invalidatePreparedBlock() {
  if (!manifest || phase !== 'setup') return;
  manifest = null;
  manifestUrl = null;
  manifestBaseUrl = null;
  stimulusById = new Map();
  audioBuffers = new Map();
  preparedPanel.classList.add('hidden');
  prepareStatus.className = 'status';
  prepareStatus.textContent = 'Settings changed. Generate and preload the block again.';
}

function validateCalibration(settings) {
  if (settings.presentation !== 'calibrated') return null;
  if (!settings.displayId) return 'Calibrated presentation requires a display ID.';
  if (!(settings.displayWidthCm > 0) || !(settings.viewingDistanceCm > 0)) {
    return 'Calibrated presentation requires display width and viewing distance.';
  }
  return null;
}

function showReady() {
  phase = 'ready';
  stopAudio();
  hide(stimulusImage, mask, choices);
  feedback.textContent = '';
  readyOverlay.classList.remove('hidden');
  readyCopy.textContent = session.responseDevice === 'keyboard'
    ? 'Press any key to start. Use keys 1–4 to answer.'
    : 'Click Start trial. Then click one of four interpretations.';
  readyButton.classList.toggle('hidden', session.responseDevice === 'keyboard');
  const trial = manifest.trials[trialIndex];
  trialStatus.textContent = `Trial ${trialIndex + 1} / ${manifest.trials.length}`;
  conditionStatus.textContent = humanCondition(trial.condition);
}

async function beginTrial(startMethod) {
  if (phase !== 'ready') return;
  phase = 'arming';
  readyOverlay.classList.add('hidden');
  const trial = manifest.trials[trialIndex];
  const stimulus = stimulusById.get(trial.stimulus_id);
  currentAttempt = {
    trial,
    stimulus,
    startMethod,
    invalidReasons: [],
    stage: null,
    visualActualDurationMs: null,
    responseChoices: resolveResponseChoices(trial, stimulus),
  };
  renderChoices(currentAttempt.responseChoices);
  stimulusImage.src = assetUrl(trial.plate_png);
  fitStage();

  let delayBeforeVisualMs = 0;
  audioPlannedOnsetMs = null;
  if (trial.audio_wav) {
    const buffer = audioBuffers.get(trial.audio_wav);
    if (!buffer) return invalidateAttempt('missing_preloaded_audio');
    const scheduled = scheduleRepeatedAudio(buffer);
    audioPlannedOnsetMs = scheduled.performanceOnsetMs;
    delayBeforeVisualMs = Math.max(0, scheduled.performanceOnsetMs - performance.now());
  }
  if (delayBeforeVisualMs > 1) await wait(delayBeforeVisualMs);
  await nextFrame();
  if (phase !== 'arming') return;
  phase = 'stimulus';
  stimulusOnsetMs = performance.now();
  stimulusImage.classList.remove('hidden');
  phaseTimer = window.setTimeout(endStimulus, manifest.stimulus_duration_ms);
}

async function endStimulus() {
  if (phase !== 'stimulus') return;
  currentAttempt.visualActualDurationMs = performance.now() - stimulusOnsetMs;
  stimulusImage.classList.add('hidden');
  mask.classList.remove('hidden');
  phase = 'mask';
  phaseTimer = window.setTimeout(showChoices, manifest.mask_duration_ms);
}

async function showChoices() {
  if (phase !== 'mask') return;
  mask.classList.add('hidden');
  choices.classList.remove('hidden');
  await nextFrame();
  responseOnsetMs = performance.now();
  phase = 'response';
}

function resolveResponseChoices(trial, stimulus) {
  if (!Array.isArray(trial.response_choice_ids)) return stimulus.response_choices;
  const byId = new Map(stimulus.response_choices.map(choice => [choice.choice_id, choice]));
  const ordered = trial.response_choice_ids.map(choiceId => byId.get(choiceId));
  return ordered.length === stimulus.response_choices.length && ordered.every(Boolean)
    ? ordered
    : stimulus.response_choices;
}

function renderChoices(responseChoices) {
  choices.replaceChildren();
  responseChoices.forEach((choice, index) => {
    const button = document.createElement('button');
    button.className = 'choice';
    button.dataset.position = String(index + 1);
    button.dataset.choiceId = choice.choice_id;
    button.tabIndex = session?.responseDevice === 'pointer' ? 0 : -1;
    button.innerHTML = `<span class="number">${index + 1}</span><img alt="Interpretation ${index + 1}" src="${assetUrl(choice.png)}">`;
    button.addEventListener('pointerup', () => {
      if (session?.responseDevice === 'pointer') recordChoice(choice, index + 1, 'pointer');
    });
    choices.append(button);
  });
}

async function recordChoice(choice, responsePosition, responseInputMethod) {
  if (phase !== 'response' || responseInputMethod !== session?.responseDevice) return;
  phase = 'feedback';
  const responseMs = performance.now();
  const { trial, stimulus, startMethod } = currentAttempt;
  const correct = choice.choice_id === stimulus.target_choice_id;
  const decoySelected = choice.choice_id === stimulus.decoy_choice_id;
  const stageAudit = auditStage();
  trialRows.push({
    participant_id: session.participantId,
    session_id: manifest.session_id,
    session_started_at: session.startedAt,
    session_seed: manifest.settings.seed,
    schema_version: manifest.schema_version,
    catalog_version: manifest.catalog_version,
    task_name: manifest.task,
    source_split: manifest.settings.split,
    experiment_mode: manifest.settings.signalMode,
    signal_mode: manifest.settings.signalMode,
    comparison_design: manifest.comparison_design,
    stimuli_repeated_across_conditions: manifest.stimuli_repeated_across_conditions ? 1 : 0,
    base_stimulus_count: manifest.settings.baseStimulusCount,
    glyph_composition: manifest.settings.glyphComposition,
    trial_progression: manifest.settings.progression,
    feedback_enabled: manifest.settings.feedbackEnabled ? 1 : 0,
    total_presentation_count: manifest.total_presentation_count,
    glyph_quota_1: manifest.glyph_count_distribution?.['1'],
    glyph_quota_2: manifest.glyph_count_distribution?.['2'],
    glyph_quota_3: manifest.glyph_count_distribution?.['3'],
    condition_count_visual_silent: manifest.condition_distribution?.visual_silent || 0,
    condition_count_visual_background_audio: (
      manifest.condition_distribution?.visual_background_audio || 0
    ),
    condition_count_ir_audio: manifest.condition_distribution?.ir_audio || 0,
    condition_assignment_method: manifest.condition_assignment?.method,
    difficulty_model_version: manifest.difficulty_model_version,
    condition: trial.condition,
    pair_id: trial.pair_id,
    pair_position: trial.pair_position,
    pair_order: trial.pair_order,
    pair_pass: trial.pair_pass,
    pair_lag: trial.pair_lag,
    trial_index: trial.trial_index,
    stimulus_id: stimulus.stimulus_id,
    source_ids: stimulus.source_ids,
    target_ids: stimulus.target_ids,
    mapping_ids: stimulus.mapping_ids,
    changed_count: stimulus.changed_count,
    glyph_count: stimulus.source_ids.length,
    transformation_signature: stimulus.transformation_signature,
    mapping_repetition_index: stimulus.mapping_repetition_index,
    estimated_difficulty_score: stimulus.estimated_difficulty_score,
    difficulty_rank: stimulus.difficulty_rank,
    difficulty_stratum: stimulus.difficulty_stratum,
    difficulty_glyph_load: stimulus.difficulty_components?.glyph_load,
    difficulty_diagnostic_subtlety: stimulus.difficulty_components?.diagnostic_subtlety,
    difficulty_alternative_foil_similarity: stimulus.difficulty_components?.alternative_foil_similarity,
    difficulty_family_ambiguity: stimulus.difficulty_components?.family_ambiguity,
    difficulty_source_pixel_count: stimulus.difficulty_inputs?.source_pixel_count,
    difficulty_diagnostic_pixel_count: stimulus.difficulty_inputs?.diagnostic_pixel_count,
    difficulty_outcome_space_size: stimulus.difficulty_inputs?.outcome_space_size,
    difficulty_match_id: trial.difficulty_match_id ?? stimulus.difficulty_match_id,
    difficulty_match_position: (
      trial.difficulty_match_position ?? stimulus.difficulty_match_position
    ),
    difficulty_match_score_gap: resolveDifficultyMatchScoreGap(trial, stimulus),
    assigned_condition: stimulus.assigned_condition ?? trial.condition,
    displayed_choice_order: currentAttempt.responseChoices.map(item => item.choice_id),
    displayed_choice_targets_json: JSON.stringify(
      currentAttempt.responseChoices.map(item => item.target_ids),
    ),
    target_choice_id: stimulus.target_choice_id,
    decoy_choice_id: stimulus.decoy_choice_id,
    response_choice_id: choice.choice_id,
    response_target_ids: choice.target_ids,
    response_position: responsePosition,
    correct: correct ? 1 : 0,
    decoy_selected: decoySelected ? 1 : 0,
    rt_choice_onset_ms: responseMs - responseOnsetMs,
    rt_stimulus_onset_ms: responseMs - stimulusOnsetMs,
    trial_start_method: startMethod,
    response_input_method: responseInputMethod,
    response_device: session.responseDevice,
    presentation_mode: session.presentation,
    display_id: session.displayId,
    display_width_cm: session.displayWidthCm,
    viewing_distance_cm: session.viewingDistanceCm,
    target_width_visual_angle_deg: session.targetAngleDeg,
    stimulus_duration_planned_ms: manifest.stimulus_duration_ms,
    stimulus_duration_actual_ms: currentAttempt.visualActualDurationMs,
    mask_duration_planned_ms: manifest.mask_duration_ms,
    audio_content: trial.audio_content,
    audio_sweeps_planned: trial.audio_wav ? manifest.sweep_repetitions : 0,
    audio_sweep_duration_ms: trial.audio_wav ? manifest.sweep_duration_ms : 0,
    inter_sweep_interval_ms: trial.audio_wav ? manifest.inter_sweep_interval_ms : 0,
    audio_visual_onset_offset_ms: Number.isFinite(audioPlannedOnsetMs)
      ? stimulusOnsetMs - audioPlannedOnsetMs
      : null,
    invalid_attempts_before_response: invalidAttempts,
    timestamp: new Date().toISOString(),
    ...stageAudit,
  });
  invalidAttempts = 0;
  stopAudio();
  choices.classList.add('hidden');
  if (session.feedbackEnabled) {
    feedback.textContent = correct ? 'Correct' : 'Incorrect';
    feedback.style.color = correct ? 'var(--good)' : 'var(--bad)';
    await wait(650);
  } else {
    await wait(180);
  }
  feedback.textContent = '';
  trialIndex += 1;
  if (trialIndex >= manifest.trials.length) finishBlock();
  else showReady();
}

function scheduleRepeatedAudio(buffer) {
  audioContext ||= new AudioContext();
  const contextStart = audioContext.currentTime + 0.08;
  const stride = manifest.sweep_duration_ms / 1000
    + manifest.inter_sweep_interval_ms / 1000;
  activeAudioSources = [];
  for (let index = 0; index < manifest.sweep_repetitions; index += 1) {
    const source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(audioContext.destination);
    source.start(contextStart + index * stride);
    activeAudioSources.push(source);
  }
  return {
    contextStart,
    performanceOnsetMs: performance.now()
      + (contextStart - audioContext.currentTime) * 1000,
  };
}

function stopAudio() {
  for (const source of activeAudioSources) {
    try { source.stop(); } catch (_error) { /* already stopped */ }
  }
  activeAudioSources = [];
}

function invalidateAttempt(reason) {
  if (!['arming', 'stimulus', 'mask', 'response'].includes(phase)) return;
  if (phaseTimer) window.clearTimeout(phaseTimer);
  phaseTimer = null;
  stopAudio();
  invalidAttempts += 1;
  if (currentAttempt) currentAttempt.invalidReasons.push(reason);
  hide(stimulusImage, mask, choices);
  feedback.textContent = 'Interrupted attempt excluded; the same trial will restart.';
  window.setTimeout(() => {
    feedback.textContent = '';
    showReady();
  }, 550);
  phase = 'invalid';
}

function finishBlock() {
  phase = 'finished';
  stopAudio();
  trialScreen.classList.add('hidden');
  endScreen.classList.remove('hidden');
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  const totalCorrect = trialRows.reduce((total, row) => total + row.correct, 0);
  const conditions = [...new Set(trialRows.map(row => row.condition))];
  summary.innerHTML = `
    <p><strong>Overall accuracy:</strong> ${(100 * totalCorrect / trialRows.length).toFixed(1)}% (${totalCorrect}/${trialRows.length})</p>
    <table class="summary-table"><thead><tr><th>Condition</th><th>Accuracy</th><th>Decoy capture</th><th>Median choice RT</th></tr></thead><tbody>
      ${conditions.map(condition => summarizeCondition(condition)).join('')}
    </tbody></table>`;
}

function summarizeCondition(condition) {
  const rows = trialRows.filter(row => row.condition === condition);
  const correct = rows.reduce((total, row) => total + row.correct, 0);
  const decoys = rows.reduce((total, row) => total + row.decoy_selected, 0);
  const correctRts = rows.filter(row => row.correct).map(row => row.rt_choice_onset_ms);
  return `<tr><td>${humanCondition(condition)}</td><td>${(100 * correct / rows.length).toFixed(1)}% (${correct}/${rows.length})</td><td>${(100 * decoys / rows.length).toFixed(1)}% (${decoys}/${rows.length})</td><td>${correctRts.length ? Math.round(median(correctRts)) + ' ms' : '—'}</td></tr>`;
}

async function saveResults() {
  saveButton.disabled = true;
  const filename = `advanced_ishihara_${safeFilenamePart(session.participantId)}_${session.split}_${Date.now()}.csv`;
  await saveCsv({ rows: trialRows, columns: CSV_COLUMNS, filename, statusElement: saveStatus });
  saveButton.disabled = false;
}

function resetApp() {
  stopAudio();
  manifest = null;
  manifestUrl = null;
  manifestBaseUrl = null;
  stimulusById = new Map();
  audioBuffers = new Map();
  session = null;
  trialRows = [];
  phase = 'setup';
  setFreshRunCode();
  updateSetupPreview();
  preparedPanel.classList.add('hidden');
  prepareStatus.className = 'status';
  prepareStatus.textContent = 'No assets loaded.';
  endScreen.classList.add('hidden');
  trialScreen.classList.add('hidden');
  setupScreen.classList.remove('hidden');
}

function fitStage() {
  const availableWidth = Math.max(200, window.innerWidth - 28);
  const availableHeight = Math.max(120, window.innerHeight - 190);
  const presentation = session?.presentation || document.getElementById('presentation').value;
  let size;
  if (presentation === 'compact') {
    size = compactStageSize({
      availableWidthCssPx: availableWidth,
      availableHeightCssPx: availableHeight,
      nativeWidthPx: NATIVE_WIDTH,
      nativeHeightPx: NATIVE_HEIGHT,
      aspectRatio: ASPECT_RATIO,
    });
  } else if (presentation === 'calibrated' && session) {
    size = calibratedStageSize({
      targetWidthAngleDeg: session.targetAngleDeg,
      viewingDistanceCm: session.viewingDistanceCm,
      displayWidthCm: session.displayWidthCm,
      screenWidthCssPx: window.screen.width,
      availableWidthCssPx: availableWidth,
      availableHeightCssPx: availableHeight,
      aspectRatio: ASPECT_RATIO,
    });
    if (!size.fits) size = fitAspectRatio(availableWidth, availableHeight, ASPECT_RATIO);
  } else {
    size = fitAspectRatio(availableWidth, availableHeight, ASPECT_RATIO);
  }
  stageShell.style.width = `${Math.max(1, size.width)}px`;
  stageShell.style.height = `${Math.max(1, size.height)}px`;
}

function auditStage() {
  const rect = stageShell.getBoundingClientRect();
  let widthCm = null;
  let heightCm = null;
  let widthAngle = null;
  let heightAngle = null;
  if (session.displayWidthCm > 0 && session.viewingDistanceCm > 0) {
    const cmPerCssPixel = session.displayWidthCm / window.screen.width;
    widthCm = rect.width * cmPerCssPixel;
    heightCm = rect.height * cmPerCssPixel;
    widthAngle = visualAngleDeg(widthCm, session.viewingDistanceCm);
    heightAngle = visualAngleDeg(heightCm, session.viewingDistanceCm);
  }
  return {
    viewport_width_css_px: window.innerWidth,
    viewport_height_css_px: window.innerHeight,
    stage_width_css_px: rect.width,
    stage_height_css_px: rect.height,
    stage_aspect_ratio: rect.width / rect.height,
    stage_width_cm: widthCm,
    stage_height_cm: heightCm,
    stage_width_visual_angle_deg: widthAngle,
    stage_height_visual_angle_deg: heightAngle,
    css_px_per_audio_column: rect.width / manifest.audio_spatial_columns,
    css_px_per_audio_row: rect.height / manifest.audio_spatial_rows,
    coordinate_mapping: manifest.coordinate_mapping,
    fullscreen_at_onset: document.fullscreenElement ? 1 : 0,
    device_pixel_ratio: window.devicePixelRatio,
  };
}

function updateCalibrationVisibility() {
  const calibrationFields = document.getElementById('calibration-fields');
  const calibrated = document.getElementById('presentation').value === 'calibrated';
  calibrationFields.classList.toggle('hidden', !calibrated);
  calibrationFields.open = calibrated;
}

function showPrepareError(message) {
  prepareStatus.className = 'status error';
  prepareStatus.textContent = message;
  preparedPanel.classList.add('hidden');
}

function assetUrl(path) {
  return new URL(path, manifestBaseUrl).href;
}

function preloadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = resolve;
    image.onerror = () => reject(new Error(`Image preload failed: ${url}`));
    image.src = url;
  });
}

function hide(...elements) {
  for (const element of elements) element.classList.add('hidden');
}

function nextFrame() {
  return new Promise(resolve => requestAnimationFrame(resolve));
}

function wait(milliseconds) {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds));
}

function numberOrNull(id) {
  const value = Number(document.getElementById(id).value);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function resolveDifficultyMatchScoreGap(trial, stimulus) {
  const explicitGap = trial.difficulty_match_score_gap
    ?? stimulus.difficulty_match_score_gap;
  if (explicitGap !== undefined && explicitGap !== null) return explicitGap;
  const matchId = trial.difficulty_match_id ?? stimulus.difficulty_match_id;
  if (!matchId) return null;
  const partner = manifest.stimuli.find(item => (
    item.difficulty_match_id === matchId
    && item.stimulus_id !== stimulus.stimulus_id
  ));
  if (!partner) return null;
  return Number(Math.abs(
    stimulus.estimated_difficulty_score - partner.estimated_difficulty_score
  ).toFixed(4));
}

function humanCondition(condition) {
  return {
    'visual_silent': 'Visual diagnostic (silent)',
    'visual_background_audio': 'Visual diagnostic + neutral carrier audio',
    'ir_audio': 'Source scaffold + IR diagnostic audio',
  }[condition] || condition;
}

function humanSignalMode(signalMode) {
  return {
    mixed: 'mixed visual vs IR · carrier-controlled',
    visual: 'visual baseline · silent',
    ir: 'IR only · audio diagnostic',
    paired: 'repeated same-puzzle pair · research',
  }[signalMode] || signalMode;
}

const CSV_COLUMNS = [
  'participant_id', 'session_id', 'session_started_at', 'session_seed',
  'schema_version', 'catalog_version', 'task_name', 'source_split',
  'experiment_mode', 'signal_mode', 'comparison_design',
  'stimuli_repeated_across_conditions', 'base_stimulus_count', 'glyph_composition',
  'trial_progression', 'feedback_enabled', 'total_presentation_count',
  'glyph_quota_1', 'glyph_quota_2', 'glyph_quota_3',
  'condition_count_visual_silent', 'condition_count_visual_background_audio',
  'condition_count_ir_audio', 'condition_assignment_method',
  'difficulty_model_version',
  'condition', 'pair_id', 'pair_position', 'pair_order', 'pair_pass', 'pair_lag',
  'trial_index', 'stimulus_id', 'source_ids', 'target_ids', 'mapping_ids',
  'changed_count', 'glyph_count', 'transformation_signature',
  'mapping_repetition_index', 'estimated_difficulty_score',
  'difficulty_rank', 'difficulty_stratum',
  'difficulty_glyph_load', 'difficulty_diagnostic_subtlety',
  'difficulty_alternative_foil_similarity', 'difficulty_family_ambiguity',
  'difficulty_source_pixel_count', 'difficulty_diagnostic_pixel_count',
  'difficulty_outcome_space_size',
  'difficulty_match_id', 'difficulty_match_position',
  'difficulty_match_score_gap', 'assigned_condition',
  'displayed_choice_order', 'displayed_choice_targets_json',
  'target_choice_id', 'decoy_choice_id',
  'response_choice_id', 'response_target_ids', 'response_position', 'correct',
  'decoy_selected', 'rt_choice_onset_ms', 'rt_stimulus_onset_ms',
  'trial_start_method', 'response_input_method', 'response_device',
  'presentation_mode', 'display_id',
  'display_width_cm', 'viewing_distance_cm', 'target_width_visual_angle_deg',
  'stimulus_duration_planned_ms', 'stimulus_duration_actual_ms',
  'mask_duration_planned_ms', 'audio_content', 'audio_sweeps_planned',
  'audio_sweep_duration_ms', 'inter_sweep_interval_ms',
  'audio_visual_onset_offset_ms', 'invalid_attempts_before_response',
  'viewport_width_css_px', 'viewport_height_css_px', 'stage_width_css_px',
  'stage_height_css_px', 'stage_aspect_ratio', 'stage_width_cm', 'stage_height_cm',
  'stage_width_visual_angle_deg', 'stage_height_visual_angle_deg',
  'css_px_per_audio_column', 'css_px_per_audio_row', 'coordinate_mapping',
  'fullscreen_at_onset', 'device_pixel_ratio', 'timestamp',
];

document.documentElement.dataset.advancedIshiharaVersion = 'advanced-2';
