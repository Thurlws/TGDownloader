// ═══════════════════════════════════════
//  AUDIO FX
//  WebAudio EQ + visualizer + ReplayGain application, crossfade engine,
//  per-track resume for long files, waveform seekbar, karaoke mode,
//  synced-lyrics .lrc export.
//
//  Integration contract with 05-player.js (all called via window.* guards):
//    _fxTick(cur, dur)            : every timeupdate of the active element
//    _fxOnTrackChange(t, alb, ph) : end of _mpPlayTrack
//    _fxFinishXfade()             : from the 'ended' handler; true = consumed
//    _mpCancelXfade()             : manual track change / sleep stop
// ═══════════════════════════════════════

// ── Settings ─────────────────────────────────────────────────────────────────
const _FX_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const _FX_PRESETS = {
  Flat:     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  Bass:     [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
  Vocal:    [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
  Treble:   [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
  Loudness: [5, 4, 2, 0, -1, -1, 0, 2, 4, 5],
};

let _fxS = { eq: false, gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], fade: 0, rg: true, vis: false };
try { Object.assign(_fxS, JSON.parse(localStorage.getItem('tgdl_fx') || '{}')); } catch (_) {}
if (!Array.isArray(_fxS.gains) || _fxS.gains.length !== 10) _fxS.gains = [0,0,0,0,0,0,0,0,0,0];

function _fxSave() { try { localStorage.setItem('tgdl_fx', JSON.stringify(_fxS)); } catch (_) {} }

// ── WebAudio graph (lazy; playback is untouched if creation fails) ───────────
const _fxMainEl = document.getElementById('mini-audio');
let _fxCtx = null, _fxBands = null, _fxRgGain = null, _fxAnalyser = null;
let _fxDead = false;                       // graph creation failed, never retry
const _fxRouted = new WeakSet();

function _fxRoute(el) {
  if (!_fxCtx || _fxRouted.has(el)) return;
  try {
    const src = _fxCtx.createMediaElementSource(el);
    src.connect(_fxBands[0]);
    _fxRouted.add(el);
  } catch (_) {}
}

function _fxEnsureGraph() {
  if (_fxCtx || _fxDead) return !!_fxCtx;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    _fxCtx = new AC();
    _fxBands = _FX_FREQS.map((f, i) => {
      const b = _fxCtx.createBiquadFilter();
      b.type = i === 0 ? 'lowshelf' : i === _FX_FREQS.length - 1 ? 'highshelf' : 'peaking';
      b.frequency.value = f;
      if (b.type === 'peaking') b.Q.value = 1.1;
      b.gain.value = 0;
      return b;
    });
    for (let i = 0; i < _fxBands.length - 1; i++) _fxBands[i].connect(_fxBands[i + 1]);
    _fxRgGain   = _fxCtx.createGain();
    _fxAnalyser = _fxCtx.createAnalyser();
    _fxAnalyser.fftSize = 128;
    _fxBands[_fxBands.length - 1].connect(_fxRgGain);
    _fxRgGain.connect(_fxAnalyser);
    _fxAnalyser.connect(_fxCtx.destination);
    _fxRoute(_fxMainEl);
    if (_mpAudioB) _fxRoute(_mpAudioB);
    _fxApplyEq();
  } catch (_) {
    _fxDead = true;
    _fxCtx = null;
  }
  return !!_fxCtx;
}

function _fxApplyEq() {
  if (!_fxBands) return;
  _fxBands.forEach((b, i) => { b.gain.value = _fxS.eq ? (_fxS.gains[i] || 0) : 0; });
}

// ── ReplayGain application ────────────────────────────────────────────────────
let _fxCurGain = null;      // dB for the current track, or null

function _fxApplyRg() {
  if (!_fxRgGain) return;
  const lin = (_fxS.rg && _fxCurGain != null)
    ? Math.min(3.16, Math.max(0.25, Math.pow(10, _fxCurGain / 20)))
    : 1;
  _fxRgGain.gain.value = lin;
}

// ── Crossfade engine ─────────────────────────────────────────────────────────
let _fxXfade = null;          // {idx, fade, from, to}
let _fxXfadeTriedFor = -1;    // don't re-attempt a failed fade every timeupdate

function _fxEnsureB() {
  if (!_mpAudioB) {
    _mpAudioB = new Audio();
    _mpAudioB.preload = 'auto';
    _mpBindAudioEvents(_mpAudioB);
    if (_fxCtx) _fxRoute(_mpAudioB);
  }
  return (_mpAudio === _mpAudioB) ? _fxMainEl : _mpAudioB;
}

function _fxUserVol() {
  return parseFloat(document.getElementById('mp-vol-range')?.value || '1');
}

function _fxStartXfade(nextIdx, fade) {
  const t = _mpQueue[nextIdx];
  if (!t) return;
  const ph  = t.path_hash || _mpCurrentAlb?.path_hash;
  const url = (ph && t.name)
    ? `/audio-file?path_hash=${encodeURIComponent(ph)}&name=${encodeURIComponent(t.name)}`
    : (t.preview_url || '');
  if (!url) return;
  const incoming = _fxEnsureB();
  try {
    incoming.src = url;
    incoming.volume = 0;
    const p = incoming.play();
    if (p && p.catch) p.catch(() => { _fxXfade = null; });
    _fxXfade = { idx: nextIdx, fade, from: _mpAudio, to: incoming };
  } catch (_) { _fxXfade = null; }
}

function _fxXfadeStep() {
  const x = _fxXfade;
  if (!x) return;
  if (!_mpPlaying) { _mpCancelXfade(); return; }
  const dur = x.from.duration || 0, cur = x.from.currentTime || 0;
  const v   = _fxUserVol();
  const k   = dur > 0 ? Math.min(1, Math.max(0, (cur - (dur - x.fade)) / x.fade)) : 1;
  try { x.from.volume = v * (1 - k); x.to.volume = v * k; } catch (_) {}
}

function _fxFinishXfade() {
  const x = _fxXfade;
  if (!x) return false;
  _fxXfade = null;
  _fxXfadeTriedFor = -1;
  try { x.to.volume = _fxUserVol(); } catch (_) {}
  try { x.from.volume = _fxUserVol(); } catch (_) {}
  window._mpXfadeAdopt = { el: x.to, idx: x.idx };
  _mpPlayTrack(x.idx, _mpCurrentAlb, true);
  return true;
}

function _mpCancelXfade() {
  const x = _fxXfade;
  if (!x) return;
  _fxXfade = null;
  _fxXfadeTriedFor = -1;
  try { x.to.pause(); x.to.removeAttribute('src'); x.to.load(); } catch (_) {}
  try { x.from.volume = _fxUserVol(); } catch (_) {}
}

// ── Per-track resume for long files (≥20 min: DJ mixes, audiobooks) ──────────
const _FX_RESUME_MIN_DUR = 20 * 60;
let _fxResume = {};
try { _fxResume = JSON.parse(localStorage.getItem('tgdl_resume') || '{}') || {}; } catch (_) {}
let _fxResumeLastSave = 0;

function _fxResumeKey() {
  const c = window._fxCur;
  return c && c.ph && c.name ? c.ph + '|' + c.name : null;
}

function _fxResumeTick(cur, dur) {
  if (!dur || dur < _FX_RESUME_MIN_DUR) return;
  const key = _fxResumeKey();
  if (!key) return;
  const now = Date.now();
  if (now - _fxResumeLastSave < 5000) return;
  _fxResumeLastSave = now;
  if (cur > 60 && cur < dur - 90) {
    _fxResume[key] = Math.floor(cur);
  } else if (cur >= dur - 90) {
    delete _fxResume[key];          // effectively finished, start fresh next time
  }
  try { localStorage.setItem('tgdl_resume', JSON.stringify(_fxResume)); } catch (_) {}
}

// ── Waveform seekbar (Now Playing panel) ─────────────────────────────────────
let _fxPeaks = null, _fxWaveLastX = -1;

function _fxCssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function _fxDrawWave(cur, dur) {
  const cv = document.getElementById('np-wave');
  if (!cv || !_fxPeaks || !_fxPeaks.length) return;
  const frac = dur > 0 ? Math.min(1, cur / dur) : 0;
  const playedX = Math.round(frac * cv.width);
  if (playedX === _fxWaveLastX) return;
  _fxWaveLastX = playedX;
  const ctx = cv.getContext('2d');
  const W = cv.width, H = cv.height, n = _fxPeaks.length;
  const bw = W / n;
  const accent = _fxCssVar('--accent', '#1db954');
  const dim    = _fxCssVar('--border2', '#444');
  ctx.clearRect(0, 0, W, H);
  for (let i = 0; i < n; i++) {
    const h = Math.max(2, _fxPeaks[i] * (H - 4));
    const x = i * bw;
    ctx.fillStyle = (x + bw / 2) <= playedX ? accent : dim;
    ctx.fillRect(x + bw * 0.15, (H - h) / 2, bw * 0.7, h);
  }
}

async function _fxLoadWave(ph, name) {
  const cv = document.getElementById('np-wave');
  if (!cv) return;
  _fxPeaks = null; _fxWaveLastX = -1;
  cv.style.display = 'none';
  if (!ph || !name) return;
  try {
    const r = await fetch(`/waveform?path_hash=${encodeURIComponent(ph)}&name=${encodeURIComponent(name)}`);
    if (!r.ok) return;
    const d = await r.json();
    const c = window._fxCur;
    if (!c || c.ph !== ph || c.name !== name) return;     // stale response
    if (d.peaks && d.peaks.length) {
      _fxPeaks = d.peaks;
      cv.style.display = '';
      _fxDrawWave(_mpAudio.currentTime || 0, _mpAudio.duration || 0);
    }
  } catch (_) {}
}

document.getElementById('np-wave')?.addEventListener('click', e => {
  const cv = e.currentTarget;
  const dur = _mpAudio.duration;
  if (!isFinite(dur) || dur <= 0) return;
  const rect = cv.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  try { _mpAudio.currentTime = frac * dur; } catch (_) {}
});

// ── Visualizer (Now Playing panel) ───────────────────────────────────────────
let _fxVisRaf = 0;

function _fxVisLoop() {
  _fxVisRaf = 0;
  const cv = document.getElementById('np-vis');
  if (!cv || !_fxS.vis || !_fxAnalyser) { if (cv) cv.style.display = 'none'; return; }
  cv.style.display = '';
  const ctx = cv.getContext('2d');
  const data = new Uint8Array(_fxAnalyser.frequencyBinCount);
  _fxAnalyser.getByteFrequencyData(data);
  const W = cv.width, H = cv.height, n = 48, step = Math.floor(data.length / n) || 1;
  const accent = _fxCssVar('--accent', '#1db954');
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = accent;
  for (let i = 0; i < n; i++) {
    const v = data[i * step] / 255;
    const h = Math.max(1, v * (H - 2));
    const bw = W / n;
    ctx.globalAlpha = 0.35 + v * 0.65;
    ctx.fillRect(i * bw + bw * 0.2, H - h, bw * 0.6, h);
  }
  ctx.globalAlpha = 1;
  if (_mpPlaying && _fxS.vis) _fxVisRaf = requestAnimationFrame(_fxVisLoop);
}

function _fxVisKick() {
  if (_fxS.vis && !_fxVisRaf && _fxEnsureGraph()) _fxVisRaf = requestAnimationFrame(_fxVisLoop);
  if (!_fxS.vis) { const cv = document.getElementById('np-vis'); if (cv) cv.style.display = 'none'; }
}

// ── Karaoke mode ──────────────────────────────────────────────────────────────
let _fxKaraokeOn = false, _fxKaraokeIdx = -2;

function _fxKaraokeEl() {
  let el = document.getElementById('fx-karaoke');
  if (!el) {
    el = document.createElement('div');
    el.id = 'fx-karaoke';
    el.innerHTML = '<div id="fx-k-prev"></div><div id="fx-k-cur"></div><div id="fx-k-next"></div>' +
                   '<div id="fx-k-hint">Esc or click to exit</div>';
    el.addEventListener('click', () => _fxKaraokeToggle(false));
    document.body.appendChild(el);
  }
  return el;
}

function _fxKaraokeToggle(force) {
  const want = typeof force === 'boolean' ? force : !_fxKaraokeOn;
  if (want && (!_npLyricsData || !_npLyricsData.length)) {
    if (window._toast) _toast('No synced lyrics for this track.', 'info');
    return;
  }
  _fxKaraokeOn = want;
  _fxKaraokeEl().classList.toggle('open', want);
  document.getElementById('np-karaoke')?.classList.toggle('active', want);
  _fxKaraokeIdx = -2;
  if (want) _fxKaraokeTick(true);
}

function _fxKaraokeTick(force) {
  if (!_fxKaraokeOn) return;
  const data = _npLyricsData;
  if (!data || !data.length) { _fxKaraokeToggle(false); return; }
  const t = _mpAudio.currentTime || 0;
  let idx = -1;
  for (let i = 0; i < data.length; i++) { if (data[i].t <= t + 0.15) idx = i; else break; }
  if (idx === _fxKaraokeIdx && !force) return;
  _fxKaraokeIdx = idx;
  const g = id => document.getElementById(id);
  if (g('fx-k-prev')) g('fx-k-prev').textContent = idx > 0 ? (data[idx - 1].text || '♪') : '';
  if (g('fx-k-cur'))  g('fx-k-cur').textContent  = idx >= 0 ? (data[idx].text || '♪') : '…';
  if (g('fx-k-next')) g('fx-k-next').textContent = idx + 1 < data.length ? (data[idx + 1].text || '♪') : '';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && _fxKaraokeOn) _fxKaraokeToggle(false);
});
document.getElementById('np-karaoke')?.addEventListener('click', () => _fxKaraokeToggle());

