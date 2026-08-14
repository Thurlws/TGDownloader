// ═══════════════════════════════════════
//  ICONS  (clean minimal line/solid SVGs, sized via currentColor + 1em)
// ═══════════════════════════════════════
const ICON = {
  play:    '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.14v13.72a1 1 0 0 0 1.54.84l10.5-6.86a1 1 0 0 0 0-1.68L9.54 4.3A1 1 0 0 0 8 5.14z"/></svg>',
  pause:   '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6.5" y="5" width="4" height="14" rx="1"/><rect x="13.5" y="5" width="4" height="14" rx="1"/></svg>',
  prev:    '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="2.5" height="14" rx="1"/><path d="M20 5.7v12.6a1 1 0 0 1-1.54.84L9 13.06a1 1 0 0 1 0-1.68l9.46-6.32A1 1 0 0 1 20 5.7z"/></svg>',
  next:    '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="16.5" y="5" width="2.5" height="14" rx="1"/><path d="M4 5.7v12.6a1 1 0 0 0 1.54.84L15 13.06a1 1 0 0 0 0-1.68L5.54 5.06A1 1 0 0 0 4 5.7z"/></svg>',
  shuffle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3h4v4"/><path d="M3 20 21 3"/><path d="M17 21h4v-4"/><path d="M15 15l6 6"/><path d="M3 4l6 6"/></svg>',
  repeat:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l3.5 3.5L17 9"/><path d="M3 11V9.5A4 4 0 0 1 7 5.5h13.5"/><path d="M7 22l-3.5-3.5L7 15"/><path d="M21 13v1.5a4 4 0 0 1-4 4H3.5"/></svg>',
  repeatOne:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l3.5 3.5L17 9"/><path d="M3 11V9.5A4 4 0 0 1 7 5.5h13.5"/><path d="M7 22l-3.5-3.5L7 15"/><path d="M21 13v1.5a4 4 0 0 1-4 4H3.5"/><text x="12" y="15" font-size="8" font-family="monospace" fill="currentColor" stroke="none" text-anchor="middle">1</text></svg>',
  moon:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  sliders: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/></svg>',
  mic:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 17v4M8 21h8"/></svg>',
  save:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>',
  search:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  copy:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  close:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  plus:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>',
  plusCircle:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/></svg>',
  grid:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1"/></svg>',
  home:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9.5"/><path d="M9.5 21v-6h5v6"/></svg>',
  clock:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/></svg>',
  list:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  group:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 6h10M10 12h10M10 18h10"/><path d="M4 5v6a1 1 0 0 0 1 1"/><path d="M4 13v2"/></svg>',
  note:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>',
  keyboard:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/></svg>',
  arrowUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>',
  arrowDown:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12l7 7 7-7"/></svg>',
  check:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  pencil:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  star:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5l2.6 5.27 5.82.85-4.21 4.1.99 5.78L12 17.77 6.8 19.5l.99-5.78-4.21-4.1 5.82-.85z"/></svg>',
  bookmark:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg>',
  layers:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 8l10 5 10-5-10-5z"/><path d="M2 13l10 5 10-5"/></svg>',
  playlist:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h12M3 12h8M3 18h8"/><circle cx="17" cy="16" r="3"/><path d="M20 16V7l1-.3"/></svg>',
  heart:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20.5 4.2 12.7a4.6 4.6 0 0 1 6.5-6.5l1.3 1.3 1.3-1.3a4.6 4.6 0 0 1 6.5 6.5z"/></svg>',
  heartFilled:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 20.5 4.2 12.7a4.6 4.6 0 0 1 6.5-6.5l1.3 1.3 1.3-1.3a4.6 4.6 0 0 1 6.5 6.5z"/></svg>',
  upload:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/><path d="M12 3v13M7 8l5-5 5 5"/></svg>',
  download:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/><path d="M12 16V3M7 11l5 5 5-5"/></svg>',
  folder:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  stop:    '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v5h-5"/></svg>',
  trash:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/></svg>',
  headphones:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="2.5" y="14" width="4.5" height="7" rx="1.5"/><rect x="17" y="14" width="4.5" height="7" rx="1.5"/></svg>',
  radio:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 16.2a6 6 0 0 0 0-8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 19.1a10 10 0 0 0 0-14.2"/></svg>',
  info:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/></svg>',
  link:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
  sparkle: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9z"/></svg>',
  chevronLeft:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
  chevronRight:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>',
  pin:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 10.76V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v5.76a2 2 0 0 0 .59 1.42l1.7 1.7A1 1 0 0 1 17.59 16H6.41a1 1 0 0 1-.71-1.71l1.7-1.7A2 2 0 0 0 8 11.18"/></svg>',
  pinFilled:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 4a1 1 0 0 0-1 1v5.76a2 2 0 0 1-.59 1.42l-1.7 1.7A1 1 0 0 0 6.41 16H11v6h2v-6h4.59a1 1 0 0 0 .7-1.71l-1.7-1.7A2 2 0 0 1 16 11.18V5a1 1 0 0 0-1-1z"/></svg>',
  panelRight:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14 4v16"/></svg>',
};

// Build "<icon> Label" markup for a button; pair with _applyIcons(btn).
function _btnIco(name, label) {
  return `<span class="bi" data-icon="${name}"></span>${label ?? ''}`;
}
function _setBtnIco(btn, name, label) {
  if (!btn) return;
  btn.innerHTML = _btnIco(name, label);
  _applyIcons(btn);
}

