//  SETTINGS MODAL
// ═══════════════════════════════════════
const CFG_FIELDS = {
  'cfg-bot':           'bot_username',
  'cfg-reply-timeout': 'reply_timeout',
  'cfg-queue-wait':    'queue_wait_timeout',
  'cfg-idle-timeout':  'inter_file_idle_timeout',
  'cfg-idle-check':    'idle_check_interval',
  'cfg-busy-wait':     'bot_busy_wait',
  'cfg-busy-retries':  'bot_busy_retries',
  'cfg-max-parallel':  'max_parallel_downloads',
  'cfg-max-queue':     'max_queue',
};

function applyTheme(theme) {
  document.documentElement.classList.toggle('theme-light', theme === 'light');
  try { localStorage.setItem('tgd_theme', theme); } catch (_) {}
}

// Desktop notification (opt-in; only fires when enabled + permission granted)
function _notify(title, body) {
  try {
    if (localStorage.getItem('tgd_notify') !== 'true') return;
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    new Notification(title, { body });
  } catch (_) {}
}

// Apply theme as early as possible: localStorage first (instant, no flash),
// then reconcile with the server config.
applyTheme(localStorage.getItem('tgd_theme') || 'dark');
fetch('/config').then(r => r.json()).then(cfg => { if (cfg.theme) applyTheme(cfg.theme); }).catch(() => {});

function applyScale(v) {
  const n   = Math.min(2.0, Math.max(0.6, parseFloat(v) || 1.0));
  const app = document.getElementById('app');
  const inv = (100 / n).toFixed(6);
  app.style.transformOrigin = 'top left';
  app.style.transform  = `scale(${n})`;
  app.style.width  = `${inv}vw`;
  app.style.height = `${inv}vh`;
  return n;
}

(function initScale() {
  const range = document.getElementById('cfg-scale-range');
  const text  = document.getElementById('cfg-scale-text');
  function syncFrom(val) {
    const n = applyScale(val);
    range.value = n; text.value = n.toFixed(2);
  }
  range.addEventListener('input', () => syncFrom(range.value));
  text.addEventListener('change', () => syncFrom(text.value));
  fetch('/config').then(r => r.json()).then(cfg => syncFrom(cfg.ui_scale ?? 1.0)).catch(() => syncFrom(1.0));
})();

function openSettings() {
  fetch('/config').then(r => r.json()).then(cfg => {
    for (const [id, key] of Object.entries(CFG_FIELDS)) {
      const el = document.getElementById(id);
      if (el) el.value = cfg[key] ?? '';
    }
    const s = parseFloat(cfg.ui_scale ?? 1.0);
    document.getElementById('cfg-scale-range').value = s;
    document.getElementById('cfg-scale-text').value  = s.toFixed(2);
    document.getElementById('cfg-show-welcome').checked = showWelcomeOnStart;
    // Scrobbling
    document.getElementById('cfg-scrobble-enabled').checked = !!cfg.scrobble_enabled;
    document.getElementById('cfg-scrobble-service').value   = cfg.scrobble_service || 'listenbrainz';
    document.getElementById('cfg-listenbrainz-token').value = cfg.listenbrainz_token || '';
    document.getElementById('cfg-lastfm-key').value         = cfg.lastfm_api_key || '';
    document.getElementById('cfg-lastfm-secret').value      = cfg.lastfm_secret || '';
    document.getElementById('cfg-lastfm-sk').value          = cfg.lastfm_session_key || '';
    // Spotify search API credentials
    document.getElementById('cfg-spotify-id').value         = cfg.spotify_client_id || '';
    document.getElementById('cfg-spotify-secret').value     = cfg.spotify_client_secret || '';
    // Post-download hook (v1.8.0)
    const postCmd = document.getElementById('cfg-post-cmd');
    if (postCmd) postCmd.value = cfg.post_download_command || '';
    // Auto-refresh library watcher (v1.11.0)
    const watchLib = document.getElementById('cfg-watch-library');
    if (watchLib) watchLib.checked = !!cfg.watch_library;
    _loadTgHealth();
    _syncScrobbleService();
    // Theme + notifications
    document.getElementById('cfg-theme-light').checked = (cfg.theme === 'light');
    document.getElementById('cfg-notify').checked = (localStorage.getItem('tgd_notify') === 'true');
    document.getElementById('cfg-autoplay').checked = _mpAutoplayRadio;
    // Version (head + About panel)
    const ver = (document.getElementById('app-ver')?.textContent || '').replace(/^v/, '') || '';
    const vEl = document.getElementById('settings-version'); if (vEl) vEl.textContent = ver ? 'v' + ver : '';
    const aEl = document.getElementById('about-version');    if (aEl) aEl.textContent = ver ? 'v' + ver : '—';
    _settingsShowPanel('connection');
    document.getElementById('modal-overlay').classList.add('open');
  });
}

// Telegram flood-wait health block on the Connection panel (v1.8.0)
async function _loadTgHealth() {
  const el = document.getElementById('tg-health');
  if (!el) return;
  try {
    const d = await (await fetch('/telegram-health')).json();
    if (!d.total_recorded) {
      el.innerHTML = '<span style="color:var(--accent)">✓ No flood-waits recorded this session.</span>';
      return;
    }
    const recent = (d.recent || []).slice(0, 5).map(e =>
      `<div style="font-family:var(--mono);font-size:10px;padding:1px 0">
         ${escHtml(e.ts)} — waited ${e.wait}s <span style="color:var(--fg3)">(${escHtml(e.file)})</span>
       </div>`).join('');
    el.innerHTML = `
      <div>Last hour: <strong${d.last_hour ? ' style="color:var(--yellow)"' : ''}>${d.last_hour}</strong>
        · last 24 h: <strong>${d.last_24h}</strong>
        · total wait (24 h): <strong>${d.wait_secs_24h}s</strong></div>
      ${recent ? `<div style="margin-top:6px">${recent}</div>` : ''}`;
  } catch (_) {
    el.textContent = 'Could not load health data.';
  }
}

// Settings: switch which category panel is visible
function _settingsShowPanel(name) {
  document.querySelectorAll('.settings-nav-item').forEach(b =>
    b.classList.toggle('active', b.dataset.spanel === name));
  document.querySelectorAll('.settings-panel').forEach(p =>
    p.classList.toggle('active', p.dataset.spanel === name));
}

// Show only the relevant credential fields for the chosen scrobble service
function _syncScrobbleService() {
  const svc = document.getElementById('cfg-scrobble-service').value;
  document.getElementById('cfg-lb-wrap').style.display     = (svc === 'listenbrainz') ? '' : 'none';
  document.getElementById('cfg-lastfm-wrap').style.display = (svc === 'lastfm') ? '' : 'none';
}

