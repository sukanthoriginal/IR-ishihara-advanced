import { buildSchedule, median, mulberry32 } from './task_logic.mjs';

const STIMULUS_ROOT = '../ishihara_stimuli/';

const setupScreen = document.getElementById('setup-screen');
const trialScreen = document.getElementById('trial-screen');
const endScreen = document.getElementById('end-screen');
const startBtn = document.getElementById('start-btn');
const readyBtn = document.getElementById('ready-btn');
const readyPanel = document.getElementById('ready-panel');
const plateEl = document.getElementById('plate');
const maskEl = document.getElementById('mask');
const choicesEl = document.getElementById('choices');
const feedbackEl = document.getElementById('feedback');
const progressEl = document.getElementById('progress');
const trialStatusEl = document.getElementById('trial-status');
const summaryEl = document.getElementById('summary');
const saveStatusEl = document.getElementById('save-status');

let manifest = null;
let audioCtx = null;
let audioCache = new Map();
let session = null;
let schedule = [];
let trialIndex = 0;
let currentTrial = null;
let trialLog = [];
let acceptingChoice = false;

startBtn.addEventListener('click', startSession);
readyBtn.addEventListener('click', beginTrial);
document.getElementById('download-btn').addEventListener('click', saveResults);
document.getElementById('retry-btn').addEventListener('click', retryBlock);
document.getElementById('new-session-btn').addEventListener('click', newSession);
document.addEventListener('keydown', onKeyDown);

function onKeyDown(event) {
  if (event.code === 'Space' && !readyPanel.classList.contains('hidden')) {
    event.preventDefault();
    beginTrial();
    return;
  }
  if (acceptingChoice && ['Digit1', 'Digit2', 'Digit3', 'Digit4'].includes(event.code)) {
    const index = Number(event.code.slice(-1)) - 1;
    const button = choicesEl.querySelectorAll('.choice')[index];
    if (button) button.click();
  }
}

async function startSession() {
  const participantId = document.getElementById('participant-id').value.trim();
  if (!participantId) {
    alert('Enter a participant ID before starting.');
    document.getElementById('participant-id').focus();
    return;
  }

  startBtn.disabled = true;
  startBtn.textContent = 'Loading stimuli…';
  try {
    if (!manifest) {
      const response = await fetch(`${STIMULUS_ROOT}manifest.json`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`manifest request returned ${response.status}`);
      manifest = await response.json();
      validateManifest(manifest);
    }

    const condition = document.getElementById('condition').value;
    if (condition !== 'visible' && !manifest.audio_generated) {
      throw new Error('This stimulus bank was generated with --skip-audio. Select visible-only or regenerate with raspivoice.');
    }

    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    await audioCtx.resume();
    const requestedTrials = Math.max(4, Number.parseInt(document.getElementById('num-trials').value, 10) || 16);
    const numTrials = condition === 'mixed' && requestedTrials % 2
      ? requestedTrials + 1
      : requestedTrials;
    session = {
      participantId,
      arm: document.getElementById('arm').value,
      condition,
      split: document.getElementById('split').value,
      mode: document.getElementById('mode').value,
      numTrials,
      seed: makeSeed(),
      startedAt: new Date().toISOString(),
    };
    beginBlock();
  } catch (error) {
    alert(`Could not start the Ishihara task: ${error.message}`);
    startBtn.disabled = false;
    startBtn.textContent = 'Load block';
  }
}

function validateManifest(value) {
  if (value.task !== 'ir-ishihara-role-substitution' || !Array.isArray(value.stimuli)) {
    throw new Error('manifest has an unsupported schema');
  }
}

function beginBlock() {
  const rng = mulberry32(session.seed);
  schedule = buildSchedule(manifest, session, rng);
  trialIndex = 0;
  trialLog = [];
  currentTrial = null;
  setupScreen.classList.add('hidden');
  endScreen.classList.add('hidden');
  trialScreen.classList.remove('hidden');
  prepareNextTrial();
}

async function prepareNextTrial() {
  acceptingChoice = false;
  choicesEl.classList.add('hidden');
  feedbackEl.textContent = '';
  plateEl.classList.add('hidden');
  maskEl.classList.add('hidden');

  if (trialIndex >= schedule.length) {
    finishBlock();
    return;
  }

  currentTrial = schedule[trialIndex];
  progressEl.textContent = `Trial ${trialIndex + 1} / ${schedule.length}`;
  trialStatusEl.textContent = 'Preparing stimulus…';
  readyPanel.classList.add('hidden');

  try {
    await preloadImage(assetUrl(plateFileFor(currentTrial)));
    const wav = wavFileFor(currentTrial);
    if (wav) await getAudioBuffer(wav);
    readyPanel.classList.remove('hidden');
    readyBtn.disabled = false;
    trialStatusEl.textContent = 'Ready when you are.';
    readyBtn.focus();
  } catch (error) {
    trialStatusEl.textContent = `Stimulus failed to load: ${error.message}`;
  }
}