// Fill any element carrying data-icon="name" with its SVG; safe to re-run.
function _applyIcons(root) {
  (root || document).querySelectorAll('[data-icon]').forEach(el => {
    const name = el.getAttribute('data-icon');
    if (ICON[name] && el.dataset.iconDone !== '1') {
      el.innerHTML = ICON[name];
      el.classList.add('ico');
      el.dataset.iconDone = '1';
    }
  });
}
document.addEventListener('DOMContentLoaded', () => _applyIcons());

// ═══════════════════════════════════════
//  STATE
// ═══════════════════════════════════════
let entries          = [];
let viewMode         = 'list';
let collapsedArtists = new Set();
let activeTab        = 'log';
let sessions         = {};
let sessionResults   = {};
let dragSrcIdx       = null;
let running          = false;
let lastArtist       = '';
let ws               = null;

let manifestUrls     = new Set();
let entryMeta = JSON.parse(localStorage.getItem('tgd_entry_meta') || '{}');
let histTagFilter    = '';
let sessionStartTime  = null;
let currentProgress   = null;
let historyData       = [];

let speedHistory  = JSON.parse(localStorage.getItem('tgd_speed_hist') || '[]');
let avgSpeedMBs   = speedHistory.length
  ? speedHistory.reduce((a, b) => a + b, 0) / speedHistory.length : null;

// ── Smooth progress state ─────────────────────────────────────────────────
// Raw data written by WS handler; render loop reads from it at 60fps.
const _prog = {
  raw:        null,   // latest parsed ##PROG## object
  smoothSpeed: 0,     // EMA-smoothed speed MB/s
  displayPct:  0,     // what the CSS bar is currently showing
};
const EMA_ALPHA = 0.12;   // lower = smoother, higher = more responsive
let   _rafId    = null;

// Feed one progress reading into the smoothed bar. Fields come straight from the
// backend's structured "progress" message; the render loop reads _prog at 60fps.
function _applyProgress(p) {
  const rawSpeed = +p.speedMBs;
  _prog.smoothSpeed = _prog.smoothSpeed === 0
    ? rawSpeed
    : EMA_ALPHA * rawSpeed + (1 - EMA_ALPHA) * _prog.smoothSpeed;
  _prog.raw = {
    filesDone: +p.filesDone, filesTotal: +p.filesTotal,
    mbDone: +p.mbDone, mbTotal: +p.mbTotal, speedMBs: rawSpeed,
  };
  currentProgress = _prog.raw;
  if (!_rafId) _startProgressRaf();
}
// ─────────────────────────────────────────────────────────────────────────

let searchHistory = JSON.parse(localStorage.getItem('tgd_search_history') || '[]');
let ctxIdx        = null;
let noteIdx       = null;
let showWelcomeOnStart = localStorage.getItem('tgd_show_welcome') !== 'false';

// Debug log auto-refresh
let _debugRefreshInterval = null;


// ═══════════════════════════════════════
//  WEBSOCKET
// ═══════════════════════════════════════
function connectWS() {
  ws = new WebSocket(`ws://${location.host}/`);
  ws.onopen  = () => setStatus('idle');
  ws.onclose = () => { setStatus('disconnected'); setTimeout(connectWS, 1500); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'log') {
      if (msg.text.startsWith('##PAUSED##'))  { setPauseBtn(true);  return; }
      if (msg.text.startsWith('##RESUMED##')) { setPauseBtn(false); return; }
      appendLog(msg.text);
      if (msg.text.startsWith('##PROG##')) {   // legacy fallback: raw-CLI backend
        const m = msg.text.match(/(\d+)\/(\d+) files\s+([\d.]+)\/([\d.]+) MB\s+([\d.]+) MB\/s/);
        if (m) _applyProgress({
          filesDone: m[1], filesTotal: m[2], mbDone: m[3], mbTotal: m[4], speedMBs: m[5],
        });
      }
    } else if (msg.type === 'progress') {
      _applyProgress(msg);
    } else if (msg.type === 'paused')  {
      setPauseBtn(true);
    } else if (msg.type === 'resumed') {
      setPauseBtn(false);
    } else if (msg.type === 'result') {
      sessionResults[msg.url] = msg;
      if (msg.status === 'ok' || msg.status === 'partial') manifestUrls.add(msg.url);
      renderQueue();
      if (activeTab === 'stats') renderStats();
    } else if (msg.type === 'status') {
      running = msg.running;
      setStatus(running ? 'running' : 'idle');
      _updateRetryFailedBtn();
      if (running) sessionStartTime = Date.now();
    } else if (msg.type === 'done') {
      running = false;
      setStatus('idle');
      _updateRetryFailedBtn();   // running flag changed → refresh the retry button
      const ok = msg.code === 0;
      appendLog(`\nProcess exited (code ${msg.code})\n`, ok ? 'log-success' : 'log-error');
      appendLog('─'.repeat(52) + '\n', 'log-dim');
      if (document.visibilityState !== 'visible') {
        const nOk = Object.values(sessionResults).filter(r => r.status === 'ok').length;
        _notify(ok ? 'Downloads complete' : 'Downloads finished with errors',
                nOk ? `${nOk} item${nOk === 1 ? '' : 's'} downloaded` : 'Session ended');
      }
      if (_prog.raw && _prog.smoothSpeed > 0) {
        speedHistory.push(_prog.smoothSpeed);
        speedHistory = speedHistory.slice(-20);
        localStorage.setItem('tgd_speed_hist', JSON.stringify(speedHistory));
        avgSpeedMBs = speedHistory.reduce((a, b) => a + b, 0) / speedHistory.length;
      }
      // Stop rAF loop, hide progress bar
      _stopProgressRaf();
      document.getElementById('progress-bar-wrap').classList.remove('visible');
      _prog.raw = null; _prog.smoothSpeed = 0; _prog.displayPct = 0;
      loadManifestUrls();
      // A finished session changes what's on disk, so drop cached library stats
      // and refresh whichever data tab is open so downloads show up immediately.
      _libStatsCache = null;
      if (activeTab === 'history') loadHistory();
      if (activeTab === 'stats')   { _fetchLibStats(); renderStats(); }
      if (activeTab === 'library') loadLibrary({ keepView: true });
    } else if (msg.type === 'library-changed') {
      // Files changed on disk (filesystem watcher), mirror the post-download
      // refresh so whichever data tab is open updates without a manual Refresh.
      _libStatsCache = null;
      if (activeTab === 'history') loadHistory();
      if (activeTab === 'stats')   { _fetchLibStats(); renderStats(); }
      if (activeTab === 'library') loadLibrary({ keepView: true });
    } else if (msg.type === 'error') {
      appendLog(`ERROR: ${msg.text}\n`, 'log-error');
    }
  };
}