function saveSettings() {
  const patch = {};
  for (const [id, key] of Object.entries(CFG_FIELDS)) {
    const el = document.getElementById(id);
    if (!el) continue;
    const v = el.value.trim();
    if (!v) continue;
    patch[key] = id !== 'cfg-bot' ? (parseFloat(v) || v) : v;
  }
  const sv = parseFloat(document.getElementById('cfg-scale-text').value);
  if (!isNaN(sv)) patch.ui_scale = Math.min(2.0, Math.max(0.6, sv));

  showWelcomeOnStart = document.getElementById('cfg-show-welcome').checked;
  localStorage.setItem('tgd_show_welcome', showWelcomeOnStart ? 'true' : 'false');

  // Scrobbling (kept as raw strings — never numeric-coerced like CFG_FIELDS)
  patch.scrobble_enabled   = document.getElementById('cfg-scrobble-enabled').checked;
  patch.scrobble_service   = document.getElementById('cfg-scrobble-service').value;
  patch.listenbrainz_token = document.getElementById('cfg-listenbrainz-token').value.trim();
  patch.lastfm_api_key     = document.getElementById('cfg-lastfm-key').value.trim();
  patch.lastfm_secret      = document.getElementById('cfg-lastfm-secret').value.trim();
  patch.lastfm_session_key = document.getElementById('cfg-lastfm-sk').value.trim();

  // Spotify search API credentials (raw strings)
  patch.spotify_client_id     = document.getElementById('cfg-spotify-id').value.trim();
  patch.spotify_client_secret = document.getElementById('cfg-spotify-secret').value.trim();

  // Post-download hook — always sent (raw string) so clearing the field
  // actually disables the hook.
  const postCmdEl = document.getElementById('cfg-post-cmd');
  if (postCmdEl) patch.post_download_command = postCmdEl.value.trim();

  // Auto-refresh library watcher — always sent so toggling off is honoured; the
  // server (re)starts or stops the watcher thread when this key is present.
  const watchLibEl = document.getElementById('cfg-watch-library');
  if (watchLibEl) patch.watch_library = watchLibEl.checked;

  // Theme
  const theme = document.getElementById('cfg-theme-light').checked ? 'light' : 'dark';
  patch.theme = theme;
  applyTheme(theme);

  // Desktop notifications (browser-local preference; request permission on enable)
  const notifyOn = document.getElementById('cfg-notify').checked;
  localStorage.setItem('tgd_notify', notifyOn ? 'true' : 'false');
  _mpAutoplayRadio = document.getElementById('cfg-autoplay').checked;
  localStorage.setItem('tgdl_autoplay', _mpAutoplayRadio ? '1' : '0');
  if (notifyOn && 'Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {});
  }

  fetch('/config', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }).then(() => {
    if (patch.ui_scale !== undefined) applyScale(patch.ui_scale);
    document.getElementById('modal-overlay').classList.remove('open');
  });
}

function closeAllModals() {
  document.getElementById('modal-overlay').classList.remove('open');
  document.getElementById('search-overlay').classList.remove('open');
  document.getElementById('quit-overlay').classList.remove('open');
  document.getElementById('tg-auth-overlay').classList.remove('open');
  document.getElementById('sessions-overlay').classList.remove('open');
  document.getElementById('load-session-overlay').classList.remove('open');
  document.getElementById('genre-pl-overlay').classList.remove('open');
  document.getElementById('smart-pl-overlay')?.classList.remove('open');
  document.getElementById('libsearch-overlay')?.classList.remove('open');
  closeKebabMenu();
  closeCtxMenu();
}


// ═══════════════════════════════════════
//  KEYBOARD SHORTCUTS
// ═══════════════════════════════════════
document.addEventListener('keydown', (e) => {
  const inInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);

  // Ctrl+? works everywhere
  if (e.ctrlKey && e.key === '/') { e.preventDefault(); toggleKeybindsPanel(); return; }

  if (e.key === 'Escape') {
    closeAllModals();
    document.getElementById('note-overlay').classList.remove('open');
    document.getElementById('keybinds-overlay').classList.remove('open');
    return;
  }

  // Space bar — play/pause mini player (works everywhere except when typing)
  if (e.key === ' ' && !inInput) {
    const player = document.getElementById('mini-player');
    if (player && !player.classList.contains('hidden')) {
      e.preventDefault();
      document.getElementById('mp-play-btn')?.click();
      return;
    }
  }

  if (!inInput) {
    if (e.ctrlKey && e.key === 'k')     { e.preventDefault(); openSearch(); }
    if (e.key === '/' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); openLibSearch(); }
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); runOrStop(); }
    if (e.ctrlKey && e.key === 'l')     { e.preventDefault(); clearLog(); showWelcome(); }
    if (e.ctrlKey && e.key === 's')     { e.preventDefault(); saveSession(); }
    if (e.key === '1') { e.preventDefault(); switchTab('log'); }
    if (e.key === '2') { e.preventDefault(); switchTab('history'); }
    if (e.key === '3') { e.preventDefault(); switchTab('library'); }
    if (e.key === '4') { e.preventDefault(); switchTab('stats'); }
    if (e.key === '5') { e.preventDefault(); switchTab('debug'); }
  } else {
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); runOrStop(); }
    if (e.ctrlKey && e.key === 'k')     { e.preventDefault(); openSearch(); }
  }
});


// ═══════════════════════════════════════
//  UTILS
// ═══════════════════════════════════════
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// In-app confirm dialog (replaces native confirm()). Returns a Promise<bool>.
function _confirm(message, opts = {}) {
  return new Promise(resolve => {
    const ov     = document.getElementById('confirm-overlay');
    const okBtn  = document.getElementById('confirm-box-ok');
    const cancel = document.getElementById('confirm-box-cancel');
    document.getElementById('confirm-box-title').textContent = opts.title || 'Confirm';
    document.getElementById('confirm-box-msg').textContent   = message;
    okBtn.textContent = opts.confirmLabel || 'OK';
    okBtn.classList.toggle('danger', !!opts.danger);
    let done = false;
    const close = (val) => {
      if (done) return; done = true;
      ov.classList.remove('open');
      okBtn.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
      ov.removeEventListener('mousedown', onBackdrop);
      document.removeEventListener('keydown', onKey, true);
      resolve(val);
    };
    const onOk      = () => close(true);
    const onCancel  = () => close(false);
    const onBackdrop = (e) => { if (e.target === ov) close(false); };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(false); }
      else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); close(true); }
    };
    okBtn.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
    ov.addEventListener('mousedown', onBackdrop);
    document.addEventListener('keydown', onKey, true);
    ov.classList.add('open');
    okBtn.focus();
  });
}

