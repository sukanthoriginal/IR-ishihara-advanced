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

const setupScreen = document.getElementById('setup-screen');
const trialScreen = document.getElementById('trial-screen');
const endScreen = document.getElementById('end-screen');
const prepareButton = document.getElementById('prepare-btn');
const prepareStatus = document.getElementById('prepare-status');
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
for (const id of ['split', 'mode', 'trial-count', 'seed']) {
  document.getElementById(id).addEventListener('change', invalidatePreparedBlock);
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
  if (phase === 'response' && /^[1-4]$/.test(event.key)) {
    const button = choices.querySelector(`[data-position="${event.key}"]`);
    if (button) {
      event.preventDefault();
      button.click();
    }
  }
});

updateCalibrationVisibility();
fitStage();

async function prepareBlock() {
  const participantId = document.getElementById('participant-id').value.trim();
  const split = document.getElementById('split').value;
  const mode = document.getElementById('mode').value;
  const trialCount = Number(document.getElementById('trial-count').value);
  const seed = Number(document.getElementById('seed').value);
  if (!participantId) return showPrepareError('Participant ID is required.');
  if (!Number.isInteger(trialCount) || trialCount < 4 || trialCount > 96) {
    return showPrepareError('Trials must be an integer from 4 to 96.');
  }
  if (mode === 'mixed' && trialCount % 2) {
    return showPrepareError('Paired visible-versus-IR blocks require an even trial count.');
  }

  prepareButton.disabled = true;
  preparedPanel.classList.add('hidden');
  prepareStatus.className = 'status';
  prepareStatus.textContent = mode === 'mixed'
    ? 'Generating the frozen schedule and its complete audio cache…'
    : 'Generating the frozen visual schedule…';
  try {
    const response = await fetch('/api/prepare-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ split, mode, trialCount, seed }),
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
    preparedSummary.textContent = `${manifest.trials.length} trials · ${split} sources · ${mode}`;
    preparedPanel.classList.remove('hidden');
  } catch (error) {
    showPrepareError(String(error.message || error));
  } finally {
    prepareButton.disabled = false;
  }
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
    mode: manifest.settings.mode,
    responseDevice: document.getElementById('response-device').value,
    presentation: document.getElementById('presentation').value,
    displayId: document.getElementById('display-id').value.trim(),
    displayWidthCm: numberOrNull('display-width-cm'),
    viewingDistanceCm: numberOrNull('viewing-distance-cm'),
    targetAngleDeg: numberOrNull('target-angle-deg'),
    seed: manifest.settings.seed,
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
  };
  renderChoices(stimulus);
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

function renderChoices(stimulus) {
  choices.replaceChildren();
  stimulus.response_choices.forEach((choice, index) => {
    const button = document.createElement('button');
    button.className = 'choice';
    button.dataset.position = String(index + 1);
    button.innerHTML = `<span class="number">${index + 1}</span><img alt="Interpretation ${index + 1}" src="${assetUrl(choice.png)}">`;
    button.addEventListener('click', () => recordChoice(choice, index + 1));
    choices.append(button);
  });
}

async function recordChoice(choice, responsePosition) {
  if (phase !== 'response') return;
  phase = 'feedback';
  const responseMs = performance.now();
  const { trial, stimulus, startMethod } = currentAttempt;
  const correct = choice.choice_id === stimulus.target_choice_id;
  const decoySelected = choice.choice_id === stimulus.decoy_choice_id;
  const stageAudit = auditStage();
  trialRows.push({
    participant_id: session.participantId,
    session_id: manifest.session_id,
    session_seed: manifest.settings.seed,
    source_split: manifest.settings.split,
    experiment_mode: manifest.settings.mode,
    condition: trial.condition,
    pair_id: trial.pair_id,
    pair_position: trial.pair_position,
    trial_index: trial.trial_index,
    stimulus_id: stimulus.stimulus_id,
    source_ids: stimulus.source_ids,
    target_ids: stimulus.target_ids,
    mapping_ids: stimulus.mapping_ids,
    changed_count: stimulus.changed_count,
    response_choice_id: choice.choice_id,
    response_target_ids: choice.target_ids,
    response_position: responsePosition,
    correct: correct ? 1 : 0,
    decoy_selected: decoySelected ? 1 : 0,
    rt_choice_onset_ms: responseMs - responseOnsetMs,
    rt_stimulus_onset_ms: responseMs - stimulusOnsetMs,
    trial_start_method: startMethod,
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
  if (session.split === 'train') {
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
  manifest = null;
  session = null;
  trialRows = [];
  phase = 'setup';
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
  document.getElementById('calibration-fields').open = (
    document.getElementById('presentation').value === 'calibrated'
  );
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

function humanCondition(condition) {
  return {
    'visual-only': 'Visual-only baseline',
    'visible-composite': 'Visible probe + background carrier',
    'ir-composite': 'IR probe',
  }[condition] || condition;
}

const CSV_COLUMNS = [
  'participant_id', 'session_id', 'session_seed', 'source_split',
  'experiment_mode', 'condition', 'pair_id', 'pair_position', 'trial_index',
  'stimulus_id', 'source_ids', 'target_ids', 'mapping_ids', 'changed_count',
  'response_choice_id', 'response_target_ids', 'response_position', 'correct',
  'decoy_selected', 'rt_choice_onset_ms', 'rt_stimulus_onset_ms',
  'trial_start_method', 'response_device', 'presentation_mode', 'display_id',
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

document.documentElement.dataset.advancedIshiharaVersion = 'advanced-1';