// ── Save synced lyrics as a .lrc sidecar ─────────────────────────────────────
document.getElementById('np-save-lrc')?.addEventListener('click', async () => {
  const c = window._fxCur;
  if (!c || !c.ph || !c.name || !window._npLyricsRaw) return;
  try {
    const r = await fetch('/save-lrc', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path_hash: c.ph, name: c.name, lrc: window._npLyricsRaw }),
    });
    const d = await r.json();
    if (window._toast) _toast(d.ok ? `Saved ${d.file}` : (d.error || 'Could not save .lrc'), d.ok ? 'info' : 'error');
  } catch (_) {
    if (window._toast) _toast('Could not save .lrc', 'error');
  }
});

// ── EQ / audio settings panel ─────────────────────────────────────────────────
function _fxBuildPanel() {
  let panel = document.getElementById('fx-panel');
  if (panel) return panel;
  panel = document.createElement('div');
  panel.id = 'fx-panel';
  const bands = _FX_FREQS.map((f, i) => {
    const g = _fxS.gains[i] || 0;
    return `
    <div class="fx-band-col">
      <span class="fx-db" id="fx-db-${i}">${g > 0 ? '+' : ''}${g}</span>
      <div class="fx-band-track">
        <input type="range" class="fx-band" data-i="${i}" min="-12" max="12" step="1" value="${g}"
               aria-label="${f >= 1000 ? (f / 1000) + 'k' : f} Hz" orient="vertical">
      </div>
      <span class="fx-freq">${f >= 1000 ? (f / 1000) + 'k' : f}</span>
    </div>`;
  }).join('');
  panel.innerHTML = `
    <div class="fx-head">
      <span class="fx-title">Equalizer</span>
      <label class="fx-switch" title="Toggle equalizer">
        <input type="checkbox" id="fx-eq-on" ${_fxS.eq ? 'checked' : ''}>
        <span class="fx-switch-track"></span>
      </label>
    </div>
    <div class="fx-preset-row">
      ${Object.keys(_FX_PRESETS).map(p => `<button class="fx-preset" data-p="${p}">${p}</button>`).join('')}
    </div>
    <div class="fx-eq ${_fxS.eq ? '' : 'fx-eq-off'}" id="fx-eq-area">
      <div class="fx-eq-bands">${bands}</div>
    </div>
    <div class="fx-sep"></div>
    <div class="fx-ctrl-row">
      <span class="fx-ctrl-label">Crossfade</span>
      <input type="range" id="fx-fade" min="0" max="12" step="1" value="${_fxS.fade || 0}">
      <span class="fx-ctrl-val" id="fx-fade-lbl">${_fxS.fade ? _fxS.fade + 's' : 'off'}</span>
    </div>
    <label class="fx-check"><input type="checkbox" id="fx-rg" ${_fxS.rg ? 'checked' : ''}><span>ReplayGain volume levelling</span></label>
    <label class="fx-check"><input type="checkbox" id="fx-vis" ${_fxS.vis ? 'checked' : ''}><span>Visualizer</span></label>`;
  document.body.appendChild(panel);

  panel.querySelectorAll('.fx-band').forEach(sl => sl.addEventListener('input', () => {
    const i = +sl.dataset.i;
    _fxS.gains[i] = +sl.value;
    const lbl = document.getElementById('fx-db-' + i);
    if (lbl) lbl.textContent = (sl.value > 0 ? '+' : '') + sl.value;
    _fxHighlightPreset();
    if (_fxS.eq) { _fxEnsureGraph(); _fxApplyEq(); }
    _fxSave();
  }));
  panel.querySelectorAll('.fx-preset').forEach(btn => btn.addEventListener('click', () => {
    _fxS.gains = [..._FX_PRESETS[btn.dataset.p]];
    panel.querySelectorAll('.fx-band').forEach(sl => {
      const i = +sl.dataset.i;
      sl.value = _fxS.gains[i];
      const lbl = document.getElementById('fx-db-' + i);
      if (lbl) lbl.textContent = (_fxS.gains[i] > 0 ? '+' : '') + _fxS.gains[i];
    });
    if (!_fxS.eq) { _fxS.eq = true; const cb = document.getElementById('fx-eq-on'); if (cb) cb.checked = true; }
    _fxEqAreaState();
    _fxHighlightPreset();
    _fxEnsureGraph(); _fxApplyEq(); _fxSave(); _fxSyncEqBtn();
  }));
  panel.querySelector('#fx-eq-on').addEventListener('change', e => {
    _fxS.eq = e.target.checked;
    _fxEqAreaState();
    if (_fxS.eq) _fxEnsureGraph();
    _fxApplyEq(); _fxSave(); _fxSyncEqBtn();
  });
  _fxHighlightPreset();
  panel.querySelector('#fx-fade').addEventListener('input', e => {
    _fxS.fade = +e.target.value;
    const lbl = document.getElementById('fx-fade-lbl');
    if (lbl) lbl.textContent = _fxS.fade ? _fxS.fade + 's' : 'off';
    _fxSave(); _fxSyncEqBtn();
  });
  panel.querySelector('#fx-rg').addEventListener('change', e => {
    _fxS.rg = e.target.checked;
    if (_fxS.rg) _fxEnsureGraph();
    _fxApplyRg(); _fxSave();
  });
  panel.querySelector('#fx-vis').addEventListener('change', e => {
    _fxS.vis = e.target.checked;
    _fxSave(); _fxVisKick();
  });
  return panel;
}