function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}


// ═══════════════════════════════════════
//  STATUS BAR
// ═══════════════════════════════════════
function setStatus(s) {
  const dot  = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  const btn  = document.getElementById('btn-run');
  dot.className = '';
  const pauseBtn = document.getElementById('btn-pause');
  if (s === 'running') {
    dot.classList.add('running');
    text.textContent = 'Running…';
    text.style.color = '';
    _setBtnIco(btn, 'stop', 'Stop');
    btn.className    = 'btn-stop';
    if (pauseBtn) pauseBtn.style.display = 'block';
  } else if (s === 'disconnected') {
    dot.classList.add('disconnected');
    text.textContent = 'Disconnected';
    text.style.color = 'var(--red)';
    _setBtnIco(btn, 'play', 'Run');
    btn.className    = 'btn-accent';
  } else {
    text.textContent = 'Idle';
    text.style.color = '';
    _setBtnIco(btn, 'play', 'Run');
    btn.className    = 'btn-accent';
    if (pauseBtn) { pauseBtn.style.display = 'none'; setPauseBtn(false); }
  }
}


// ═══════════════════════════════════════
//  LOG PANEL
// ═══════════════════════════════════════
let _logStickyBottom = true;  // true = always scroll to bottom unless user scrolled up

function appendLog(text, forceClass) {
  // ##PROG## lines are now handled entirely by the sticky progress bar, skip them in the log
  if (text.startsWith('##PROG##')) return;

  const log = document.getElementById('log');

  const span = document.createElement('span');
  if (forceClass) {
    span.className = forceClass;
  } else {
    const lower = text.toLowerCase();
    if (/error|warning|failed/.test(lower))                     span.className = 'log-error';
    else if (/✓|sorted|all track|session complete/.test(lower)) span.className = 'log-success';
    else if (/warn/.test(lower))                                span.className = 'log-warn';
    else if (/^[\s]*[═─+|]/.test(text))                         span.className = 'log-dim';
    else if (/^\s+\[\d+\/?\d*\]/.test(text))                    span.className = 'log-track';
  }
  span.textContent = text;
  log.appendChild(span);
  if (_logStickyBottom) log.scrollTop = log.scrollHeight;
}

function clearLog() {
  document.getElementById('log').innerHTML = '';
  _logStickyBottom = true;
}

// Wire up scroll-listener after DOM is ready: user scrolling up disables auto-scroll,
// scrolling back to bottom re-enables it.
document.addEventListener('DOMContentLoaded', () => {
  const log = document.getElementById('log');
  log.addEventListener('scroll', () => {
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
    _logStickyBottom = atBottom;
  }, { passive: true });
});


// ═══════════════════════════════════════
//  TAB SWITCHING
// ═══════════════════════════════════════
function switchTab(tab) {
  if (tab === activeTab) return;   // already on this tab, don't reload/refresh it
  activeTab = tab;
  document.querySelectorAll('.tab').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tab-pane').forEach(p =>
    p.classList.toggle('active', p.id === 'tab-' + tab));

  // Queue panel only appears in the Log tab (frees space for other tabs)
  document.getElementById('app').classList.toggle('queue-hidden', tab !== 'log');

  const clearBtn = document.getElementById('log-clear');
  if (tab === 'stats' || tab === 'debug' || tab === 'library') {
    // These tabs have no "clear" affordance
    clearBtn.style.visibility = 'hidden';
  } else if (tab === 'history') {
    clearBtn.textContent   = 'clear history';
    clearBtn.style.visibility = 'visible';
  } else {
    clearBtn.textContent   = 'clear';
    clearBtn.style.visibility = 'visible';
  }

  if (tab === 'history') loadHistory();
  if (tab === 'stats')   { if (!_libStatsCache) _fetchLibStats(); renderStats(); }
  if (tab === 'debug')   loadDebugLog();
  if (tab === 'library') loadLibrary();
}