// In-app single-line text prompt (replaces native prompt()).
// Resolves to the trimmed string, or null if cancelled / left empty.
function _prompt(message, opts = {}) {
  return new Promise(resolve => {
    const ov = document.createElement('div');
    ov.className = 'prompt-overlay';
    ov.innerHTML = `
      <div class="prompt-box">
        <h3>${escHtml(opts.title || 'Enter a value')}</h3>
        ${message ? `<p>${escHtml(message)}</p>` : ''}
        <input type="text" class="prompt-input" placeholder="${escHtml(opts.placeholder || '')}"
               value="${escHtml(opts.value || '')}" maxlength="120">
        <div class="btn-row" style="max-width:260px;margin:12px auto 0">
          <button class="btn-secondary" data-act="cancel" style="width:auto">Cancel</button>
          <button class="btn-accent"    data-act="ok"     style="width:auto">${escHtml(opts.confirmLabel || 'OK')}</button>
        </div>
      </div>`;
    document.getElementById('app').appendChild(ov);
    const input = ov.querySelector('.prompt-input');
    let done = false;
    const close = (val) => {
      if (done) return; done = true;
      document.removeEventListener('keydown', onKey, true);
      ov.remove();
      resolve(val);
    };
    const submit = () => { const v = input.value.trim(); close(v || null); };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(null); }
      else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); submit(); }
    };
    ov.querySelector('[data-act="ok"]').addEventListener('click', submit);
    ov.querySelector('[data-act="cancel"]').addEventListener('click', () => close(null));
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) close(null); });
    document.addEventListener('keydown', onKey, true);
    requestAnimationFrame(() => ov.classList.add('open'));
    setTimeout(() => { input.focus(); input.select(); }, 40);
  });
}

// In-app toast notice (replaces native alert()).
function _toast(message, type = 'info') {
  const host = document.getElementById('toast-host');
  if (!host) return;
  // Sit above the mini-player dock (62px) while it's visible, else near the bottom
  const mpVisible = !document.getElementById('mini-player')?.classList.contains('hidden');
  host.style.bottom = mpVisible ? '78px' : '18px';
  const t = document.createElement('div');
  t.className = 'toast toast-' + type;
  t.textContent = message;
  host.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 250);
  }, 3400);
}

function flash(el) {
  el.classList.add('error');
  setTimeout(() => el.classList.remove('error'), 700);
}

// ═══════════════════════════════════════
//  TELEGRAM AUTH
// ═══════════════════════════════════════
 
function _tgSetStep(stepId) {
  ['tg-step-phone','tg-step-code','tg-step-pw'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('active', id === stepId);
  });
}
 
function _tgMsg(text, color) {
  const el = document.getElementById('tg-auth-msg');
  if (!el) return;
  el.textContent = text;
  el.style.color = color || '';
}
 
function _tgHandleStep(data) {
  const discBtn = document.getElementById('btn-tg-auth-disconnect');
  switch (data.step) {
    case 'done':
      _tgSetStep(null);
      _tgMsg(`Logged in as ${data.username}`, 'var(--accent)');
      updateTgDot(data);
      if (discBtn) discBtn.style.display = 'inline-block';
      break;
    case 'error':
      _tgSetStep(null);
      _tgMsg(`Error: ${data.error}`, 'var(--red)');
      updateTgDot(data);
      break;
    case 'need_phone':
      _tgMsg('Enter your phone number to receive a login code via Telegram.');
      _tgSetStep('tg-step-phone');
      if (discBtn) discBtn.style.display = 'none';
      setTimeout(() => document.getElementById('tg-phone-input').focus(), 60);
      break;
    case 'need_code':
      _tgMsg('A code has been sent to your Telegram app. Enter it below.');
      _tgSetStep('tg-step-code');
      setTimeout(() => document.getElementById('tg-code-input').focus(), 60);
      break;
    case 'need_password':
      _tgMsg('Two-factor authentication is enabled. Enter your password.');
      _tgSetStep('tg-step-pw');
      setTimeout(() => document.getElementById('tg-pw-input').focus(), 60);
      break;
    default:
      _tgMsg(`Status: ${data.step}…`);
  }
}
 
async function _tgPost(action, value) {
  const resp = await fetch('/telegram-auth', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ action, value }),
  });
  return resp.json();
}
 
function updateTgDot(data) {
  const dot = document.getElementById('tg-status-dot');
  const btn = document.getElementById('btn-tg-connect');
  if (!dot || !btn) return;
  if (data.step === 'done' || data.session_exists) {
    dot.className = 'connected'; dot.textContent = '●';
    btn.title = data.username ? `Telegram: ${data.username}` : 'Telegram: connected';
  } else if (data.step === 'error') {
    dot.className = 'error'; dot.textContent = '●';
    btn.title = `Telegram error: ${data.error || '?'}`;
  } else {
    dot.className = ''; dot.textContent = '○';
    btn.title = 'Telegram: not connected — click to log in';
  }
}
 
async function checkTelegramStatus() {
  try {
    const data = await (await fetch('/telegram-status')).json();
    updateTgDot(data);
  } catch (_) {}
}
 
async function openTgAuth() {
  document.getElementById('tg-auth-overlay').classList.add('open');
  _tgSetStep(null);
  _tgMsg('Checking Telegram session…');
  try {
    const data = await _tgPost('start', '');
    _tgHandleStep(data);
  } catch(e) {
    _tgMsg(`Error: ${e}`, 'var(--red)');
  }
}
 
// Wire up auth modal buttons
document.getElementById('btn-tg-connect').addEventListener('click', openTgAuth);
document.getElementById('btn-tg-auth-cancel').addEventListener('click', () => {
  document.getElementById('tg-auth-overlay').classList.remove('open');
});
document.getElementById('btn-tg-auth-disconnect').addEventListener('click', () => {
  showDeleteConfirm({
    title: 'Disconnect Telegram',
    msg:   'Your Telegram session file will be deleted. You will need to log in again to download.',
    detail: 'tg_audio_session.session',
    onConfirm: async () => {
      try {
        const d = await (await fetch('/telegram-auth', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'disconnect' }),
        })).json();
        if (d.ok) {
          document.getElementById('tg-auth-overlay').classList.remove('open');
          updateTgDot({ step: 'idle', session_exists: false });
          appendLog('  Telegram disconnected.\n', 'log-warn');
        } else {
          appendLog(`  Disconnect error: ${d.error}\n`, 'log-error');
        }
      } catch(e) { appendLog(`  Disconnect failed: ${e}\n`, 'log-error'); }
    }
  });
});
 
