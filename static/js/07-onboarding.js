// ═══════════════════════════════════════
//  ONBOARDING TOUR  (v1.10.0)
//  A first-run guided spotlight over the core workflow. Self-contained and
//  loaded last, so it may safely reference anything from earlier modules and
//  nothing references it (no load-order landmines — see the v1.9.1 fix).
// ═══════════════════════════════════════
(function () {
  const SEEN_KEY = 'tgd_onboarded';

  // Each step spotlights one element. `placement` is the preferred tooltip side.
  const STEPS = [
    { sel: '#btn-tg-connect', title: 'Connect Telegram',
      text: 'Start here. Click TG to sign in to Telegram and pick your music bot — nothing downloads until this is connected.',
      placement: 'bottom' },
    { sel: '#home-input', title: 'Choose your music folder',
      text: 'Point this at where your library should live. Downloads are sorted into Artists/ and Playlists/ here.',
      placement: 'bottom' },
    { sel: '#url-input', title: 'Add music',
      text: 'Paste a Deezer or Spotify album, track or playlist link and hit Add — or use Search to find one.',
      placement: 'bottom' },
    { sel: '#btn-run', title: 'Download the queue',
      text: 'Press Run (or Ctrl+Enter) to start. Progress streams in the Log tab. You can also schedule a run for later.',
      placement: 'left' },
    { sel: '.tab[data-tab="library"]', title: 'Play your library',
      text: 'Everything you download lands here — a full player with an equalizer, playlists, ratings and listening stats.',
      placement: 'bottom' },
  ];

  let idx = 0;
  let steps = [];   // resolved (existing) steps for this run

  function overlay() { return document.getElementById('tour-overlay'); }

  function build() {
    if (document.getElementById('tour-overlay')) return;
    const el = document.createElement('div');
    el.id = 'tour-overlay';
    el.innerHTML = `
      <div id="tour-spot"></div>
      <div id="tour-tip">
        <div id="tour-tip-title"></div>
        <div id="tour-tip-text"></div>
        <div id="tour-tip-foot">
          <span id="tour-progress"></span>
          <div class="tour-btns">
            <button id="tour-skip" class="tour-btn-ghost">Skip</button>
            <button id="tour-back" class="tour-btn-ghost">Back</button>
            <button id="tour-next" class="tour-btn-go">Next</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(el);
    document.getElementById('tour-skip').addEventListener('click', end);
    document.getElementById('tour-back').addEventListener('click', () => go(idx - 1));
    document.getElementById('tour-next').addEventListener('click', () => {
      if (idx >= steps.length - 1) end(); else go(idx + 1);
    });
    // Clicking the dimmed backdrop (not the tip) does nothing but is captured,
    // so the app stays inert during the tour. Esc skips.
    el.addEventListener('click', (e) => { if (e.target === el) { /* swallow */ } });
    document.addEventListener('keydown', _onKey, true);
  }

  function _onKey(e) {
    if (!overlay()) return;
    if (e.key === 'Escape') { e.preventDefault(); end(); }
    else if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); if (idx >= steps.length - 1) end(); else go(idx + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(idx - 1); }
  }

  function position() {
    const step = steps[idx];
    const target = document.querySelector(step.sel);
    const spot = document.getElementById('tour-spot');
    const tip = document.getElementById('tour-tip');
    if (!target || !spot || !tip) return;
    const r = target.getBoundingClientRect();
    const pad = 6;
    spot.style.left = (r.left - pad) + 'px';
    spot.style.top = (r.top - pad) + 'px';
    spot.style.width = (r.width + pad * 2) + 'px';
    spot.style.height = (r.height + pad * 2) + 'px';

    // Place the tip on the preferred side, flipping/clamping to stay on-screen.
    const tr = tip.getBoundingClientRect();
    const gap = 12;
    let place = step.placement || 'bottom';
    if (place === 'bottom' && r.bottom + gap + tr.height > innerHeight) place = 'top';
    if (place === 'top' && r.top - gap - tr.height < 0) place = 'bottom';
    let left, top;
    if (place === 'left') {
      left = r.left - gap - tr.width; top = r.top;
    } else if (place === 'top') {
      left = r.left; top = r.top - gap - tr.height;
    } else { // bottom
      left = r.left; top = r.bottom + gap;
    }
    left = Math.max(12, Math.min(left, innerWidth - tr.width - 12));
    top = Math.max(12, Math.min(top, innerHeight - tr.height - 12));
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }

  function go(n) {
    idx = Math.max(0, Math.min(n, steps.length - 1));
    const step = steps[idx];
    document.getElementById('tour-tip-title').textContent = step.title;
    document.getElementById('tour-tip-text').textContent = step.text;
    document.getElementById('tour-progress').textContent = `${idx + 1} / ${steps.length}`;
    document.getElementById('tour-back').style.visibility = idx === 0 ? 'hidden' : 'visible';
    document.getElementById('tour-next').textContent = idx >= steps.length - 1 ? 'Done' : 'Next';
    // Ensure the target is scrolled into view (Library tab etc. are always visible,
    // but be safe) then position. Call position() directly rather than via
    // requestAnimationFrame — rAF is paused in background/hidden tabs, which
    // would leave the spotlight stuck at 0×0. A short timeout re-settles after
    // any scroll/reflow and fires even when the tab is not foregrounded.
    const target = document.querySelector(step.sel);
    if (target?.scrollIntoView) target.scrollIntoView({ block: 'nearest' });
    position();
    setTimeout(position, 60);
  }

  function start() {
    // Idempotent: if a tour is already open, don't restart it — this prevents
    // the first-run auto-start (fired on a timer) from resetting a tour the
    // user has already begun, and guards against double resize listeners.
    if (document.getElementById('tour-overlay')) return;
    steps = STEPS.filter(s => document.querySelector(s.sel));
    if (!steps.length) return;
    build();
    idx = 0;
    go(0);
    window.addEventListener('resize', position);
  }

  function end() {
    try { localStorage.setItem(SEEN_KEY, 'true'); } catch (_) {}
    window.removeEventListener('resize', position);
    document.removeEventListener('keydown', _onKey, true);
    overlay()?.remove();
  }

  // Public replay hook + About button.
  window.startOnboardingTour = start;
  document.getElementById('btn-about-tour')?.addEventListener('click', () => {
    // Close the settings modal so the spotlight targets aren't hidden behind it.
    document.getElementById('modal-overlay')?.classList.remove('open');
    setTimeout(start, 220);
  });

  // First run: auto-start once, after the UI has settled.
  let seen = false;
  try { seen = localStorage.getItem(SEEN_KEY) === 'true'; } catch (_) {}
  if (!seen) {
    window.addEventListener('load', () => setTimeout(() => {
      // Don't fight a modal/overlay that may already be open on first paint.
      if (!document.querySelector('.settings-modal.open, #tg-auth-overlay.open')) start();
    }, 900));
  }

  // Styles (injected; keeps this module drop-in like 06-audio-fx.js).
  const st = document.createElement('style');
  st.textContent = `
#tour-overlay { position: fixed; inset: 0; z-index: 1500; }
#tour-spot { position: fixed; border-radius: 8px; pointer-events: none;
  box-shadow: 0 0 0 9999px rgba(0,0,0,.72), 0 0 0 2px var(--accent, #c4fa55);
  transition: left .25s ease, top .25s ease, width .25s ease, height .25s ease; }
#tour-tip { position: fixed; width: 300px; max-width: calc(100vw - 24px);
  background: var(--bg2, #1a1a1a); border: 1px solid var(--border2, #404040);
  border-radius: 10px; padding: 14px 16px; box-shadow: 0 16px 48px rgba(0,0,0,.6);
  font-family: var(--sans, sans-serif); transition: left .25s ease, top .25s ease; }
#tour-tip-title { font-size: 14px; font-weight: 600; color: var(--fg, #f4f4f4); margin-bottom: 6px; }
#tour-tip-text { font-size: 12px; line-height: 1.55; color: var(--fg2, #b6b6b6); }
#tour-tip-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 14px; }
#tour-progress { font-family: var(--mono, monospace); font-size: 10px; color: var(--fg3, #828282); }
.tour-btns { display: flex; gap: 6px; }
#tour-tip .tour-btn-ghost { background: none; border: none; color: var(--fg3, #999);
  font-size: 12px; padding: 5px 10px; border-radius: 6px; cursor: pointer; }
#tour-tip .tour-btn-ghost:hover { background: var(--bg3, #232323); color: var(--fg, #fff); }
#tour-tip .tour-btn-go { background: var(--accent, #c4fa55); border: none; color: #111;
  font-size: 12px; font-weight: 600; padding: 5px 16px; border-radius: 6px; cursor: pointer; }
#tour-tip .tour-btn-go:hover { filter: brightness(1.05); }`;
  document.head.appendChild(st);
})();