// ═══════════════════════════════════════
//  DEBUG LOG TAB
// ═══════════════════════════════════════
async function loadDebugLog() {
  const content = document.getElementById('debug-log-content');
  const pathEl  = document.getElementById('debug-log-path');
  const sizeEl  = document.getElementById('debug-log-size');
  try {
    const resp = await fetch('/debug-log', { cache: 'no-store' });
    const data = await resp.json();

    if (data.error) {
      content.textContent = `Error loading log: ${data.error}`;
      return;
    }

    // Render with simple colour-coding per log level
    content.innerHTML = '';
    const lines = data.log.split('\n');
    lines.forEach(line => {
      const span = document.createElement('span');
      span.style.display = 'block';
      if (/\| ERROR\s+\|/.test(line))    span.className = 'dbg-error';
      else if (/\| WARNING\s+\|/.test(line)) span.className = 'dbg-warn';
      else if (/\| DEBUG\s+\|/.test(line))   span.className = 'dbg-debug';
      else                                    span.className = 'dbg-info';
      span.textContent = line;
      content.appendChild(span);
    });

    // Scroll to bottom
    content.scrollTop = content.scrollHeight;

    if (data.path) pathEl.textContent = data.path;
    if (data.size !== undefined) {
      const kb = (data.size / 1024).toFixed(1);
      sizeEl.textContent = `${kb} KB`;
    }
  } catch (err) {
    content.textContent = `Could not fetch debug log: ${err}`;
  }
}

// ── Debug toolbar wiring (top-level: elements already parsed above this script) ──
(function initDebugToolbar() {
  const refreshBtn = document.getElementById('btn-refresh-log');
  if (refreshBtn) refreshBtn.addEventListener('click', () => loadDebugLog());

  const autoChk = document.getElementById('chk-auto-refresh');
  if (autoChk) autoChk.addEventListener('change', (e) => {
    if (e.target.checked) startDebugAutoRefresh();
    else stopDebugAutoRefresh();
  });

  // Clear log file: inline two-step confirm (no browser popup)
  const clearBtn = document.getElementById('btn-clear-log-file');
  if (clearBtn) {
    let _armed = false, _armTimer = null;
    const disarm = () => {
      _armed = false;
      clearBtn.classList.remove('armed');
      clearBtn.textContent = 'Clear Log File';
      if (_armTimer) { clearTimeout(_armTimer); _armTimer = null; }
    };
    clearBtn.addEventListener('click', async () => {
      if (!_armed) {
        _armed = true;
        clearBtn.classList.add('armed');
        clearBtn.textContent = 'Click again to confirm';
        _armTimer = setTimeout(disarm, 3000);
        return;
      }
      disarm();
      try { await fetch('/debug-log-clear', { method: 'POST' }); } catch (_) {}
      loadDebugLog();
    });
  }
})();

// ── Auto-update check (notify-only) ─────────────────────────────────────────
// Polls the backend, which queries GitHub Releases. We never download or
// replace files, just surface a badge linking to the release page.
let _updateInfo = null;

async function _loadAppVersion() {
  try {
    const d  = await (await fetch('/version')).json();
    const el = document.getElementById('app-ver');
    if (el && d.version) el.textContent = 'v' + d.version;
  } catch (_) {}
}

async function _checkForUpdate(force = false) {
  const badge   = document.getElementById('update-badge');
  const badgeTx = document.getElementById('update-badge-text');
  const status  = document.getElementById('update-status');
  const aboutS  = document.getElementById('about-update-status');
  if (force && status) status.textContent = 'Checking…';
  if (force && aboutS) aboutS.textContent = 'Checking…';
  let data;
  try {
    data = await (await fetch('/check-update' + (force ? '?force=1' : ''))).json();
  } catch (_) {
    if (status) status.textContent = 'Update check failed (offline?)';
    if (aboutS) aboutS.textContent = 'Check failed (offline?)';
    return;
  }
  _updateInfo = data;
  const dlBtn   = document.getElementById('btn-about-download');
  const instBtn = document.getElementById('btn-about-install');
  if (data.update_available) {
    if (badge)   { badge.style.display = 'inline-flex';
                   badge.title = `Version ${data.latest} is available — you have ${data.current}. Click to view the release.`; }
    if (badgeTx) badgeTx.textContent = 'Update to v' + (data.latest || '');
    if (status)  status.textContent = `Update available: v${data.latest} (you have v${data.current})`;
    if (aboutS)  { aboutS.innerHTML = `<a href="#" id="about-update-link" style="color:var(--accent)">Update available: v${data.latest} ↗</a>`;
                   document.getElementById('about-update-link')?.addEventListener('click', (e) => { e.preventDefault(); _openReleasePage(); }); }
    // Packaged Windows app installs in place and restarts; everywhere
    // else falls back to opening the zip for a manual folder replace.
    const canApply = data.can_apply && data.download_url;
    if (instBtn) { instBtn.style.display = canApply ? '' : 'none';
                   instBtn.textContent = `Install v${data.latest} & restart`; }
    if (dlBtn)   { dlBtn.style.display = (!canApply && data.download_url) ? '' : 'none';
                   dlBtn.textContent = `Download v${data.latest}`; }
  } else {
    if (badge)   badge.style.display = 'none';
    if (status)  status.textContent = data.error ? data.error : `Up to date (v${data.current})`;
    if (aboutS)  aboutS.textContent = data.error ? data.error : `Up to date (v${data.current})`;
    if (dlBtn)   dlBtn.style.display = 'none';
    if (instBtn) instBtn.style.display = 'none';
  }
}