async function beginTrial() {
  if (!currentTrial || readyPanel.classList.contains('hidden')) return;
  readyBtn.disabled = true;
  readyPanel.classList.add('hidden');
  choicesEl.classList.add('hidden');
  maskEl.classList.add('hidden');
  plateEl.src = assetUrl(plateFileFor(currentTrial));
  plateEl.classList.remove('hidden');
  trialStatusEl.textContent = currentTrial.condition === 'visible'
    ? 'Visible-colour plate'
    : currentTrial.condition === 'ir'
      ? 'IR-colour plate'
      : 'IR spatial-scramble control';

  currentTrial.stimulusStartedMs = performance.now();
  const wav = wavFileFor(currentTrial);
  if (wav) {
    const source = audioCtx.createBufferSource();
    source.buffer = audioCache.get(wav);
    source.connect(audioCtx.destination);
    source.start();
    currentTrial.source = source;
  }

  window.setTimeout(showMask, manifest.soundscape_duration_ms);
}

function showMask() {
  stopCurrentAudio();
  plateEl.classList.add('hidden');
  maskEl.classList.remove('hidden');
  trialStatusEl.textContent = 'Choose the glyph that the target dots formed.';
  window.setTimeout(showChoices, 220);
}

function showChoices() {
  maskEl.classList.add('hidden');
  choicesEl.innerHTML = '';
  for (const [index, glyph] of currentTrial.choices.entries()) {
    const button = document.createElement('button');
    button.className = 'choice';
    button.type = 'button';
    button.dataset.glyph = glyph;
    button.innerHTML = `
      <span class="choice-index">${index + 1}</span>
      <img src="${assetUrl(manifest.glyph_thumbnails[glyph])}" alt="Choice ${index + 1}">
    `;
    button.addEventListener('click', () => recordChoice(glyph, index));
    choicesEl.appendChild(button);
  }
  choicesEl.classList.remove('hidden');
  currentTrial.responseStartedMs = performance.now();
  acceptingChoice = true;
  choicesEl.querySelector('.choice').focus();
}

function recordChoice(choiceGlyph, responsePosition) {
  if (!acceptingChoice) return;
  acceptingChoice = false;
  const rtMs = performance.now() - currentTrial.responseStartedMs;
  const targetGlyph = currentTrial.stimulus.glyph_id;
  const correct = choiceGlyph === targetGlyph;

  trialLog.push({
    participant_id: session.participantId,
    arm: session.arm,
    requested_condition: session.condition,
    condition: currentTrial.condition,
    split: session.split,
    mode: session.mode,
    session_seed: session.seed,
    trial_index: trialIndex,
    stimulus_id: currentTrial.stimulus.stimulus_id,
    stimulus_seed: currentTrial.stimulus.seed,
    target_glyph: targetGlyph,
    choice_glyph: choiceGlyph,
    response_position: responsePosition + 1,
    correct: correct ? 1 : 0,
    rt_ms: Math.round(rtMs),
    stimulus_duration_ms: manifest.soundscape_duration_ms,
    timestamp: new Date().toISOString(),
  });

  choicesEl.querySelectorAll('.choice').forEach(button => { button.disabled = true; });
  if (session.mode === 'train') {
    feedbackEl.className = correct ? 'correct-text' : 'incorrect-text';
    feedbackEl.textContent = correct ? 'Correct' : `Incorrect — target was ${targetGlyph}`;
  } else {
    feedbackEl.className = '';
    feedbackEl.textContent = 'Response recorded.';
  }
  trialIndex += 1;
  window.setTimeout(prepareNextTrial, session.mode === 'train' ? 850 : 400);
}

function plateFileFor(trial) {
  return trial.condition === 'visible'
    ? trial.stimulus.visible_png
    : trial.stimulus.ir_hidden_png;
}

function wavFileFor(trial) {
  if (trial.condition === 'ir') return trial.stimulus.ir_wav;
  if (trial.condition === 'ir-scrambled') return trial.stimulus.ir_scrambled_wav;
  return null;
}