function _fxSyncEqBtn() {
  const b = document.getElementById('mp-eq');
  if (b) b.classList.toggle('active', !!(_fxS.eq || _fxS.fade));
}

// Highlight the preset pill whose curve matches the current band gains (if any).
function _fxHighlightPreset() {
  const match = Object.keys(_FX_PRESETS).find(p =>
    _FX_PRESETS[p].every((v, i) => v === (_fxS.gains[i] || 0)));
  document.querySelectorAll('#fx-panel .fx-preset').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.p === match));
}

// Dim the band sliders when the EQ is switched off (still adjustable).
function _fxEqAreaState() {
  const area = document.getElementById('fx-eq-area');
  if (area) area.classList.toggle('fx-eq-off', !_fxS.eq);
}

document.getElementById('mp-eq')?.addEventListener('click', e => {
  e.stopPropagation();
  const panel = _fxBuildPanel();
  panel.classList.toggle('open');
});
document.addEventListener('click', e => {
  const panel = document.getElementById('fx-panel');
  if (panel && panel.classList.contains('open')
      && !panel.contains(e.target) && e.target.id !== 'mp-eq') {
    panel.classList.remove('open');
  }
});

// ── Hooks called from 05-player.js ───────────────────────────────────────────
function _fxOnTrackChange(track, alb, ph) {
  window._fxCur = (ph && track?.name) ? { ph, name: track.name } : null;
  _fxCurGain = null;
  _fxApplyRg();
  _fxKaraokeIdx = -2;

  // Lyrics-dependent buttons reset; _fxTick re-shows them when data arrives.
  const kBtn = document.getElementById('np-karaoke');
  const sBtn = document.getElementById('np-save-lrc');
  if (kBtn) kBtn.style.display = 'none';
  if (sBtn) sBtn.style.display = 'none';

  if (!window._fxCur) { _fxLoadWave(null, null); return; }

  // Per-track resume (only when nothing else queued a seek, e.g. app restore)
  const rKey = ph + '|' + track.name;
  if (_fxResume[rKey] > 60 && _mpPendingSeek == null) _mpPendingSeek = _fxResume[rKey];

  _fxLoadWave(ph, track.name);

  fetch(`/track-gain?path_hash=${encodeURIComponent(ph)}&name=${encodeURIComponent(track.name)}`)
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      const c = window._fxCur;
      if (!d || !c || c.ph !== ph || c.name !== track.name) return;
      _fxCurGain = (typeof d.gain === 'number') ? d.gain : null;
      if (_fxS.rg && _fxCurGain != null && _fxEnsureGraph()) _fxApplyRg();
    })
    .catch(() => {});

  _fxVisKick();
}