async function _installUpdate() {
  if (!_updateInfo || !_updateInfo.download_url) { _openReleasePage(); return; }
  const ok = confirm(`Download v${_updateInfo.latest} and restart to install it?\n\n`
    + `Your library, settings and Telegram session are kept. TGDownloader will `
    + `close and reopen automatically.`);
  if (!ok) return;
  const instBtn = document.getElementById('btn-about-install');
  const aboutS  = document.getElementById('about-update-status');
  if (instBtn) { instBtn.disabled = true; instBtn.textContent = 'Downloading…'; }
  if (aboutS)    aboutS.textContent = 'Downloading update…';
  try {
    const r = await (await fetch('/apply-update',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
    if (r.ok && r.restarting) {
      if (aboutS)  aboutS.textContent = 'Installing… the app will restart and this tab will reconnect.';
      if (instBtn) instBtn.textContent = 'Restarting…';
    } else {
      if (aboutS)  aboutS.textContent = r.error || 'Update failed.';
      if (instBtn) { instBtn.disabled = false; instBtn.textContent = `Install v${_updateInfo.latest} & restart`; }
    }
  } catch (_) {
    // Expected on success: the server drops the connection as it exits to swap.
    if (aboutS) aboutS.textContent = 'Installing… the app will restart and this tab will reconnect.';
  }
}

function _downloadUpdate() {
  const url = _updateInfo && _updateInfo.download_url;
  if (!url) { _openReleasePage(); return; }
  const a = document.createElement('a');
  a.href = url; a.target = '_blank'; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
}

function _openReleasePage() {
  const url = (_updateInfo && _updateInfo.url) || 'https://github.com/Thurlws/TGDownloader/releases';
  const a = document.createElement('a');
  a.href = url; a.target = '_blank'; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
}

(function initUpdateUI() {
  document.getElementById('update-badge')?.addEventListener('click', _openReleasePage);
  document.getElementById('btn-check-update')?.addEventListener('click', () => _checkForUpdate(true));
  _loadAppVersion();
  _checkForUpdate(false);     // silent, cache-backed check on launch
})();

// PWA: register the (network-only) service worker so the app is installable /
// can be added to a phone home screen. Self-contained: references nothing from
// later modules. Failure is non-fatal (e.g. insecure context on a LAN IP).
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

// New-release radar: silent check on launch if the user has opted in.
(async function initReleaseRadar() {
  try {
    await _loadWatchlist();
    if (!_watchedIds.size) return;
    const cfg = await fetch('/config').then(r => r.json()).catch(() => ({}));
    if (cfg.watchlist_autocheck !== false) checkReleases(false);
  } catch (_) {}
})();

function startDebugAutoRefresh() {
  stopDebugAutoRefresh();
  _debugRefreshInterval = setInterval(loadDebugLog, 5000);
}

function stopDebugAutoRefresh() {
  if (_debugRefreshInterval) {
    clearInterval(_debugRefreshInterval);
    _debugRefreshInterval = null;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-refresh-lib').addEventListener('click', async () => {
    // Persist any unsaved home-input value before refreshing
    const val = document.getElementById('home-input').value.trim();
    if (val) {
      await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ home_music_folder: val }) });
    }
    // Reset cached library data so loadLibrary fetches fresh
    _libAllAlbums  = [];
    _libArtistList = [];
    _libActiveArtist = null;
    _libActiveAlbum  = null;
    _libStatsCache = null;
    _libStatsForceNext = true;   // explicit refresh → bypass the server stats cache once
    loadLibrary();
  });

  document.getElementById('lib-filter')?.addEventListener('input', () => {
    if (_libArtistList.length) _libApplyArtistFilter();
  });

  document.getElementById('lib-album-filter')?.addEventListener('input', () => {
    if (_libActiveArtist && _libView === 'albums') _libApplyAlbumFilter();
  });

  document.getElementById('lib-album-sort')?.addEventListener('change', (e) => {
    _libAlbumSort = e.target.value || 'az';
    if (_libActiveArtist && _libView === 'albums') _libApplyAlbumFilter();
  });

  // Search is always visible; clicking anywhere on the box focuses the field.
  document.getElementById('lib-album-search')?.addEventListener('click', () => {
    document.getElementById('lib-album-filter')?.focus();
  });

  document.getElementById('lib-add-artist-row')?.addEventListener('click', () => _openArtistSearch());
});


// ═══════════════════════════════════════
//  DELETE CONFIRM DIALOG
// ═══════════════════════════════════════
let _deleteCallback = null;

function showDeleteConfirm({ title, msg, detail, onConfirm }) {
  _deleteCallback = onConfirm;
  const titleEl  = document.getElementById('delete-box-title');
  const msgEl    = document.getElementById('delete-box-msg');
  const detailEl = document.getElementById('delete-box-detail');
  if (titleEl)  titleEl.textContent  = title  || 'Confirm Delete';
  if (msgEl)    msgEl.textContent    = msg    || 'Are you sure?';
  if (detailEl) {
    detailEl.textContent = detail || '';
    detailEl.style.display = detail ? '' : 'none';
  }
  document.getElementById('delete-overlay').classList.add('open');
  document.getElementById('btn-delete-confirm').focus();
}

function closeDeleteConfirm() {
  document.getElementById('delete-overlay').classList.remove('open');
  _deleteCallback = null;
}

// ── Load-session confirm dialog ──────────────────────────────────────────────
let _loadSessionCallback = null;

function showLoadSessionConfirm({ sessionName, itemCount, queueCount, onConfirm }) {
  _loadSessionCallback = onConfirm;
  document.getElementById('load-session-msg').textContent =
    `Your current queue has ${queueCount} item${queueCount !== 1 ? 's' : ''}. Loading this session will replace it.`;
  document.getElementById('load-session-detail').textContent =
    `${sessionName}  ·  ${itemCount} item${itemCount !== 1 ? 's' : ''}`;
  document.getElementById('load-session-sub').textContent =
    'The current queue will be lost unless you save it first.';
  document.getElementById('load-session-overlay').classList.add('open');
  document.getElementById('btn-lsc-confirm').focus();
}