document.getElementById('btn-tg-send-phone').addEventListener('click', async () => {
  const phone = document.getElementById('tg-phone-input').value.trim();
  if (!phone) { flash(document.getElementById('tg-phone-input')); return; }
  _tgSetStep(null); _tgMsg('Sending code — this can take a few seconds…');
  try { _tgHandleStep(await _tgPost('submit_phone', phone)); }
  catch(e) { _tgMsg(`Error: ${e}`, 'var(--red)'); _tgSetStep('tg-step-phone'); }
});
 
document.getElementById('btn-tg-send-code').addEventListener('click', async () => {
  const code = document.getElementById('tg-code-input').value.trim();
  if (!code) { flash(document.getElementById('tg-code-input')); return; }
  _tgSetStep(null); _tgMsg('Verifying code…');
  try { _tgHandleStep(await _tgPost('submit_code', code)); }
  catch(e) { _tgMsg(`Error: ${e}`, 'var(--red)'); _tgSetStep('tg-step-code'); }
});
 
document.getElementById('btn-tg-send-pw').addEventListener('click', async () => {
  const pw = document.getElementById('tg-pw-input').value;
  if (!pw) { flash(document.getElementById('tg-pw-input')); return; }
  _tgSetStep(null); _tgMsg('Verifying password…');
  try { _tgHandleStep(await _tgPost('submit_password', pw)); }
  catch(e) { _tgMsg(`Error: ${e}`, 'var(--red)'); _tgSetStep('tg-step-pw'); }
});
 
// Enter-key support inside auth inputs
{
  const map = {
    'tg-phone-input': 'btn-tg-send-phone',
    'tg-code-input':  'btn-tg-send-code',
    'tg-pw-input':    'btn-tg-send-pw',
  };
  Object.entries(map).forEach(([inputId, btnId]) => {
    document.getElementById(inputId)?.addEventListener('keydown', e => {
      if (e.key === 'Enter') document.getElementById(btnId)?.click();
    });
  });
}

// ═══════════════════════════════════════
//  EVENT WIRING
// ═══════════════════════════════════════

document.getElementById('btn-add').addEventListener('click', addEntry);
document.getElementById('url-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') addEntry();
});

// Recognise the pasted link's source and reflect it in the URL box
function _updateUrlSource() {
  const el    = document.getElementById('url-input');
  const badge = document.getElementById('url-source-badge');
  if (!el || !badge) return;
  const v = el.value.trim();
  let kind = '';
  if (/spotify\.com/.test(v))                         kind = 'spotify';
  else if (/deezer\.com|link\.deezer\.com/.test(v))   kind = 'deezer';
  badge.className = kind;
  badge.textContent = kind === 'spotify' ? 'Spotify' : kind === 'deezer' ? 'Deezer' : '';
  el.classList.toggle('is-spotify', kind === 'spotify');
  el.classList.toggle('is-deezer',  kind === 'deezer');
}
document.getElementById('url-input').addEventListener('input', _updateUrlSource);

// ── Bulk mode toggle ────────────────────────────────────────────────────────
{
  let _bulkMode = false;
  document.getElementById('btn-toggle-bulk').addEventListener('click', () => {
    _bulkMode = !_bulkMode;
    document.getElementById('add-single-mode').style.display = _bulkMode ? 'none' : '';
    document.getElementById('add-bulk-mode').style.display   = _bulkMode ? '' : 'none';
    const btn = document.getElementById('btn-toggle-bulk');
    _setBtnIco(btn, _bulkMode ? 'close' : 'layers', _bulkMode ? 'SINGLE' : 'BULK');
    btn.style.color = _bulkMode ? 'var(--accent)' : '';
    if (_bulkMode) document.getElementById('bulk-url-input').focus();
  });
  // Live URL count as user types
  document.getElementById('bulk-url-input').addEventListener('input', () => {
    const lines = document.getElementById('bulk-url-input').value
      .split('\n').map(l => l.trim()).filter(Boolean);
    const lbl = document.getElementById('bulk-count-label');
    lbl.textContent = lines.length ? `${lines.length} URL${lines.length !== 1 ? 's' : ''}` : '';
  });
  document.getElementById('btn-bulk-add').addEventListener('click', addBulkEntries);
  document.getElementById('bulk-url-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); addBulkEntries(); }
  });
}

document.getElementById('btn-browse').addEventListener('click', async () => {
  const current = document.getElementById('home-input').value.trim();
  try {
    const resp = await fetch('/browse-folder', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ current }),
    });
    const data = await resp.json();
    if (data.path) {
      document.getElementById('home-input').value = data.path;
      // Immediately persist the picked folder
      fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ home_music_folder: data.path }) });
    }
  } catch (_) {
    // Server unreachable — fall back to text prompt
    const chosen = prompt('Paste the full path to your home music folder:', current);
    if (chosen !== null && chosen.trim()) {
      document.getElementById('home-input').value = chosen.trim();
      fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ home_music_folder: chosen.trim() }) });
    }
  }
});
// Auto-save home music folder on blur so it persists without clicking Run
document.getElementById('home-input').addEventListener('blur', () => {
  const val = document.getElementById('home-input').value.trim();
  if (val) {
    fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ home_music_folder: val }) });
  }
});


['list','grid','group'].forEach(mode => {
  document.getElementById(`vbtn-${mode}`).addEventListener('click', () => {
    viewMode = mode;
    document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(`vbtn-${mode}`).classList.add('active');
    renderQueue();
  });
});

document.getElementById('btn-copy-all').addEventListener('click', copyAllUrls);
document.getElementById('btn-copy-all-full').addEventListener('click', copyAllUrls);
document.getElementById('btn-export-m3u').addEventListener('click', exportM3U);

document.getElementById('btn-open-sessions').addEventListener('click', openSessionsModal);
document.getElementById('btn-sessions-save-cur').addEventListener('click', saveSession);
document.getElementById('btn-sessions-close').addEventListener('click', () =>
  document.getElementById('sessions-overlay').classList.remove('open'));
document.getElementById('sessions-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('sessions-overlay'))
    document.getElementById('sessions-overlay').classList.remove('open');
});

document.getElementById('btn-run').addEventListener('click', runOrStop);
document.getElementById('btn-retry-failed')?.addEventListener('click', retryAllFailed);

{
  let _clearTimer = null;
  document.getElementById('btn-clear-queue').addEventListener('click', () => {
    const btn = document.getElementById('btn-clear-queue');
    if (!entries.length) return;
    if (!btn.classList.contains('confirming')) {
      btn.classList.add('confirming');
      btn.textContent = 'Confirm?';
      _clearTimer = setTimeout(() => {
        btn.classList.remove('confirming');
        btn.textContent = 'Clear Queue';
      }, 2000);
      return;
    }
    clearTimeout(_clearTimer);
    btn.classList.remove('confirming');
    btn.textContent = 'Clear Queue';
    entries = []; sessionResults = {};
    renderQueue();
  });
}