function assetUrl(relativePath) {
  return `${STIMULUS_ROOT}${relativePath}`;
}

async function getAudioBuffer(relativePath) {
  if (audioCache.has(relativePath)) return audioCache.get(relativePath);
  const response = await fetch(assetUrl(relativePath));
  if (!response.ok) throw new Error(`${relativePath} returned ${response.status}`);
  const buffer = await audioCtx.decodeAudioData(await response.arrayBuffer());
  audioCache.set(relativePath, buffer);
  return buffer;
}

function preloadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = resolve;
    image.onerror = () => reject(new Error(`could not load ${url}`));
    image.src = url;
  });
}

function stopCurrentAudio() {
  if (currentTrial && currentTrial.source) {
    try { currentTrial.source.stop(); } catch (_) { /* already ended */ }
    currentTrial.source = null;
  }
}

function finishBlock() {
  stopCurrentAudio();
  trialScreen.classList.add('hidden');
  endScreen.classList.remove('hidden');
  const rows = ['visible', 'ir', 'ir-scrambled']
    .map(condition => summarize(condition))
    .filter(Boolean);
  const totalCorrect = trialLog.reduce((sum, row) => sum + row.correct, 0);
  summaryEl.innerHTML = `
    <p><strong>Overall accuracy:</strong> ${(100 * totalCorrect / trialLog.length).toFixed(1)}%
      (${totalCorrect}/${trialLog.length})</p>
    <table>
      <thead><tr><th>Condition</th><th>Accuracy</th><th>Median correct RT</th></tr></thead>
      <tbody>${rows.map(row => `<tr><td>${row.condition}</td><td>${row.accuracy}</td><td>${row.rt}</td></tr>`).join('')}</tbody>
    </table>
  `;
}

function summarize(condition) {
  const rows = trialLog.filter(row => row.condition === condition);
  if (!rows.length) return null;
  const nCorrect = rows.reduce((sum, row) => sum + row.correct, 0);
  const correctRts = rows.filter(row => row.correct).map(row => row.rt_ms);
  return {
    condition,
    accuracy: `${(100 * nCorrect / rows.length).toFixed(1)}% (${nCorrect}/${rows.length})`,
    rt: correctRts.length ? `${Math.round(median(correctRts))} ms` : '—',
  };
}

function retryBlock() {
  session.seed = makeSeed();
  session.startedAt = new Date().toISOString();
  beginBlock();
}

function newSession() {
  stopCurrentAudio();
  trialLog = [];
  schedule = [];
  currentTrial = null;
  endScreen.classList.add('hidden');
  trialScreen.classList.add('hidden');
  setupScreen.classList.remove('hidden');
  startBtn.disabled = false;
  startBtn.textContent = 'Load block';
}

async function saveResults() {
  const csv = buildCsv(trialLog);
  const filename = `ishihara_${safePart(session.participantId)}_${session.condition}_${session.split}_${Date.now()}.csv`;
  const button = document.getElementById('download-btn');
  button.disabled = true;
  saveStatusEl.className = '';
  saveStatusEl.textContent = 'Saving…';
  try {
    const response = await fetch('/api/save-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, csv }),
    });
    if (!response.ok) throw new Error(`server returned ${response.status}`);
    const info = await response.json();
    saveStatusEl.textContent = `Saved to ${info.path}`;
  } catch (error) {
    triggerDownload(csv, filename);
    saveStatusEl.className = 'error';
    saveStatusEl.textContent = 'Local auto-save failed; the CSV was downloaded instead.';
  } finally {
    button.disabled = false;
  }
}

function buildCsv(rows) {
  const columns = [
    'participant_id', 'arm', 'requested_condition', 'condition', 'split', 'mode',
    'session_seed', 'trial_index', 'stimulus_id', 'stimulus_seed', 'target_glyph',
    'choice_glyph', 'response_position', 'correct', 'rt_ms', 'stimulus_duration_ms', 'timestamp',
  ];
  const lines = [columns.join(',')];
  for (const row of rows) lines.push(columns.map(column => csvCell(row[column])).join(','));
  return lines.join('\n');
}

function csvCell(value) {
  const text = String(value ?? '');
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function triggerDownload(text, filename) {
  const blob = new Blob([text], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function safePart(value) {
  return value.replace(/[^A-Za-z0-9_.-]+/g, '_') || 'participant';
}

function makeSeed() {
  const values = new Uint32Array(1);
  crypto.getRandomValues(values);
  return values[0];
}