function closeLoadSessionConfirm() {
  document.getElementById('load-session-overlay').classList.remove('open');
  _loadSessionCallback = null;
}

// ── Genre playlist builder ───────────────────────────────────────────────────
async function openGenrePlaylistModal() {
  const overlay = document.getElementById('genre-pl-overlay');
  const select  = document.getElementById('genre-pl-select');
  const nameEl  = document.getElementById('genre-pl-name');
  const status  = document.getElementById('genre-pl-status');
  status.textContent = ''; status.className = '';
  nameEl.value = '';
  select.innerHTML = '<option value="">Loading genres…</option>';
  overlay.classList.add('open');

  try {
    const resp = await fetch('/genres');
    const data = await resp.json();
    if (data.error) {
      select.innerHTML = '<option value="">—</option>';
      status.textContent = data.error; status.className = 'error';
      return;
    }
    const genres = data.genres || [];
    if (!genres.length) {
      select.innerHTML = '<option value="">No genres found in your library</option>';
      status.textContent = 'Tracks need a genre tag to appear here.';
      return;
    }
    select.innerHTML = genres.map(g =>
      `<option value="${escHtml(g.genre)}">${escHtml(g.genre)} (${g.track_count} track${g.track_count !== 1 ? 's' : ''})</option>`
    ).join('');
  } catch (e) {
    select.innerHTML = '<option value="">—</option>';
    status.textContent = `Error: ${e}`; status.className = 'error';
  }
}

function closeGenrePlaylistModal() {
  document.getElementById('genre-pl-overlay').classList.remove('open');
}