document.getElementById('btn-settings').addEventListener('click', openSettings);
document.getElementById('btn-modal-save').addEventListener('click', saveSettings);
document.getElementById('cfg-scrobble-service').addEventListener('change', _syncScrobbleService);
// Settings category nav
document.querySelectorAll('.settings-nav-item').forEach(btn =>
  btn.addEventListener('click', () => _settingsShowPanel(btn.dataset.spanel)));
document.getElementById('btn-about-check-update')?.addEventListener('click', () => _checkForUpdate(true));
document.getElementById('btn-about-download')?.addEventListener('click', _downloadUpdate);
// Lazy arrows, not bare identifiers: these three live in 04-library.js, which
// loads AFTER this file — a direct reference here is evaluated at load time,
// throws ReferenceError, and silently kills every listener wired below
// (including tab switching). Regression shipped in the v1.4.0 split.
document.getElementById('btn-run-health')?.addEventListener('click', () => runDiagnostics());
document.getElementById('btn-run-scan')?.addEventListener('click', () => runIntegrityScan());
document.getElementById('btn-find-dupes')?.addEventListener('click', () => findDuplicates());
document.getElementById('btn-backup').addEventListener('click', () => {
  const a = document.createElement('a');
  a.href = '/backup'; a.download = 'tgdownloader-backup.zip';
  document.body.appendChild(a); a.click(); a.remove();
});
document.getElementById('btn-debug-bundle')?.addEventListener('click', () => {
  const a = document.createElement('a');
  a.href = '/debug-bundle'; a.download = 'tgdownloader-debug.zip';
  document.body.appendChild(a); a.click(); a.remove();
});
document.getElementById('btn-restore')?.addEventListener('click', () =>
  document.getElementById('restore-file').click());