function _fxTick(cur, dur) {
  if (_fxCtx && _fxCtx.state === 'suspended') { try { _fxCtx.resume(); } catch (_) {} }

  // Crossfade: start inside the fade window, then step the volume ramp.
  const fade = _fxS.fade || 0;
  if (_fxXfade) {
    _fxXfadeStep();
  } else if (fade > 0 && _mpPlaying && dur > fade + 8 && (dur - cur) <= fade
             && _mpRepeat !== 'one' && _fxXfadeTriedFor !== _mpIdx) {
    _fxXfadeTriedFor = _mpIdx;
    const next = _mpPickNext(true);
    if (next >= 0) _fxStartXfade(next, fade);
  }

  _fxResumeTick(cur, dur);
  _fxDrawWave(cur, dur);
  _fxKaraokeTick();
  if (_fxS.vis && !_fxVisRaf) _fxVisKick();

  // Show/hide lyric-dependent header buttons based on loaded lyric data.
  const hasSynced = !!(_npLyricsData && _npLyricsData.length);
  const kBtn = document.getElementById('np-karaoke');
  const sBtn = document.getElementById('np-save-lrc');
  if (kBtn) kBtn.style.display = hasSynced ? '' : 'none';
  if (sBtn) sBtn.style.display = (hasSynced && window._npLyricsRaw && window._fxCur) ? '' : 'none';
}