async function _createGenrePlaylist() {
  const select = document.getElementById('genre-pl-select');
  const nameEl = document.getElementById('genre-pl-name');
  const status = document.getElementById('genre-pl-status');
  const btn    = document.getElementById('btn-genre-pl-create');
  const genre  = select.value;
  if (!genre) { status.textContent = 'Pick a genre first.'; status.className = 'error'; return; }

  btn.disabled = true;
  status.textContent = 'Copying tracks…'; status.className = '';
  try {
    const resp = await fetch('/create-genre-playlist', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ genre, name: nameEl.value.trim() }),
    });
    const data = await resp.json();
    if (!data.ok) {
      status.textContent = data.error || 'Failed to create playlist.'; status.className = 'error';
      return;
    }
    status.textContent = `Created "${data.name}" — ${data.copied} of ${data.total} tracks.`;
    status.className = 'success';
    appendLog(`  Created genre playlist "${data.name}" (${data.copied} tracks)\n`, 'log-success');
    setTimeout(() => { closeGenrePlaylistModal(); if (activeTab === 'library') loadLibrary(); }, 900);
  } catch (e) {
    status.textContent = `Error: ${e}`; status.className = 'error';
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('lib-add-playlist-row')?.addEventListener('click', openGenrePlaylistModal);
document.getElementById('btn-genre-pl-cancel')?.addEventListener('click', closeGenrePlaylistModal);
document.getElementById('btn-genre-pl-create')?.addEventListener('click', _createGenrePlaylist);
document.getElementById('genre-pl-overlay')?.addEventListener('click', (e) => {
  if (e.target === document.getElementById('genre-pl-overlay')) closeGenrePlaylistModal();
});

// ── Smart playlist builder ──────────────────────────────────────────────────
// Rule field -> input id. Also drives the "Describe it" fill, so the two stay
// in step: adding a rule here wires it into both paths at once.
const SMART_PL_FIELDS = {
  name:            'smart-pl-name',
  format:          'smart-pl-format',
  genre:           'smart-pl-genre',
  artist:          'smart-pl-artist',
  added_days:      'smart-pl-days',
  limit:           'smart-pl-limit',
  min_rating:      'smart-pl-rating',
  not_played_days: 'smart-pl-notplayed',
  bpm_min:         'smart-pl-bpm-min',
  bpm_max:         'smart-pl-bpm-max',
  sort:            'smart-pl-sort',
};

function openSmartPlaylistModal() {
  const st = document.getElementById('smart-pl-status');
  if (st) { st.textContent = ''; st.className = ''; }
  ['smart-pl-name','smart-pl-genre','smart-pl-artist','smart-pl-days','smart-pl-limit',
   'smart-pl-rating','smart-pl-notplayed','smart-pl-bpm-min','smart-pl-bpm-max'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  const fmt = document.getElementById('smart-pl-format'); if (fmt) fmt.value = '';
  const srt = document.getElementById('smart-pl-sort');   if (srt) srt.value = 'az';
  const nl  = document.getElementById('smart-pl-nl-input');
  if (nl) nl.value = '';
  const nlSt = document.getElementById('smart-pl-nl-status');
  if (nlSt) { nlSt.textContent = ''; nlSt.className = ''; }
  _clearNlHighlights();
  document.getElementById('smart-pl-overlay')?.classList.add('open');
  setTimeout(() => document.getElementById('smart-pl-nl-input')?.focus(), 20);
}

function _clearNlHighlights() {
  Object.values(SMART_PL_FIELDS).forEach(id => {
    document.getElementById(id)?.classList.remove('nl-filled');
  });
}

// Writes a parsed rule set into the form. Nothing is created here: the user
// still reviews the fields and presses Create, so a bad reading is visible
// before it costs anything.
function _applySmartPlaylistRules(rules, filled) {
  _clearNlHighlights();
  Object.entries(SMART_PL_FIELDS).forEach(([field, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    const val = rules[field];
    if (field === 'sort') {
      el.value = val === 'recent' ? 'recent' : 'az';
    } else if (typeof val === 'number') {
      el.value = val > 0 ? String(val) : '';
    } else {
      el.value = val || '';
    }
  });
  (filled || []).forEach(field => {
    document.getElementById(SMART_PL_FIELDS[field])?.classList.add('nl-filled');
  });
}

async function _fillSmartPlaylistFromText() {
  const input  = document.getElementById('smart-pl-nl-input');
  const status = document.getElementById('smart-pl-nl-status');
  const btn    = document.getElementById('btn-smart-pl-nl');
  const query  = (input?.value || '').trim();
  if (!query) {
    status.textContent = 'Describe the playlist you want first.';
    status.className = 'error';
    return;
  }
  btn.disabled = true;
  status.textContent = 'Reading…';
  status.className = '';
  try {
    const resp = await fetch('/parse-playlist-rules', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const data = await resp.json();
    if (!data.ok) {
      status.textContent = data.error || 'Could not read that.';
      status.className = 'error';
      return;
    }
    _applySmartPlaylistRules(data.rules, data.filled);
    const lines = [];
    if (!data.filled.length) {
      lines.push('No rules matched. Fill the fields in by hand, or rephrase.');
    } else {
      const via = data.source === 'claude' ? ' via Claude' : '';
      lines.push(`Set ${data.filled.length} rule${data.filled.length !== 1 ? 's' : ''}${via}. Check them, then Create.`);
    }
    if (data.unparsed?.length) lines.push(`Ignored: ${data.unparsed.join(', ')}.`);
    (data.notes || []).forEach(n => lines.push(n));
    status.textContent = lines.join(' ');
    status.className = data.filled.length ? 'success' : 'error';
  } catch (e) {
    status.textContent = `Error: ${e}`;
    status.className = 'error';
  } finally {
    btn.disabled = false;
  }
}
function closeSmartPlaylistModal() {
  document.getElementById('smart-pl-overlay')?.classList.remove('open');
}
async function _createSmartPlaylist() {
  const status = document.getElementById('smart-pl-status');
  const btn    = document.getElementById('btn-smart-pl-create');
  const body = {
    name:            document.getElementById('smart-pl-name').value.trim(),
    format:          document.getElementById('smart-pl-format').value,
    genre:           document.getElementById('smart-pl-genre').value.trim(),
    artist:          document.getElementById('smart-pl-artist').value.trim(),
    added_days:      document.getElementById('smart-pl-days').value.trim(),
    limit:           document.getElementById('smart-pl-limit').value.trim(),
    min_rating:      document.getElementById('smart-pl-rating')?.value || '',
    not_played_days: document.getElementById('smart-pl-notplayed')?.value.trim() || '',
    bpm_min:         document.getElementById('smart-pl-bpm-min')?.value.trim() || '',
    bpm_max:         document.getElementById('smart-pl-bpm-max')?.value.trim() || '',
    sort:            document.getElementById('smart-pl-sort').value,
  };
  if (!body.name) { status.textContent = 'Give the playlist a name.'; status.className = 'error'; return; }
  if (!body.format && !body.genre && !body.artist && !body.added_days
      && !body.min_rating && !body.not_played_days && !body.bpm_min && !body.bpm_max) {
    status.textContent = 'Set at least one rule.'; status.className = 'error'; return;
  }
  btn.disabled = true;
  status.textContent = 'Building playlist…'; status.className = '';
  try {
    const resp = await fetch('/create-smart-playlist', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.ok) { status.textContent = data.error || 'Failed to create playlist.'; status.className = 'error'; return; }
    status.textContent = `Created "${data.name}" — ${data.copied} track${data.copied !== 1 ? 's' : ''}.`;
    status.className = 'success';
    appendLog(`  Created smart playlist "${data.name}" (${data.copied} tracks)\n`, 'log-success');
    _libStatsCache = null;
    setTimeout(() => { closeSmartPlaylistModal(); if (activeTab === 'library') loadLibrary(); }, 900);
  } catch (e) {
    status.textContent = `Error: ${e}`; status.className = 'error';
  } finally {
    btn.disabled = false;
  }
}
document.getElementById('lib-add-smart-row')?.addEventListener('click', openSmartPlaylistModal);
document.getElementById('btn-smart-pl-cancel')?.addEventListener('click', closeSmartPlaylistModal);
document.getElementById('btn-smart-pl-create')?.addEventListener('click', _createSmartPlaylist);
document.getElementById('btn-smart-pl-nl')?.addEventListener('click', _fillSmartPlaylistFromText);
document.getElementById('smart-pl-nl-input')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); _fillSmartPlaylistFromText(); }
});
document.getElementById('smart-pl-overlay')?.addEventListener('click', (e) => {
  if (e.target === document.getElementById('smart-pl-overlay')) closeSmartPlaylistModal();
});

// ═══════════════════════════════════════
//  KEYBINDS PANEL
// ═══════════════════════════════════════
function toggleKeybindsPanel() {
  const overlay = document.getElementById('keybinds-overlay');
  overlay.classList.toggle('open');
}

document.getElementById('keybinds-close')?.addEventListener('click', () =>
  document.getElementById('keybinds-overlay').classList.remove('open'));