document.getElementById('restore-file')?.addEventListener('change', async e => {
  const f = e.target.files && e.target.files[0];
  e.target.value = '';
  if (!f) return;
  const out = document.getElementById('restore-results');
  out.innerHTML = '<div style="font-size:11px;color:var(--fg3)">Restoring…</div>';
  try {
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = '';
    for (let i = 0; i < buf.length; i += 0x8000)
      bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    const d = await (await fetch('/restore-backup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ zip_b64: btoa(bin) }),
    })).json();
    if (d.error) {
      out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(d.error)}</div>`;
      return;
    }
    const skippedNote = (d.skipped || []).length
      ? ` · ${d.skipped.length} unknown entr${d.skipped.length === 1 ? 'y' : 'ies'} skipped` : '';
    out.innerHTML = `<div style="font-size:11px;color:var(--accent)">Restored: ${escHtml((d.restored || []).join(', ') || 'nothing')}${skippedNote}. Reload the app to see restored data.</div>`;
  } catch (err) {
    out.innerHTML = `<div style="font-size:11px;color:var(--red)">Restore failed: ${escHtml(String(err))}</div>`;
  }
});
document.getElementById('btn-art-repair')?.addEventListener('click', async () => {
  const out = document.getElementById('art-repair-results');
  const btn = document.getElementById('btn-art-repair');
  btn.disabled = true;
  out.innerHTML = '<div style="font-size:11px;color:var(--fg3)">Scanning albums and fetching covers…</div>';
  try {
    const d = await (await fetch('/art-repair', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    })).json();
    if (d.error) {
      out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(d.error)}</div>`;
    } else {
      out.innerHTML = `<div style="font-size:11px">Checked <strong>${d.checked}</strong> folders · missing <strong>${d.missing}</strong> · fixed <strong style="color:var(--accent)">${d.fixed}</strong>${d.failed ? ` · failed <strong style="color:var(--red)">${d.failed}</strong>` : ''}${d.remaining > 0 ? ` · ${d.remaining} left — run again` : ''}</div>`;
    }
  } catch (err) {
    out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(String(err))}</div>`;
  }
  btn.disabled = false;
});
document.getElementById('btn-loudness')?.addEventListener('click', async () => {
  const out = document.getElementById('loudness-results');
  const btn = document.getElementById('btn-loudness');
  btn.disabled = true;
  out.innerHTML = '<div style="font-size:11px;color:var(--fg3)">Analyzing loudness — this decodes each file, please wait…</div>';
  try {
    const d = await (await fetch('/loudness-scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    })).json();
    if (d.error) {
      out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(d.error)}</div>`;
    } else {
      out.innerHTML = `<div style="font-size:11px">Checked <strong>${d.checked}</strong> tracks · untagged <strong>${d.missing}</strong> · tagged <strong style="color:var(--accent)">${d.tagged}</strong>${d.failed ? ` · failed <strong style="color:var(--red)">${d.failed}</strong>` : ''}${d.remaining > 0 ? ` · ${d.remaining} left — run again` : ''}</div>`;
    }
  } catch (err) {
    out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(String(err))}</div>`;
  }
  btn.disabled = false;
});
// Tempo Analysis — Deezer BPM lookup feeding smart-playlist tempo rules (v1.11.0)
document.getElementById('btn-bpm-scan')?.addEventListener('click', async () => {
  const out = document.getElementById('bpm-scan-results');
  const btn = document.getElementById('btn-bpm-scan');
  btn.disabled = true;
  out.innerHTML = '<div style="font-size:11px;color:var(--fg3)">Looking up tempo from Deezer — please wait…</div>';
  try {
    const d = await (await fetch('/bpm-scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    })).json();
    if (d.error) {
      out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(d.error)}</div>`;
    } else {
      out.innerHTML = `<div style="font-size:11px">Analyzed <strong>${d.processed}</strong> this run · matched <strong style="color:var(--accent)">${d.matched}</strong> · tempo known for <strong>${d.with_bpm}</strong>/<strong>${d.total}</strong>${d.remaining > 0 ? ` · ${d.remaining} left — run again` : ''}</div>`;
    }
  } catch (err) {
    out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(String(err))}</div>`;
  }
  btn.disabled = false;
});
// ── Library Intelligence (v1.6.0) ────────────────────────────────────────────
function _libIntelOut(html) {
  const el = document.getElementById('libintel-results');
  if (el) el.innerHTML = html;
}
async function _libIntelRun(btnId, url, body, render) {
  const btn = document.getElementById(btnId);
  const prev = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; }
  _libIntelOut('<div style="font-size:11px;color:var(--fg3)">Working… this scans your library, please wait.</div>');
  try {
    const d = await (await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    })).json();
    if (d.error) { _libIntelOut(`<div style="font-size:11px;color:var(--red)">${escHtml(d.error)}</div>`); return null; }
    if (render) _libIntelOut(render(d));
    return d;
  } catch (err) {
    _libIntelOut(`<div style="font-size:11px;color:var(--red)">${escHtml(String(err))}</div>`);
    return null;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = prev; }
  }
}

function _tagFillYear() {
  return !!document.getElementById('tag-fill-year')?.checked;
}
document.getElementById('btn-tag-preview')?.addEventListener('click', () =>
  _libIntelRun('btn-tag-preview', '/tag-janitor', { apply: false, fill_year: _tagFillYear() }, d => {
    const applyBtn = document.getElementById('btn-tag-apply');
    if (applyBtn) applyBtn.style.display = d.count > 0 ? '' : 'none';
    if (!d.count) return `<div style="font-size:11px;color:var(--accent)">✓ No tag issues found (scanned ${d.scanned}).</div>`;
    const rows = d.changes.filter(c => c.fixes).slice(0, 40).map(c =>
      `<div style="font-size:10px;font-family:var(--mono);padding:2px 0">
         <span style="color:var(--fg2)">${escHtml(c.file)}</span> →
         ${Object.entries(c.fixes).map(([k, v]) => `${k}: <span style="color:var(--accent)">${escHtml(String(v))}</span>`).join(', ')}
       </div>`).join('');
    const yearNote = d.year_filled
      ? ` (${d.year_filled} year${d.year_filled !== 1 ? 's' : ''} from Deezer${d.year_lookups_capped ? ' — capped, run again for more' : ''})` : '';
    return `<div style="font-size:11px;margin-bottom:4px">${d.count} file(s) with fixable tags${yearNote} (showing up to 40). Click <strong>Apply fixes</strong> to write them.</div>${rows}`;
  }));
document.getElementById('btn-tag-apply')?.addEventListener('click', async () => {
  const d = await _libIntelRun('btn-tag-apply', '/tag-janitor', { apply: true, fill_year: _tagFillYear() }, d =>
    `<div style="font-size:11px;color:var(--accent)">Applied fixes to ${d.count} file(s)${d.year_filled ? ` — ${d.year_filled} release year(s) filled` : ''}.</div>`);
  if (d) document.getElementById('btn-tag-apply').style.display = 'none';
});
document.getElementById('btn-completeness')?.addEventListener('click', () =>
  _libIntelRun('btn-completeness', '/album-completeness', {}, d => {
    if (!d.incomplete) return `<div style="font-size:11px;color:var(--accent)">✓ No incomplete albums found (checked ${d.checked}).</div>`;
    const rows = d.albums.slice(0, 40).map(a =>
      `<div style="font-size:10px;font-family:var(--mono);padding:2px 0">
         ${escHtml(a.artist)} — ${escHtml(a.album)}
         <strong style="color:var(--yellow)">${a.have}/${a.total}</strong></div>`).join('');
    return `<div style="font-size:11px;margin-bottom:4px">${d.incomplete} album(s) look incomplete vs Deezer:</div>${rows}`;
  }));
document.getElementById('btn-corruption')?.addEventListener('click', () =>
  _libIntelRun('btn-corruption', '/corruption-scan', {}, d => {
    if (!d.count) return `<div style="font-size:11px;color:var(--accent)">✓ No corrupt files (decode-tested ${d.scanned}).</div>`;
    const rows = d.corrupt.slice(0, 40).map(f =>
      `<div style="font-size:10px;font-family:var(--mono);color:var(--red);padding:1px 0">${escHtml(f)}</div>`).join('');
    return `<div style="font-size:11px;margin-bottom:4px">${d.count} file(s) failed to decode:</div>${rows}`;
  }));
document.getElementById('btn-fingerprint')?.addEventListener('click', () =>
  _libIntelRun('btn-fingerprint', '/fingerprint-scan', {}, d =>
    `<div style="font-size:11px">Scanned <strong>${d.scanned}</strong> untagged · identified <strong style="color:var(--accent)">${d.identified}</strong>${d.failed ? ` · unresolved <strong>${d.failed}</strong>` : ''}</div>` +
    (d.updates || []).slice(0, 30).map(u =>
      `<div style="font-size:10px;font-family:var(--mono);padding:1px 0">${escHtml(u.artist)} — ${escHtml(u.title)}</div>`).join('')));

// ── Import folder ─────────────────────────────────────────────────────────────
async function _runImport(path) {
  const out = document.getElementById('import-results');
  out.innerHTML = '<div style="font-size:11px;color:var(--fg3)">Importing and sorting…</div>';
  try {
    const d = await (await fetch('/import-folder', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path || '' }),
    })).json();
    if (d.cancelled) { out.innerHTML = ''; return; }
    if (d.error) { out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(d.error)}</div>`; return; }
    out.innerHTML = `<div style="font-size:11px;color:var(--accent)">Imported ${d.imported} file(s)${d.dupes ? ` · ${d.dupes} duplicate(s) skipped` : ''}.</div>`;
  } catch (err) {
    out.innerHTML = `<div style="font-size:11px;color:var(--red)">${escHtml(String(err))}</div>`;
  }
}
document.getElementById('btn-import-folder')?.addEventListener('click', () => _runImport(''));
(function _wireDropzone() {
  const dz = document.getElementById('import-dropzone');
  if (!dz) return;
  ['dragover', 'dragenter'].forEach(ev => dz.addEventListener(ev, e => {
    e.preventDefault(); dz.style.borderColor = 'var(--accent)';
  }));
  ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, () => {
    dz.style.borderColor = 'var(--border2)';
  }));
  dz.addEventListener('drop', async e => {
    e.preventDefault();
    // Browsers don't expose real filesystem paths for security. If the drop
    // carries a path (Electron-style) use it; otherwise fall back to the picker.
    const item = e.dataTransfer?.files?.[0];
    const p = item && item.path ? item.path : '';
    _runImport(p);
  });
})();

document.getElementById('btn-modal-cancel').addEventListener('click', () =>
  document.getElementById('modal-overlay').classList.remove('open'));
document.getElementById('modal-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('modal-overlay')) closeAllModals();
});

document.getElementById('log-clear').addEventListener('click', async () => {
  if (activeTab === 'log') {
    clearLog(); showWelcome();
  } else if (activeTab === 'history') {
    if (!await _confirm('Clear ALL download history? This removes manifest entries but does NOT delete files on disk.', { title: 'Clear history', confirmLabel: 'Clear', danger: true })) return;
    for (const d of historyData) {
      await fetch('/history-remove', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: d.url }),
      });
    }
    historyData = [];
    applyHistoryFilter();
    speedHistory = [];
    localStorage.removeItem('tgd_speed_hist');
    avgSpeedMBs = null;
    sessionResults = {};
    currentProgress = null;
    manifestUrls.clear();
    if (activeTab === 'stats') renderStats();
  }
});