// ── Styles + initial state ────────────────────────────────────────────────────
(function _fxInit() {
  const st = document.createElement('style');
  st.textContent = `
#fx-panel { position: fixed; right: 12px; bottom: 74px; z-index: 900; width: 300px;
  background: var(--bg2, #222); border: 1px solid var(--border2, #444); border-radius: 10px;
  padding: 14px; display: none; box-shadow: 0 12px 40px rgba(0,0,0,.5);
  font-family: var(--sans, sans-serif); }
#fx-panel.open { display: block; animation: fxPanelIn .16s ease both; }
@keyframes fxPanelIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
#fx-panel .fx-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
#fx-panel .fx-title { font-size: 11px; font-weight: 600; letter-spacing: .1em;
  text-transform: uppercase; color: var(--fg2, #bbb); }
/* Toggle switch */
#fx-panel .fx-switch { position: relative; display: inline-block; width: 34px; height: 18px; cursor: pointer; }
#fx-panel .fx-switch input { position: absolute; opacity: 0; width: 0; height: 0; }
#fx-panel .fx-switch-track { position: absolute; inset: 0; border-radius: 10px;
  background: var(--bg4, #333); border: 1px solid var(--border2, #444); transition: background .15s, border-color .15s; }
#fx-panel .fx-switch-track::after { content: ''; position: absolute; top: 2px; left: 2px;
  width: 12px; height: 12px; border-radius: 50%; background: var(--fg3, #999); transition: transform .15s, background .15s; }
#fx-panel .fx-switch input:checked + .fx-switch-track { background: var(--accent, #c4fa55); border-color: var(--accent, #c4fa55); }
#fx-panel .fx-switch input:checked + .fx-switch-track::after { transform: translateX(16px); background: #111; }
/* Preset pills */
#fx-panel .fx-preset-row { display: flex; gap: 5px; margin: 0 0 12px; flex-wrap: wrap; }
#fx-panel .fx-preset { font-size: 10px; padding: 3px 10px; border-radius: 20px;
  border: 1px solid var(--border2, #444); background: var(--bg3, #2a2a2a);
  color: var(--fg2, #bbb); cursor: pointer; transition: all .12s; }
#fx-panel .fx-preset:hover { border-color: var(--accent, #c4fa55); color: var(--fg, #fff); }
#fx-panel .fx-preset.active { background: var(--accent, #c4fa55); border-color: var(--accent, #c4fa55);
  color: #111; font-weight: 600; }
/* Equalizer bands */
#fx-panel .fx-eq { transition: opacity .15s; }
#fx-panel .fx-eq.fx-eq-off { opacity: .4; }
#fx-panel .fx-eq-bands { display: flex; justify-content: space-between; gap: 2px; }
#fx-panel .fx-band-col { display: flex; flex-direction: column; align-items: center; gap: 5px; flex: 1; }
#fx-panel .fx-db { font-size: 9px; color: var(--fg2, #ddd); font-family: var(--mono, monospace);
  min-height: 12px; }
#fx-panel .fx-freq { font-size: 9px; color: var(--fg3, #999); font-family: var(--mono, monospace); }
#fx-panel .fx-band-track { position: relative; height: 104px; display: flex; justify-content: center; }
/* 0 dB reference tick behind each slider */
#fx-panel .fx-band-track::after { content: ''; position: absolute; top: 50%; left: 3px; right: 3px;
  height: 1px; background: var(--border2, #444); pointer-events: none; }
#fx-panel .fx-band { writing-mode: vertical-lr; direction: rtl; -webkit-appearance: slider-vertical;
  appearance: slider-vertical; width: 20px; height: 104px; margin: 0; cursor: pointer;
  background: transparent; position: relative; z-index: 1; }
#fx-panel .fx-band::-webkit-slider-runnable-track { width: 4px; border-radius: 2px;
  background: var(--bg4, #333); }
#fx-panel .fx-band::-webkit-slider-thumb { -webkit-appearance: none; width: 13px; height: 13px;
  border-radius: 50%; background: var(--accent, #c4fa55); border: 2px solid var(--bg2, #222);
  box-shadow: 0 1px 3px rgba(0,0,0,.4); cursor: grab; }
#fx-panel .fx-band::-moz-range-track { width: 4px; border-radius: 2px; background: var(--bg4, #333); }
#fx-panel .fx-band::-moz-range-thumb { width: 13px; height: 13px; border-radius: 50%;
  background: var(--accent, #c4fa55); border: 2px solid var(--bg2, #222); cursor: grab; }
#fx-panel .fx-sep { border-top: 1px solid var(--border, #333); margin: 14px 0 10px; }
/* Crossfade + toggle rows */
#fx-panel .fx-ctrl-row { display: flex; align-items: center; gap: 10px; margin: 2px 0 8px; }
#fx-panel .fx-ctrl-label { font-size: 11px; color: var(--fg2, #ddd); width: 62px; flex-shrink: 0; }
#fx-panel .fx-ctrl-val { font-size: 10px; color: var(--fg3, #999); font-family: var(--mono, monospace);
  width: 24px; text-align: right; flex-shrink: 0; }
#fx-panel #fx-fade { flex: 1; accent-color: var(--accent, #c4fa55); height: 4px; }
#fx-panel .fx-check { display: flex; align-items: center; gap: 8px; font-size: 11px;
  color: var(--fg2, #ddd); margin: 7px 0; cursor: pointer; }
#fx-panel .fx-check input { accent-color: var(--accent, #c4fa55); width: 14px; height: 14px; flex-shrink: 0; }
#fx-karaoke { position: fixed; inset: 0; z-index: 1000; display: none;
  background: color-mix(in srgb, var(--bg, #111) 92%, transparent);
  backdrop-filter: blur(6px); text-align: center; cursor: pointer;
  flex-direction: column; align-items: center; justify-content: center; gap: 18px; padding: 40px; }
#fx-karaoke.open { display: flex; }
#fx-karaoke #fx-k-prev, #fx-karaoke #fx-k-next { font-size: 20px; color: var(--fg3, #888); opacity: .7; }
#fx-karaoke #fx-k-cur { font-size: 34px; font-weight: 700; color: var(--accent, #1db954);
  line-height: 1.35; max-width: 900px; }
#fx-karaoke #fx-k-hint { position: absolute; bottom: 18px; left: 0; right: 0;
  font-size: 10px; color: var(--fg3, #777); }`;
  document.head.appendChild(st);
  _fxSyncEqBtn();       // the WebAudio graph itself is created lazily on first use
})();