document.getElementById('keybinds-overlay')?.addEventListener('click', (e) => {
  if (e.target === document.getElementById('keybinds-overlay'))
    document.getElementById('keybinds-overlay').classList.remove('open');
});


// ═══════════════════════════════════════
//  QUIT
// ═══════════════════════════════════════
function openQuitDialog() {
  const msg = document.getElementById('quit-msg');
  if (running) {
    msg.textContent = 'A download is in progress — it will be stopped.';
    msg.style.color = 'var(--yellow)';
  } else {
    msg.textContent = 'The server will stop and the browser tab will close.';
    msg.style.color = '';
  }
  document.getElementById('quit-overlay').classList.add('open');
}

async function confirmQuit() {
  document.getElementById('btn-quit-confirm').disabled = true;
  document.getElementById('btn-quit-confirm').textContent = 'Quitting…';
  try {
    await fetch('/quit', { method: 'POST' });
  } catch (_) {}
  // Server is shutting down, close the tab
  setTimeout(() => window.close(), 600);
}


// ═══════════════════════════════════════
//  QUEUE PERSISTENCE
// ═══════════════════════════════════════
function saveWorkingQueue() {
  try { localStorage.setItem('tgd_working_queue', JSON.stringify(entries)); } catch (_) {}
}

function loadWorkingQueue() {
  try {
    const saved = localStorage.getItem('tgd_working_queue');
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.length) entries = parsed;
    }
  } catch (_) {}
}

function saveEntryMeta() {
  try { localStorage.setItem('tgd_entry_meta', JSON.stringify(entryMeta)); } catch (_) {}
}

function getEntryMeta(url) {
  if (!entryMeta[url]) entryMeta[url] = { tags: [], note: '' };
  return entryMeta[url];
}


// ═══════════════════════════════════════
//  MANIFEST URLS
// ═══════════════════════════════════════
async function loadManifestUrls() {
  try {
    const resp = await fetch('/history');
    const data = await resp.json();
    manifestUrls = new Set(data.map(d => d.url));
    renderQueue();
  } catch (_) {}
}


// ═══════════════════════════════════════
//  RAF PROGRESS RENDER LOOP
//  Runs at 60fps independently of WS messages.
//  Reads from _prog.raw / _prog.smoothSpeed.
// ═══════════════════════════════════════
function _startProgressRaf() {
  if (_rafId) return;
  let _lastStatsRender = 0;
  function _tick() {
    _rafId = requestAnimationFrame(_tick);
    _renderProgressBar();
    // Refresh the regular stats page ~2×/sec while downloading (no live graph)
    if (activeTab === 'stats') {
      const now = performance.now();
      if (now - _lastStatsRender > 500) { _lastStatsRender = now; renderStats(); }
    }
  }
  _rafId = requestAnimationFrame(_tick);
}

function _stopProgressRaf() {
  if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
}

function _renderProgressBar() {
  const wrap = document.getElementById('progress-bar-wrap');
  if (!_prog.raw) { wrap.classList.remove('visible'); return; }
  wrap.classList.add('visible');

  const { filesDone, filesTotal, mbDone, mbTotal } = _prog.raw;
  const pct = mbTotal > 0 ? (mbDone / mbTotal) * 100 : 0;

  // CSS transition on progress-fill handles visual smoothing already
  document.getElementById('progress-fill').style.width = pct.toFixed(2) + '%';

  // EMA-smoothed speed display
  document.getElementById('prog-speed').textContent = _prog.smoothSpeed.toFixed(2);

  // Files counter
  document.getElementById('prog-files').textContent =
    `${filesDone} / ${filesTotal} files`;

  // MB counter
  document.getElementById('prog-mb').textContent =
    `${mbDone.toFixed(1)} / ${mbTotal.toFixed(1)} MB`;

  // ETA using smoothed speed
  const etaEl = document.getElementById('prog-eta');
  if (_prog.smoothSpeed > 0 && mbTotal > mbDone) {
    const secs = (mbTotal - mbDone) / _prog.smoothSpeed;
    etaEl.textContent = secs < 60
      ? `~${Math.round(secs)}s`
      : `~${Math.round(secs / 60)}m ${Math.round(secs % 60)}s`;
    etaEl.style.display = '';
  } else {
    etaEl.style.display = 'none';
  }

  // Mirror smoothed speed to title-bar ETA and stats if needed
  if (activeTab === 'stats') renderStats();
}


// ═══════════════════════════════════════
//  ETA INDICATOR
// ═══════════════════════════════════════
function updateEtaIndicator() {
  let knownTracks = 0, unknownCount = 0;
  entries.forEach(e => e.nbTracks ? (knownTracks += e.nbTracks) : unknownCount++);
  const estimatedMB = (knownTracks + unknownCount * 10) * 8;

  let etaStr = '';
  if (avgSpeedMBs && avgSpeedMBs > 0 && estimatedMB > 0) {
    const secs = estimatedMB / avgSpeedMBs;
    etaStr = secs < 60 ? `ETA ~${Math.round(secs)}s` : `ETA ~${Math.round(secs / 60)} min`;
  }

  const ind   = document.getElementById('eta-indicator');
  const title = document.getElementById('titlebar-eta');
  if (ind)   ind.textContent = etaStr || (entries.length ? '— ETA unknown (run once to calibrate) —' : '');
  if (title) {
    title.textContent = etaStr;
    title.classList.toggle('visible', !!(etaStr && entries.length));
  }
}


// ═══════════════════════════════════════