document.querySelectorAll('.tab').forEach(btn =>
  btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

document.getElementById('history-filter').addEventListener('input', applyHistoryFilter);
document.getElementById('btn-filter-clear').addEventListener('click', () => {
  document.getElementById('history-filter').value = ''; applyHistoryFilter();
});



['fav','rev','note'].forEach(tag => {
  const btn = document.getElementById(`tf-${tag}`);
  btn.addEventListener('click', () => {
    const wasActive = histTagFilter === tag;
    histTagFilter = wasActive ? '' : tag;
    document.querySelectorAll('.tag-filter-btn').forEach(b => {
      b.classList.remove('tf-active-fav','tf-active-rev','tf-active-note');
    });
    if (!wasActive) btn.classList.add(`tf-active-${tag}`);
    applyHistoryFilter();
  });
});

document.getElementById('btn-deezer-search').addEventListener('click', openSearch);
document.getElementById('btn-search-go').addEventListener('click', () => doSearch(true));
document.getElementById('btn-search-cancel').addEventListener('click', closeSearch);

let _searchDebounce = null;
document.getElementById('search-input').addEventListener('input', () => {
  clearTimeout(_searchDebounce);
  const q = document.getElementById('search-input').value.trim();
  if (!q) {
    document.getElementById('search-results').innerHTML =
      '<div class="search-status">Type an artist or album and press Search.</div>';
    return;
  }
  _searchDebounce = setTimeout(() => doSearch(false), 420);
});
document.getElementById('search-input').addEventListener('keydown', e => {
  // Arrow keys navigate the album cards; j/k only when input is empty
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    _searchResultsNav(1);
    return;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    _searchResultsNav(-1);
    return;
  }
  // Enter: if a card is focused, add it to queue. Otherwise run search.
  if (e.key === 'Enter') {
    e.preventDefault();
    if (_searchFocusIdx >= 0) {
      _searchResultsActivate();
    } else {
      clearTimeout(_searchDebounce);
      doSearch(true);
    }
    return;
  }
  if (e.key === 'Escape') closeSearch();
});
document.getElementById('search-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('search-overlay')) closeSearch();
});

document.getElementById('sh-clear-all').addEventListener('click', clearSearchHistory);

// Context menu
document.getElementById('ctx-copy').addEventListener('click', () => {
  if (ctxIdx === null) return;
  navigator.clipboard.writeText(entries[ctxIdx].url).catch(() => {});
  closeCtxMenu();
});
document.getElementById('ctx-top').addEventListener('click', () => {
  if (ctxIdx === null) return;
  const [e] = entries.splice(ctxIdx, 1);
  entries.unshift(e);
  closeCtxMenu(); renderQueue();
});
document.getElementById('ctx-bottom').addEventListener('click', () => {
  if (ctxIdx === null) return;
  const [e] = entries.splice(ctxIdx, 1);
  entries.push(e);
  closeCtxMenu(); renderQueue();
});
document.getElementById('ctx-fav').addEventListener('click', () => {
  if (ctxIdx === null) return;
  const meta = getEntryMeta(entries[ctxIdx].url);
  if (meta.tags.includes('fav')) meta.tags = meta.tags.filter(t => t !== 'fav');
  else meta.tags.push('fav');
  saveEntryMeta();
  closeCtxMenu(); renderQueue();
});
document.getElementById('ctx-rev').addEventListener('click', () => {
  if (ctxIdx === null) return;
  const meta = getEntryMeta(entries[ctxIdx].url);
  if (meta.tags.includes('rev')) meta.tags = meta.tags.filter(t => t !== 'rev');
  else meta.tags.push('rev');
  saveEntryMeta();
  closeCtxMenu(); renderQueue();
});
document.getElementById('ctx-note').addEventListener('click', () => {
  if (ctxIdx === null) return;
  const idx = ctxIdx;
  closeCtxMenu();
  openNoteEditor(idx);
});
document.getElementById('ctx-remove').addEventListener('click', () => {
  if (ctxIdx === null) return;
  entries.splice(ctxIdx, 1);
  closeCtxMenu(); renderQueue();
});

document.addEventListener('click', (e) => {
  if (!document.getElementById('ctx-menu').contains(e.target)) closeCtxMenu();
});
document.getElementById('queue-wrap').addEventListener('scroll', closeCtxMenu);

// Note editor
document.getElementById('btn-note-save').addEventListener('click', saveNote);
document.getElementById('btn-note-cancel').addEventListener('click', () => {
  document.getElementById('note-overlay').classList.remove('open'); noteIdx = null;
});
document.getElementById('note-overlay').addEventListener('click', (e) => {
  if (e.target === document.getElementById('note-overlay')) {
    document.getElementById('note-overlay').classList.remove('open'); noteIdx = null;
  }
});
document.getElementById('note-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.ctrlKey) saveNote();
  if (e.key === 'Escape') {
    document.getElementById('note-overlay').classList.remove('open'); noteIdx = null;
  }
});

// Quit
document.getElementById('btn-quit').addEventListener('click', openQuitDialog);
document.getElementById('btn-quit-cancel').addEventListener('click', () => {
  document.getElementById('quit-overlay').classList.remove('open');
});
document.getElementById('btn-quit-confirm').addEventListener('click', confirmQuit);
document.getElementById('quit-overlay').addEventListener('click', (e) => {
  if (e.target === document.getElementById('quit-overlay'))
    document.getElementById('quit-overlay').classList.remove('open');
});

// Delete confirm dialog
document.getElementById('btn-delete-cancel').addEventListener('click', closeDeleteConfirm);
document.getElementById('btn-delete-confirm').addEventListener('click', () => {
  const cb = _deleteCallback;
  closeDeleteConfirm();
  if (cb) cb();
});
document.getElementById('delete-overlay').addEventListener('click', (e) => {
  if (e.target === document.getElementById('delete-overlay')) closeDeleteConfirm();
});
document.getElementById('delete-overlay').addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeDeleteConfirm();
  if (e.key === 'Enter')  document.getElementById('btn-delete-confirm').click();
});

// Load-session confirm overlay
document.getElementById('btn-lsc-cancel').addEventListener('click', closeLoadSessionConfirm);
document.getElementById('btn-lsc-confirm').addEventListener('click', () => {
  const cb = _loadSessionCallback;
  closeLoadSessionConfirm();
  if (cb) cb();
});
document.getElementById('load-session-overlay').addEventListener('click', (e) => {
  if (e.target === document.getElementById('load-session-overlay')) closeLoadSessionConfirm();
});
document.getElementById('load-session-overlay').addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeLoadSessionConfirm();
  if (e.key === 'Enter')  document.getElementById('btn-lsc-confirm').click();
});


// ═══════════════════════════════════════
//  AUDIO QUALITY
// ═══════════════════════════════════════
// Quality labels as displayed in the bot's buttons
const QUALITY_OPTIONS = [
  { key: 'FLAC',    label: 'FLAC',       btn_text: 'FLAC' },
  { key: 'MP3 320', label: 'MP3 320kbps', btn_text: 'MP3 320' },
  { key: 'MP3 128', label: 'MP3 128kbps', btn_text: 'MP3 128' },
];

// Persisted quality (unknown until we query the bot)
let _currentQuality = localStorage.getItem('tgd_quality') || null;

function _qualityUpdateBadge(q) {
  const badge = document.getElementById('quality-badge');
  if (!badge) return;
  if (q) {
    const label = q === 'FLAC' ? 'FLAC' : q === 'MP3 320' ? '320' : '128';
    badge.innerHTML = ICON.note + `<span>${label}</span>`;
    badge.classList.remove('unknown');
    badge.title = `Audio quality: ${q} — click to change`;
  } else {
    badge.innerHTML = ICON.note + '<span>?</span>';
    badge.classList.add('unknown');
    badge.title = 'Audio quality unknown — click to check/change';
  }
}

function _qualitySetActive(q) {
  document.querySelectorAll('.quality-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.quality === q);
  });
}

async function openQualityModal() {
  const overlay = document.getElementById('quality-overlay');
  const msgEl   = document.getElementById('quality-modal-msg');
  overlay.classList.add('open');

  // Load quality from local config (no bot comms)
  try {
    const data = await (await fetch('/telegram-quality')).json();
    if (data.quality) {
      _currentQuality = data.quality;
      localStorage.setItem('tgd_quality', _currentQuality);
      _qualityUpdateBadge(_currentQuality);
    }
  } catch (_) {}

  _qualitySetActive(_currentQuality);
  msgEl.textContent = 'Select your target audio format. Files are converted locally by ffmpeg after download.';
  msgEl.style.color = '';
  document.querySelectorAll('.quality-btn').forEach(b => { b.disabled = false; b.style.opacity = ''; b.style.cursor = ''; });
}



async function _qualitySet(q) {
  const msgEl = document.getElementById('quality-modal-msg');
  if (q === _currentQuality) {
    msgEl.textContent = `Already set to ${q}.`;
    return;
  }
  msgEl.textContent = `Switching to ${q}…`;
  msgEl.style.color = '';
  document.querySelectorAll('.quality-btn').forEach(b => { b.disabled = true; b.style.opacity = '0.5'; });

  try {
    const resp = await fetch('/telegram-quality', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ quality: q }),
    });
    const data = await resp.json();
    if (data.ok || data.quality) {
      _currentQuality = q;
      localStorage.setItem('tgd_quality', q);
      _qualityUpdateBadge(q);
      _qualitySetActive(q);
      msgEl.textContent = `Quality set to ${q}.`;
      msgEl.style.color = 'var(--accent)';
      appendLog(`  Audio quality set to ${q}\n`, 'log-success');
    } else {
      msgEl.textContent = `Error: ${data.error || 'Could not set quality.'}`;
      msgEl.style.color = 'var(--red)';
    }
  } catch (err) {
    msgEl.textContent = `Error: ${err}`;
    msgEl.style.color = 'var(--red)';
  }
  document.querySelectorAll('.quality-btn').forEach(b => { b.disabled = false; b.style.opacity = ''; });
}

// Wire quality buttons
document.querySelectorAll('.quality-btn').forEach(btn => {
  btn.addEventListener('click', () => _qualitySet(btn.dataset.quality));
});
document.getElementById('quality-badge')?.addEventListener('click', openQualityModal);
document.getElementById('btn-quality-cancel')?.addEventListener('click', () =>
  document.getElementById('quality-overlay').classList.remove('open'));
document.getElementById('quality-overlay')?.addEventListener('click', e => {
  if (e.target === document.getElementById('quality-overlay'))
    document.getElementById('quality-overlay').classList.remove('open');
});

// Init quality badge from persisted value
_qualityUpdateBadge(_currentQuality);



function showWelcome() {
  if (!showWelcomeOnStart) return;

  const log = document.getElementById('log');
  log.innerHTML = '';

  // Title box — uses CSS vars so it matches whatever theme
  const box = document.createElement('div');
  box.style.cssText = `
    margin: 12px 0 16px 0;
    border: 1px solid var(--border2);
    border-radius: 3px; padding: 10px 20px;
    display: inline-block; min-width: 320px;
    background: var(--bg3);
  `;
  const title = document.createElement('div');
  title.style.cssText = `
    font-family: var(--mono); font-size: 12px; color: var(--accent);
    font-weight: 600; letter-spacing: 0.18em; text-align: center;
  `;
  title.textContent = 'TGDOWNLOADER';
  box.appendChild(title);
  log.appendChild(box);

  const lines = [
    { text: '\n' },
    { text: '  Ready to download.\n',   cls: 'welcome-ready' },
    { text: '\n' },
    { text: '  HOW TO USE\n',           cls: 'welcome-head' },
    { text: '  ─────────────────────────────────────────────────\n', cls: 'welcome-dim' },
    { text: '  1. Connect Telegram — TG button, top-right (required)\n', cls: 'welcome-body' },
    { text: '  2. Set your home music folder (left panel)\n', cls: 'welcome-body' },
    { text: '  3. Search or paste album URLs into the queue\n', cls: 'welcome-body' },
    { text: '  4. Press Run  (or Ctrl+Enter)\n', cls: 'welcome-body' },
    { text: '\n' },
    { text: '  Press Ctrl+/ for keyboard shortcuts.\n', cls: 'welcome-dim' },
    { text: '\n' },
  ];

  // All colors come from CSS variables — no hardcoded hex
  const colorMap = {
    'welcome-ready': 'var(--accent)',
    'welcome-head':  'var(--fg)',
    'welcome-dim':   'var(--fg3)',
    'welcome-body':  'var(--fg2)',
    'welcome-warn':  'var(--yellow)',
  };

  lines.forEach(({ text, cls }) => {
    const span = document.createElement('span');
    span.textContent = text;
    if (cls) span.style.color = colorMap[cls] || '';
    log.appendChild(span);
  });
}


// ═══════════════════════════════════════
