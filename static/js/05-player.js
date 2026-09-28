//  NOW PLAYING PANEL (right, collapsible)
// ═══════════════════════════════════════
let _npOpen = false;
let _npArtistKey = null;   // last artist whose "About" card was loaded
let _npLyricsMode   = false;
let _npLyricsData   = null;   // parsed [{t, text}] for synced LRC, else null
let _npLyricsKey    = null;   // track key currently loaded
let _npLyricsActive = -1;     // index of highlighted synced line

// Parse LRC synced lyrics into [{t: seconds, text}], sorted by time.
function _npParseLrc(lrc) {
  const out = [];
  const re = /\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]/g;
  (lrc || '').split(/\r?\n/).forEach(line => {
    const stamps = []; let m; re.lastIndex = 0;
    while ((m = re.exec(line)) !== null) {
      const frac = m[3] ? parseInt((m[3] + '000').slice(0, 3), 10) / 1000 : 0;
      stamps.push((+m[1]) * 60 + (+m[2]) + frac);
    }
    const text = line.replace(re, '').trim();
    stamps.forEach(t => out.push({ t, text }));
  });
  out.sort((a, b) => a.t - b.t);
  return out;
}

async function _npLoadLyrics(track, alb) {
  const box = document.getElementById('np-lyrics');
  if (!box || !track) return;
  const title  = track.title || track.name || '';
  const artist = track.artist || alb?.artist || '';
  const key = (artist + '\x00' + title).toLowerCase();
  if (_npLyricsKey === key && box.children.length) return;   // already loaded
  _npLyricsKey = key; _npLyricsData = null; _npLyricsActive = -1;
  box.innerHTML = '<div class="np-lyrics-status">Loading lyrics…</div>';
  if (!title || !artist) { box.innerHTML = '<div class="np-lyrics-status">No lyrics for this track.</div>'; return; }
  try {
    const params = new URLSearchParams({ artist, title });
    const album = track.album || (alb && !alb.is_liked && !alb.is_playlist ? alb.album : '');
    if (album) params.set('album', album);
    if (track.duration) params.set('duration', String(Math.round(track.duration)));
    const r = await fetch('/lyrics?' + params.toString());
    if (_npLyricsKey !== key) return;
    if (!r.ok) { box.innerHTML = '<div class="np-lyrics-status">No lyrics found for this track.</div>'; return; }
    const d = await r.json();
    if (_npLyricsKey !== key) return;
    window._npLyricsRaw = d.synced || null;   // raw LRC text for .lrc export
    if (d.synced) {
      _npLyricsData = _npParseLrc(d.synced);
      box.innerHTML = '';
      _npLyricsData.forEach((ln, i) => {
        const el = document.createElement('div');
        el.className = 'np-lyric-line synced'; el.dataset.i = i;
        el.textContent = ln.text || '♪';
        el.addEventListener('click', () => {
          try { _mpAudio.currentTime = ln.t; if (!_mpPlaying) _mpAudio.play().catch(() => {}); } catch (_) {}
        });
        box.appendChild(el);
      });
      _npLyricsActive = -1; _npSyncLyrics();
    } else if (d.plain) {
      box.innerHTML = `<div class="np-lyrics-plain">${escHtml(d.plain)}</div>`;
    } else {
      box.innerHTML = '<div class="np-lyrics-status">No lyrics found for this track.</div>';
    }
  } catch (_) {
    if (_npLyricsKey === key) box.innerHTML = '<div class="np-lyrics-status">Could not load lyrics.</div>';
  }
}

// Highlight + auto-scroll the current synced lyric line (called from timeupdate).
function _npSyncLyrics() {
  if (!_npLyricsMode || !_npLyricsData || !_npLyricsData.length) return;
  const t = _mpAudio.currentTime;
  let idx = -1;
  for (let i = 0; i < _npLyricsData.length; i++) {
    if (_npLyricsData[i].t <= t + 0.15) idx = i; else break;
  }
  if (idx === _npLyricsActive) return;
  _npLyricsActive = idx;
  const box = document.getElementById('np-lyrics');
  if (!box) return;
  box.querySelectorAll('.np-lyric-line').forEach(el => el.classList.toggle('active', +el.dataset.i === idx));
  const activeEl = box.querySelector('.np-lyric-line.active');
  if (activeEl) activeEl.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function _npLyricsToggle() {
  _npLyricsMode = !_npLyricsMode;
  document.getElementById('np-content')?.classList.toggle('np-lyrics-mode', _npLyricsMode);
  document.getElementById('np-lyrics-toggle')?.classList.toggle('active', _npLyricsMode);
  const titleEl = document.getElementById('np-header-title');
  if (titleEl) titleEl.textContent = _npLyricsMode ? 'Lyrics' : 'Now Playing';
  if (_npLyricsMode) {
    if (!_npOpen) _npToggle();
    const track = _mpQueue[_mpIdx];
    if (track) _npLoadLyrics(track, _mpCurrentAlb);
  }
}

function _npApply() {
  document.getElementById('app')?.classList.toggle('np-open', _npOpen);
  const btn = document.getElementById('mp-np-toggle');
  if (btn) btn.classList.toggle('active', _npOpen);
}
function _npToggle() {
  _npOpen = !_npOpen;
  try { localStorage.setItem('tgdl_np_open', _npOpen ? '1' : '0'); } catch (_) {}
  _npApply();
}
function _npCollapse() {
  _npOpen = false;
  try { localStorage.setItem('tgdl_np_open', '0'); } catch (_) {}
  _npApply();
}

// Extract the primary (first credited) artist from a multi-artist string like
// "Kanye West, Lupe Fiasco" or "DOOM feat. Ghostface" → "Kanye West" / "DOOM".
// Deliberately does NOT split on "&"/"and" so band names ("Simon & Garfunkel",
// "Earth, Wind & Fire") survive the first comma only when there's a feat/`,` AND
// the whole string fails to resolve (handled by callers trying full first).
function _primaryArtist(name) {
  if (!name) return '';
  let s = String(name)
    .replace(/\s*[\(\[]?\s*(feat\.?|ft\.?|featuring|with)\b[\s\S]*$/i, '')  // drop "feat. …" tail
    .split(/\s*[,;\/]\s*|\s+x\s+/i)[0]                                      // first of , ; / or " x "
    .trim();
  return s || String(name).trim();
}

// Resolve the best cover for a single track. In a playlist the folder/album
// cover is a collage, so use the per-FILE embedded art (/track-cover) rather
// than the folder cover (/cover/<hash>).
function _trackCoverSrc(track, alb) {
  if (track.cover_url) return track.cover_url;
  const trackPh = track.path_hash || alb?.path_hash || '';
  if (trackPh && track.name)
    return `/track-cover?path_hash=${encodeURIComponent(trackPh)}&name=${encodeURIComponent(track.name)}`;
  if (alb?.cover_url) return alb.cover_url;
  if (trackPh) return `/cover/${trackPh}`;
  return '';
}

// Refresh the panel with the currently-playing track (+ its artist card)
function _npUpdate(track, alb) {
  const content = document.getElementById('np-content');
  const empty   = document.getElementById('np-empty');
  if (!content || !empty) return;
  if (!track) { content.style.display = 'none'; empty.style.display = ''; return; }
  empty.style.display = 'none'; content.style.display = '';

  const coverSrc = _trackCoverSrc(track, alb);
  const img = document.getElementById('np-cover');
  const ph  = document.getElementById('np-cover-ph');
  if (coverSrc) {
    img.src = coverSrc; img.style.display = 'block'; ph.style.display = 'none';
    img.onerror = () => { img.style.display = 'none'; ph.style.display = 'flex'; };
  } else { img.style.display = 'none'; ph.style.display = 'flex'; }

  document.getElementById('np-track-title').textContent  = track.title || track.name || '-';
  const artistName = track.artist || alb?.artist || '';
  document.getElementById('np-track-artist').textContent = artistName;
  _npLoadArtist(artistName);
  _npRenderQueue();
  if (_npLyricsMode) _npLoadLyrics(track, alb);
}

// Render the "Next up" queue list (upcoming tracks after the current index).
function _npRenderQueue() {
  const wrap = document.getElementById('np-queue');
  const list = document.getElementById('np-queue-list');
  if (!wrap || !list) return;
  const upcoming = [];
  for (let i = _mpIdx + 1; i < _mpQueue.length && upcoming.length < 2; i++) upcoming.push(i);
  if (_mpIdx < 0 || !upcoming.length) { wrap.style.display = 'none'; return; }
  wrap.style.display = '';
  list.innerHTML = '';
  let dragFrom = -1;
  const clearMarks = () => list.querySelectorAll('.drop-before, .drop-after')
    .forEach(r => r.classList.remove('drop-before', 'drop-after'));

  upcoming.forEach(absIdx => {
    const t   = _mpQueue[absIdx];
    const tph = t.path_hash || _mpCurrentAlb?.path_hash || '';
    const thumbSrc = t.cover_url
      || (tph && t.name ? `/track-cover?path_hash=${encodeURIComponent(tph)}&name=${encodeURIComponent(t.name)}` : '');
    const thumb = thumbSrc
      ? `<img src="${escHtml(thumbSrc)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><span class="ico" style="display:none">${ICON.note}</span>`
      : `<span class="ico">${ICON.note}</span>`;

    const row = document.createElement('div');
    row.className = 'np-q-row';
    row.dataset.abs = absIdx;
    row.innerHTML = `
      <div class="np-q-thumb">${thumb}</div>
      <div class="np-q-info">
        <div class="np-q-title" title="${escHtml(t.title || t.name || '')}">${escHtml(t.title || t.name || '')}</div>
        <div class="np-q-artist">${escHtml(t.artist || _mpCurrentAlb?.artist || '')}</div>
      </div>`;

    row.addEventListener('click', () => _mpPlayTrack(absIdx, _mpCurrentAlb));

    // Drag to reorder upcoming tracks within the live queue
    row.draggable = true;
    row.addEventListener('dragstart', ev => {
      dragFrom = absIdx; ev.dataTransfer.effectAllowed = 'move';
      requestAnimationFrame(() => row.classList.add('dragging'));
    });
    row.addEventListener('dragend', () => { row.classList.remove('dragging'); clearMarks(); });
    row.addEventListener('dragover', ev => {
      if (dragFrom < 0) return;
      ev.preventDefault();
      const r = row.getBoundingClientRect();
      const after = (ev.clientY - r.top) > r.height / 2;
      clearMarks(); row.classList.add(after ? 'drop-after' : 'drop-before');
    });
    row.addEventListener('drop', ev => {
      ev.preventDefault(); clearMarks();
      const from = dragFrom; dragFrom = -1;
      if (from < 0 || from === absIdx) return;
      const r = row.getBoundingClientRect();
      const after = (ev.clientY - r.top) > r.height / 2;
      let to = absIdx + (after ? 1 : 0);
      if (from < to) to -= 1;
      if (to === from) return;
      const moved = _mpQueue.splice(from, 1)[0];
      _mpQueue.splice(to, 0, moved);
      _npRenderQueue();
    });

    list.appendChild(row);
  });
}

// Load the "About the artist" card via Deezer (search → meta for bio/fans).
async function _npLoadArtist(name) {
  const about = document.getElementById('np-about');
  if (!about) return;
  const clean = (name || '').trim();
  if (!clean || clean === 'Playlist' || clean === 'Liked Songs') {
    about.style.display = 'none'; _npArtistKey = null; return;
  }
  if (_npArtistKey === clean) { about.style.display = ''; return; }
  _npArtistKey = clean;
  about.style.display = '';
  about.classList.remove('np-bio-expanded');
  // Reset card while loading
  document.getElementById('np-about-img').style.backgroundImage = '';
  document.getElementById('np-about-name').textContent = clean;
  document.getElementById('np-about-meta').textContent = '';
  const bioEl  = document.getElementById('np-about-bio');
  const moreEl = document.getElementById('np-about-more');
  const wikiEl = document.getElementById('np-about-wiki');
  bioEl.textContent = ''; moreEl.style.display = 'none'; wikiEl.style.display = 'none';

  try {
    // Deezer lookup: try the full credit first, then just the primary artist
    // (so multi-artist tags like "Kanye West, Lupe Fiasco" don't return junk).
    const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const primary = _primaryArtist(clean);
    const candidates = [clean]; if (primary && primary !== clean) candidates.push(primary);
    let hit = null;
    for (const q of candidates) {
      const sr = await fetch(`/artist-search?q=${encodeURIComponent(q)}`);
      const sd = await sr.json();
      const top = (sd.data && sd.data[0]) || null;
      if (!top) continue;
      // Accept when the result reasonably matches the query we sent
      const nq = norm(q), nh = norm(top.name);
      if (nh && (nq.includes(nh) || nh.includes(nq) || q === primary)) { hit = top; break; }
      if (!hit) hit = top;   // remember the first result as a last resort
    }
    let meta = hit;
    if (hit && hit.id) {
      try {
        const mr = await fetch(`/artist-meta/${hit.id}`);
        const md = await mr.json();
        if (!md.error) meta = md;
      } catch (_) {}
    }
    if (_npArtistKey !== clean) return;          // a newer track took over
    if (!meta) { about.style.display = 'none'; return; }

    const pic = meta.picture_xl || meta.picture_big || meta.picture_medium || meta.picture_small || '';
    document.getElementById('np-about-img').style.backgroundImage = pic ? `url(${JSON.stringify(pic)})` : '';
    document.getElementById('np-about-name').textContent = meta.name || clean;
    document.getElementById('np-about-card').dataset.artist = meta.name || clean;

    // Meta line: fans · N albums
    const metaParts = [];
    const fans = _fmtFans(meta.nb_fan);
    if (fans) metaParts.push(fans);
    if (meta.nb_album) metaParts.push(`${meta.nb_album} album${meta.nb_album === 1 ? '' : 's'}`);
    document.getElementById('np-about-meta').textContent = metaParts.join(' · ');

    // Bio: Wikipedia (Deezer has none). Falls back to Deezer biography if present.
    let bio = (meta.biography || '').replace(/<[^>]*>/g, ' ').replace(/\s{2,}/g, ' ').trim();
    let wikiUrl = '';
    try {
      const br = await fetch(`/artist-bio?name=${encodeURIComponent(meta.name || clean)}`);
      const bd = await br.json();
      if (_npArtistKey !== clean) return;
      if (bd && bd.bio) { bio = bd.bio; wikiUrl = bd.url || ''; }
    } catch (_) {}

    bioEl.textContent = bio;
    bioEl.style.display = bio ? '' : 'none';
    // Offer "Show more" only when the text is actually clamped
    requestAnimationFrame(() => {
      if (_npArtistKey !== clean) return;
      const clamped = bioEl.scrollHeight - bioEl.clientHeight > 4;
      moreEl.style.display = (bio && clamped) ? '' : 'none';
    });
    if (wikiUrl) { wikiEl.href = wikiUrl; wikiEl.style.display = ''; }
  } catch (_) {
    if (_npArtistKey === clean) about.style.display = 'none';
  }
}

// Open an artist in the Library tab, if present (matches full or primary name).
function _npOpenArtist(name) {
  if (!name) return;
  switchTab('library');
  const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const wanted = [norm(name), norm(_primaryArtist(name))];
  const target = (_libArtistList || []).find(a =>
    !a.is_home_group && !a.is_liked_group && !a.is_playlist_group && wanted.includes(norm(a.name)));
  if (target) _selectLibArtist(target, true);
  else _toast(`${_primaryArtist(name) || name} isn't in your library yet.`, 'info');
}

// ═══════════════════════════════════════
//  LIBRARY SEARCH PALETTE  ( / )
// ═══════════════════════════════════════
let _lsResults = [];
let _lsActive  = -1;

function openLibSearch() {
  const ov  = document.getElementById('libsearch-overlay');
  const inp = document.getElementById('libsearch-input');
  if (!ov || !inp) return;
  ov.classList.add('open');
  inp.value = '';
  _renderLibSearch('');
  setTimeout(() => inp.focus(), 20);
  // Make sure library data is available to search
  if (!_libAllAlbums || !_libAllAlbums.length) loadLibrary({ keepView: true });
}
function closeLibSearch() {
  document.getElementById('libsearch-overlay')?.classList.remove('open');
}

function _lsRow(idx, cover, title, sub) {
  const thumb = cover
    ? `<img class="ls-thumb" src="${escHtml(cover)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><div class="ls-thumb-ph" style="display:none">${ICON.note}</div>`
    : `<div class="ls-thumb-ph">${ICON.note}</div>`;
  return `<div class="ls-item" data-idx="${idx}">${thumb}
    <div class="ls-info"><div class="ls-title">${escHtml(title)}</div><div class="ls-sub">${escHtml(sub)}</div></div></div>`;
}

function _renderLibSearch(q) {
  const box = document.getElementById('libsearch-results');
  if (!box) return;
  q = (q || '').trim().toLowerCase();
  const has = s => (s || '').toLowerCase().includes(q);
  _lsResults = [];
  if (!q) { box.innerHTML = '<div class="ls-empty">Type to search your artists, albums and playlists.</div>'; return; }

  const artists = (_libArtistList || []).filter(a =>
    !a.is_home_group && !a.is_liked_group && !a.is_playlist_group && has(a.name)).slice(0, 6);
  const albums = (_libAllAlbums || []).filter(a => !a.is_playlist && (has(a.album) || has(a.artist))).slice(0, 14);
  const playlists = (_libAllAlbums || []).filter(a => a.is_playlist && has(a.album)).slice(0, 6);

  let html = '';
  if (artists.length) {
    html += '<div class="ls-group-label">Artists</div>';
    artists.forEach(a => {
      const idx = _lsResults.length; _lsResults.push({ type: 'artist', ref: a });
      html += _lsRow(idx, a.cover_url || '', a.name, `${a.albums.length} album${a.albums.length !== 1 ? 's' : ''}`);
    });
  }
  if (albums.length) {
    html += '<div class="ls-group-label">Albums</div>';
    albums.forEach(a => {
      const idx = _lsResults.length; _lsResults.push({ type: 'album', ref: a });
      const cover = a.cover_url || (a.path_hash ? `/cover/${a.path_hash}` : '');
      html += _lsRow(idx, cover, a.album, a.artist);
    });
  }
  if (playlists.length) {
    html += '<div class="ls-group-label">Playlists</div>';
    playlists.forEach(a => {
      const idx = _lsResults.length; _lsResults.push({ type: 'playlist', ref: a });
      const cover = a.cover_url || (a.path_hash ? `/cover/${a.path_hash}` : '');
      html += _lsRow(idx, cover, a.album, `${a.track_count || ''} tracks`);
    });
  }
  box.innerHTML = html || '<div class="ls-empty">No matches in your library.</div>';
  _lsActive = _lsResults.length ? 0 : -1;
  _lsHighlight();
  box.querySelectorAll('.ls-item').forEach(el =>
    el.addEventListener('click', () => _lsOpen(+el.dataset.idx)));
}

function _lsHighlight() {
  const box = document.getElementById('libsearch-results');
  if (!box) return;
  box.querySelectorAll('.ls-item').forEach(el =>
    el.classList.toggle('active', +el.dataset.idx === _lsActive));
  const active = box.querySelector('.ls-item.active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

function _lsOpen(idx) {
  const r = _lsResults[idx];
  if (!r) return;
  closeLibSearch();
  switchTab('library');
  if (r.type === 'artist') {
    _selectLibArtist(r.ref, true);
  } else if (r.type === 'album') {
    const grp = (_libArtistList || []).find(a => !a.is_playlist_group && a.name === r.ref.artist);
    if (grp) Promise.resolve(_selectLibArtist(grp, true)).then(() => _openLibAlbum(r.ref));
    else _openLibAlbum(r.ref);
  } else if (r.type === 'playlist') {
    const grp = (_libArtistList || []).find(a => a.is_playlist_group);
    if (grp) Promise.resolve(_selectLibArtist(grp, true)).then(() => _openLibAlbum(r.ref));
    else _openLibAlbum(r.ref);
  }
}

document.getElementById('libsearch-input')?.addEventListener('input', (e) => _renderLibSearch(e.target.value));
document.getElementById('libsearch-input')?.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { e.preventDefault(); closeLibSearch(); return; }
  if (e.key === 'ArrowDown') { e.preventDefault(); if (_lsResults.length) { _lsActive = (_lsActive + 1) % _lsResults.length; _lsHighlight(); } }
  else if (e.key === 'ArrowUp') { e.preventDefault(); if (_lsResults.length) { _lsActive = (_lsActive - 1 + _lsResults.length) % _lsResults.length; _lsHighlight(); } }
  else if (e.key === 'Enter') { e.preventDefault(); if (_lsActive >= 0) _lsOpen(_lsActive); }
});
document.getElementById('libsearch-overlay')?.addEventListener('click', (e) => {
  if (e.target === document.getElementById('libsearch-overlay')) closeLibSearch();
});

// ═══════════════════════════════════════
//  MINI PLAYER
// ═══════════════════════════════════════

function _mpPlayTrack(idx, alb, autoplay = true) {
  const track = _mpQueue[idx];
  if (!track) return;

  // A manual track change during a crossfade abandons the fade (the adopt
  // path below is only taken when the fade itself initiated this call).
  if (window._mpCancelXfade && !(window._mpXfadeAdopt && window._mpXfadeAdopt.idx === idx)) {
    _mpCancelXfade();
  }

  _mpIdx        = idx;
  _mpPlaying    = autoplay;
  _mpCurrentAlb = alb;   // lock the album for this playback session
  _mpTriedTranscode = false;   // fresh track, allow one transcode retry if needed
  _mpPreloadedIdx = -1;        // re-evaluate which track to preload next
  _mpHighlightRow(idx);        // instant visual feedback (before async cover/now-playing work)

  // A track may carry its own path_hash (e.g. Liked Songs span many albums);
  // otherwise fall back to the album's path_hash.
  const trackPh = track.path_hash || alb?.path_hash || '';

  // Prefer local file stream; fall back to Deezer 30s preview
  const localSrc = (trackPh && track.name)
    ? `/audio-file?path_hash=${encodeURIComponent(trackPh)}&name=${encodeURIComponent(track.name)}`
    : (track.preview_url || '');
  if (window._mpXfadeAdopt && window._mpXfadeAdopt.idx === idx) {
    // Crossfade hand-off: the incoming element is already playing this track
    // at full volume, make it the active element instead of restarting.
    _mpAudio = window._mpXfadeAdopt.el;
    window._mpXfadeAdopt = null;
  } else {
    _mpAudio.src = localSrc;
    _mpAudio.volume = parseFloat(document.getElementById('mp-vol-range')?.value || '1');
    if (autoplay) _mpAudio.play().catch(() => {});
  }

  // Reset the scrubber so we don't briefly flash the old/preview duration (0:30)
  const _f = document.getElementById('mp-prog-fill');
  const _c = document.getElementById('mp-time-cur');
  const _t = document.getElementById('mp-time-tot');
  if (_f) _f.style.width = '0%';
  if (_c) _c.textContent = '0:00';
  if (_t) _t.textContent = '0:00';

  document.getElementById('mini-player').classList.remove('hidden');

  // Cover: per-file embedded art (playlists carry a collage folder cover)
  const coverSrc = _trackCoverSrc(track, alb);
  const coverImg = document.getElementById('mp-cover-img');
  const coverPh  = document.getElementById('mp-cover-ph');
  if (coverSrc) {
    // If the art 404s (e.g. a restored track whose folder moved), reveal the
    // placeholder note icon instead of leaving a blank/broken image.
    coverImg.onerror = () => { coverImg.style.display = 'none'; coverPh.style.display = 'block'; };
    coverImg.src          = coverSrc;
    coverImg.style.display = 'block';
    coverPh.style.display  = 'none';
  } else {
    coverImg.onerror = null;
    coverImg.style.display = 'none';
    coverPh.style.display  = 'block';
  }

  document.getElementById('mp-title').textContent       = track.title || track.name;
  // Prefer the track's own artist tag (correct for playlists / Liked Songs,
  // where the album-level "artist" is just "Playlist" / "Liked Songs").
  document.getElementById('mp-artist-name').textContent = track.artist || alb?.artist || '';
  // Show local vs preview label
  const _previewNote = document.getElementById('mp-preview-note');
  if (_previewNote) {
    const _isLocal = !!(trackPh && track.name);
    _previewNote.textContent  = _isLocal ? '' : '30s preview · Deezer';
    _previewNote.style.display = _isLocal ? 'none' : '';
  }

  _mpUpdatePlayBtn();
  _mpUpdateLikeBtn(_isTrackLiked(track, alb));
  _mpHighlightRow(idx);
  _npUpdate(track, alb);
  _mpUpdateMediaSession(track, alb, coverSrc);

  // Scrobbling: only local files (never 30s Deezer previews).
  const _isLocalTrack = !!(trackPh && track.name);
  _mpScrobbleState = {
    isLocal: _isLocalTrack,
    scrobbled: false,
    meta: {
      title:  track.title || track.name || '',
      artist: track.artist || alb?.artist || '',
      album:  track.album || (alb && !alb.is_liked ? alb.album : '') || '',
    },
  };
  if (_isLocalTrack) _sendScrobble('now_playing');
  if (window._fxOnTrackChange) { try { _fxOnTrackChange(track, alb, trackPh); } catch (_) {} }
  _mpSaveState();
}

// ── Persist / restore playback across restarts ──────────────────────────────
let _mpPendingSeek = null;      // seconds to seek to once metadata loads (restore)
let _mpLastSaveAt  = 0;
let _mpKnownDur    = 0;         // last known track duration (survives preload="none")

let _mpRestoring = false;   // suppress saves while we restore (avoid clobbering time)

function _mpSaveState() {
  if (_mpRestoring) return;
  try {
    if (_mpIdx < 0 || !_mpQueue.length) { localStorage.removeItem('tgdl_player'); return; }
    const liveDur = (isFinite(_mpAudio.duration) && _mpAudio.duration > 0) ? _mpAudio.duration : _mpKnownDur;
    localStorage.setItem('tgdl_player', JSON.stringify({
      queue:   _mpQueue,
      idx:     _mpIdx,
      album:   _mpCurrentAlb,
      time:    _mpAudio.currentTime || _mpPendingSeek || 0,
      dur:     liveDur || 0,
      shuffle: _mpShuffle,
      repeat:  _mpRepeat,
    }));
  } catch (_) {}
}

function _mpRestoreState() {
  let data;
  try { data = JSON.parse(localStorage.getItem('tgdl_player') || 'null'); } catch (_) { return; }
  if (!data || !Array.isArray(data.queue) || !data.queue.length) return;
  if (typeof data.idx !== 'number' || data.idx < 0 || data.idx >= data.queue.length) return;
  _mpRestoring  = true;
  _mpQueue      = data.queue;
  _mpCurrentAlb = data.album || null;
  _mpShuffle    = !!data.shuffle;
  _mpRepeat     = data.repeat || 'off';
  _mpShuffleOrder = [];
  _mpPendingSeek = data.time || 0;
  _mpKnownDur    = data.dur || 0;
  try {
    _mpPlayTrack(data.idx, _mpCurrentAlb, false);   // load paused; user presses play to resume
    _mpUpdateModeButtons();
    // The audio element is preload="none", so loadedmetadata won't fire until
    // the user hits play. Paint the persisted position + duration now so the
    // scrubber reflects where playback will resume from (instead of 0:00).
    _mpRenderProgress(_mpPendingSeek, _mpKnownDur);
  } finally {
    _mpRestoring = false;
  }
}

// Fire-and-forget scrobble submission to the backend (which forwards to
// ListenBrainz / Last.fm if the user has enabled and configured it).
function _sendScrobble(kind) {
  const st = _mpScrobbleState;
  if (!st || !st.isLocal || !st.meta.title || !st.meta.artist) return;
  fetch('/scrobble', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, ...st.meta }),
  }).catch(() => {});
}

// Local listening history: recorded server-side regardless of whether a
// scrobbling service is configured. Powers the Listening card in Stats.
function _sendPlayEvent(meta) {
  fetch('/play-event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(meta),
  }).catch(() => {});
}

// Discord Rich Presence: the server pushes the current track to the local
// Discord client (opt-in; no-ops server-side when the feature is off). op is
// 'resume' | 'pause' | 'stop'; play/resume carry the track meta + position.
function _sendPresence(op) {
  const body = { op };
  if (op === 'resume' || op === 'play') {
    const m = (_mpScrobbleState && _mpScrobbleState.meta) || {};
    if (!m.title && !m.artist) return;   // nothing worth showing yet
    body.title = m.title || ''; body.artist = m.artist || ''; body.album = m.album || '';
    body.position = _mpAudio.currentTime || 0;
  }
  fetch('/presence', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => {});
}

// ── OS media integration (hardware keys, Windows media overlay) ──────────────
function _mpUpdateMediaSession(track, alb, coverSrc) {
  if (!('mediaSession' in navigator)) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title:  track.title || track.name || '',
      artist: track.artist || alb?.artist || '',
      album:  track.album || (alb && !alb.is_liked ? alb.album : '') || '',
      artwork: coverSrc
        ? [{ src: new URL(coverSrc, location.href).href, sizes: '512x512', type: 'image/jpeg' }]
        : [],
    });
  } catch (_) {}
}

(function _mpInitMediaSession() {
  if (!('mediaSession' in navigator)) return;
  try {
    const ms = navigator.mediaSession;
    ms.setActionHandler('play',  () => { if (!_mpPlaying) document.getElementById('mp-play-btn')?.click(); });
    ms.setActionHandler('pause', () => { if (_mpPlaying)  document.getElementById('mp-play-btn')?.click(); });
    ms.setActionHandler('previoustrack', () => document.getElementById('mp-prev')?.click());
    ms.setActionHandler('nexttrack',     () => document.getElementById('mp-next')?.click());
    ms.setActionHandler('seekto', d => {
      if (d.seekTime != null && isFinite(_mpAudio.duration)) _mpAudio.currentTime = d.seekTime;
    });
  } catch (_) {}
})();

function _mpSyncPositionState() {
  if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
  try {
    if (isFinite(_mpAudio.duration) && _mpAudio.duration > 0) {
      navigator.mediaSession.setPositionState({
        duration: _mpAudio.duration,
        position: Math.min(_mpAudio.currentTime, _mpAudio.duration),
        playbackRate: _mpAudio.playbackRate || 1,
      });
    }
  } catch (_) {}
}

// ── Sleep timer (cycles Off → 15 → 30 → 60 min → End of track) ───────────────
let _mpSleepMode  = 'off';   // 'off' | '15' | '30' | '60' | 'track'
let _mpSleepUntil = 0;       // epoch ms when a minute-based timer expires

function _mpUpdateSleepBtn() {
  const b = document.getElementById('mp-sleep');
  if (!b) return;
  b.classList.toggle('active', _mpSleepMode !== 'off');
  b.title = _mpSleepMode === 'off'   ? 'Sleep timer: off'
          : _mpSleepMode === 'track' ? 'Sleep timer: stop after this track'
          : `Sleep timer: ~${Math.max(1, Math.ceil((_mpSleepUntil - Date.now()) / 60000))} min left`;
}

document.getElementById('mp-sleep')?.addEventListener('click', () => {
  const order = ['off', '15', '30', '60', 'track'];
  _mpSleepMode  = order[(order.indexOf(_mpSleepMode) + 1) % order.length];
  _mpSleepUntil = /^\d+$/.test(_mpSleepMode) ? Date.now() + parseInt(_mpSleepMode, 10) * 60000 : 0;
  _mpUpdateSleepBtn();
});

setInterval(() => {
  if (_mpSleepUntil && Date.now() >= _mpSleepUntil) {
    _mpSleepMode  = 'off';
    _mpSleepUntil = 0;
    _mpUpdateSleepBtn();
    if (_mpPlaying) _mpStop();
  } else if (_mpSleepUntil) {
    _mpUpdateSleepBtn();      // keep the "~N min left" tooltip fresh
  }
}, 15000);

function _mpUpdatePlayBtn() {
  const btn = document.getElementById('mp-play-btn');
  if (!btn) return;
  btn.innerHTML = _mpPlaying ? ICON.pause : ICON.play;
  btn.classList.add('ico');
  // Sync the active track row button too
  if (_mpIdx >= 0) _mpHighlightRow(_mpIdx);
}

function _mpHighlightRow(activeIdx) {
  // Only highlight rows if the currently displayed album is the one playing
  const displayingPlayingAlbum = _mpCurrentAlb && _mpCurrentAlb === _libActiveAlbum;
  document.querySelectorAll('.lib-track-row').forEach((row, i) => {
    const isActive = displayingPlayingAlbum && i === activeIdx;
    row.classList.toggle('playing', isActive);
    const btn = row.querySelector('.lib-track-play');
    if (btn && !btn.classList.contains('no-preview')) {
      btn.classList.toggle('playing', isActive);
      btn.classList.add('ico');
      btn.innerHTML = (isActive && _mpPlaying) ? ICON.pause : ICON.play;
    }
  });
}

function _mpStop() {
  _mpAudio.pause();
  _mpPlaying = false;
  _mpUpdatePlayBtn();
  _mpHighlightRow(-1);
  _sendPresence('stop');
}

// Format seconds as m:ss for the player scrubber labels.
function _mpFmtTime(s) {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Paint the scrubber (fill + current/total labels) from explicit values.
// Used by timeupdate, by loadedmetadata, and, crucially, by restore: the
// audio element is preload="none", so on startup there is no live media
// position or duration yet, and we must render the persisted ones ourselves.
function _mpRenderProgress(cur, dur) {
  dur = dur || 0;
  const pct   = dur > 0 ? Math.min(100, (cur / dur) * 100) : 0;
  const fill  = document.getElementById('mp-prog-fill');
  const curEl = document.getElementById('mp-time-cur');
  const totEl = document.getElementById('mp-time-tot');
  if (fill)  fill.style.width = pct + '%';
  if (curEl) curEl.textContent = _mpFmtTime(cur);
  if (totEl && dur > 0) totEl.textContent = _mpFmtTime(dur);
}

// Audio event listeners: named handlers bound to BOTH pooled audio elements
// (main + crossfade partner). Each ignores events from the inactive element.
function _mpOnTimeupdate(e) {
  if (e.target !== _mpAudio) return;
  const dur  = _mpAudio.duration || 30;
  const cur  = _mpAudio.currentTime;
  if (isFinite(_mpAudio.duration) && _mpAudio.duration > 0) _mpKnownDur = _mpAudio.duration;
  _mpRenderProgress(cur, dur);

  // Scrobble once the track passes the standard threshold (≥50% played, or
  // ≥4 min). Local files only; _sendScrobble guards on that.
  const st = _mpScrobbleState;
  if (st && st.isLocal && !st.scrobbled && cur >= Math.min(dur * 0.5, 240) && cur > 20) {
    st.scrobbled = true;
    _sendScrobble('scrobble');
    _sendPlayEvent(st.meta);      // local listening history (always on)
  }

  // Persist playback position at most every ~5s
  const now = Date.now();
  if (now - _mpLastSaveAt > 5000) { _mpLastSaveAt = now; _mpSaveState(); _mpSyncPositionState(); }

  // Near-gapless: warm the next track into the browser cache before this one ends
  if (_mpPlaying && dur > 0 && (dur - cur) < 20 && _mpRepeat !== 'one') {
    const next = (_mpIdx + 1 < _mpQueue.length) ? _mpIdx + 1 : (_mpRepeat === 'all' ? 0 : -1);
    if (next >= 0 && next !== _mpPreloadedIdx) {
      const t  = _mpQueue[next];
      const ph = t && (t.path_hash || _mpCurrentAlb?.path_hash);
      const url = (ph && t.name)
        ? `/audio-file?path_hash=${encodeURIComponent(ph)}&name=${encodeURIComponent(t.name)}`
        : (t?.preview_url || '');
      if (url) {
        if (!_mpPreloadEl) _mpPreloadEl = new Audio();
        _mpPreloadEl.preload = 'auto';
        _mpPreloadEl.src = url;
        try { _mpPreloadEl.load(); } catch (_) {}
        _mpPreloadedIdx = next;
      }
    }
  }

  _npSyncLyrics();
  if (window._fxTick) { try { _fxTick(cur, dur); } catch (_) {} }
}

// Apply a pending restore-seek once the media duration is known
function _mpOnLoadedMeta(e) {
  if (e.target !== _mpAudio) return;
  if (isFinite(_mpAudio.duration) && _mpAudio.duration > 0) _mpKnownDur = _mpAudio.duration;
  if (_mpPendingSeek != null) {
    const seek = _mpPendingSeek;
    _mpPendingSeek = null;
    try { _mpAudio.currentTime = seek; } catch (_) {}
    // Refresh labels now that the real duration is known (paused media may not
    // fire a timeupdate on its own).
    _mpRenderProgress(seek, _mpAudio.duration || _mpKnownDur);
  }
}

function _mpOnPlay(e) {
  if (e.target !== _mpAudio) return;
  _mpPlaying = true;  _mpUpdatePlayBtn(); _mpSaveState();
  try { if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing'; } catch (_) {}
  _sendPresence('resume');   // covers both a new track starting and a resume
}
function _mpOnPause(e) {
  if (e.target !== _mpAudio) return;
  _mpPlaying = false; _mpUpdatePlayBtn(); _mpSaveState();
  try { if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'paused'; } catch (_) {}
  _sendPresence('pause');
}
window.addEventListener('beforeunload', () => _mpSaveState());
// A track that actually starts producing audio clears the error streak.
function _mpOnPlaying(e) {
  if (e.target !== _mpAudio) return;
  _mpErrorStreak = 0;
  // A transcoded local file is full quality, drop the "converting/preview" note.
  const src = _mpAudio.currentSrc || _mpAudio.src || '';
  if (src.indexOf('/audio-stream') !== -1) {
    const note = document.getElementById('mp-preview-note');
    if (note) { note.textContent = ''; note.style.display = 'none'; }
  }
}

// Move on after a failed track: streak-guarded skip to the next playable one.
function _mpSkipAfterError(track, reason) {
  _mpErrorStreak++;
  if (_mpErrorStreak > _mpQueue.length) {
    _mpErrorStreak = 0;
    _mpPlaying = false; _mpUpdatePlayBtn(); _mpHighlightRow(-1);
    _toast("Couldn't play these tracks - the files may have moved or been removed.", 'error');
    return;
  }
  const next = _mpPickNext(true);
  if (next >= 0) {
    // Only surface the first failure; stay quiet while we hunt for the next
    // playable track so a fully-stale queue doesn't spam a toast per track.
    if (reason && _mpErrorStreak === 1) _toast(reason, 'info');
    _mpPlayTrack(next, _mpCurrentAlb);
  } else {
    _mpPlaying = false; _mpUpdatePlayBtn(); _mpHighlightRow(-1);
  }
}

// The current source failed. Figure out WHY (missing file vs. the browser not
// being able to decode it, e.g. 24-bit/hi-res FLAC) and recover accordingly:
// transcode the file server-side, fall back to the preview, or skip.
function _mpOnError(e) {
  if (e.target !== _mpAudio) return;
  if (!_mpQueue.length || _mpIdx < 0) return;
  const track = _mpQueue[_mpIdx];
  if (!track) return;
  // A restored / paused session only preloads its source, the user hasn't
  // pressed play yet. Don't cascade through the whole queue throwing errors
  // (that's the "File not found" toast storm on launch). Wait for an explicit
  // play; the error will re-fire then, with recovery handled normally.
  if (!_mpPlaying) return;
  const src = _mpAudio.currentSrc || _mpAudio.src || '';
  const note = document.getElementById('mp-preview-note');

  // The transcoded stream itself failed → ffmpeg missing or transcode error.
  if (src.indexOf('/audio-stream') !== -1) {
    fetch(src, { headers: { Range: 'bytes=0-1' } }).then(r => {
      const msg = (r.status === 501)
        ? `Install ffmpeg to play "${track.title || track.name}" (needs transcoding).`
        : `Couldn't play "${track.title || track.name}".`;
      if (track.preview_url) {
        _mpAudio.src = track.preview_url; _mpAudio.play().catch(() => {});
        if (note) { note.textContent = '30s preview · Deezer'; note.style.display = ''; }
      } else {
        _mpSkipAfterError(track, msg);
      }
    }).catch(() => _mpSkipAfterError(track, `Couldn't play "${track.title || track.name}".`));
    return;
  }

  // A local file failed, probe whether it's served at all.
  if (src.indexOf('/audio-file') !== -1) {
    if (_mpTriedTranscode) { _mpSkipAfterError(track); return; }
    _mpTriedTranscode = true;
    console.warn('[playback] local file failed:', track.name,
                 'audio.error.code =', _mpAudio.error && _mpAudio.error.code);
    fetch(src, { headers: { Range: 'bytes=0-1' } }).then(r => {
      if (r.status === 404) {
        // The file isn't where we expect it on disk.
        _mpSkipAfterError(track, `File not found on disk: ${track.title || track.name}`);
        return;
      }
      // Served fine but the browser couldn't decode it → transcode via ffmpeg.
      const ph = track.path_hash || _mpCurrentAlb?.path_hash || '';
      if (ph && track.name) {
        if (note) { note.textContent = 'Converting for playback…'; note.style.display = ''; }
        _mpAudio.src = `/audio-stream?path_hash=${encodeURIComponent(ph)}&name=${encodeURIComponent(track.name)}`;
        _mpAudio.play().catch(() => {});
      } else if (track.preview_url) {
        _mpAudio.src = track.preview_url; _mpAudio.play().catch(() => {});
        if (note) { note.textContent = '30s preview · Deezer'; note.style.display = ''; }
      } else {
        _mpSkipAfterError(track);
      }
    }).catch(() => _mpSkipAfterError(track));
    return;
  }

  // A preview URL (or unknown source) failed → just skip.
  _mpSkipAfterError(track);
}

function _mpOnEnded(e) {
  if (e.target !== _mpAudio) return;
  _mpPlaying = false;
  // Sleep timer "end of track": stop here instead of advancing.
  if (_mpSleepMode === 'track') {
    _mpSleepMode = 'off';
    _mpUpdateSleepBtn();
    if (window._mpCancelXfade) _mpCancelXfade();
    _mpStop();
    return;
  }
  // A crossfade is mid-flight: the incoming element takes over, no gap.
  if (window._fxFinishXfade && _fxFinishXfade()) return;
  // Repeat one: replay the same track
  if (_mpRepeat === 'one' && _mpIdx >= 0) {
    _mpPlayTrack(_mpIdx, _mpCurrentAlb);
    return;
  }
  const next = _mpPickNext(true);   // true = auto-advance (allows repeat-all wrap)
  if (next >= 0) {
    _mpPlayTrack(next, _mpCurrentAlb);
    return;
  }
  // Queue ended, optionally continue with a related-artist radio (once).
  if (_mpAutoplayRadio && !_mpCurrentAlb?.is_radio) {
    _mpStartRadio().then(ok => { if (!ok) { _mpUpdatePlayBtn(); _mpHighlightRow(-1); } });
    return;
  }
  _mpUpdatePlayBtn();
  _mpHighlightRow(-1);
  _sendPresence('stop');
}

// Attach the full handler set to a pooled audio element. Called for the DOM
// element now and for the crossfade partner when 06-audio-fx.js creates it.
function _mpBindAudioEvents(el) {
  el.addEventListener('timeupdate',     _mpOnTimeupdate);
  el.addEventListener('loadedmetadata', _mpOnLoadedMeta);
  el.addEventListener('play',           _mpOnPlay);
  el.addEventListener('pause',          _mpOnPause);
  el.addEventListener('playing',        _mpOnPlaying);
  el.addEventListener('error',          _mpOnError);
  el.addEventListener('ended',          _mpOnEnded);
}
_mpBindAudioEvents(_mpAudio);

// ── Track navigation helpers (respect shuffle + repeat) ──────────────────────
function _mpTrackPlayable(t) {
  // A track may carry its own path_hash (e.g. Liked Songs span many albums and
  // the synthetic album has none); fall back to the current album's path_hash.
  const ph = t?.path_hash || _mpCurrentAlb?.path_hash;
  return !!((ph && t?.name) || t?.preview_url);
}

// Pick the next index to play. `auto` is true for natural song-end advancement
// (where repeat-all may wrap to the start); false for the manual Next button.
function _mpPickNext(auto) {
  if (!_mpQueue.length) return -1;

  if (_mpShuffle) {
    // Refill the shuffle bag (excluding the current track) when empty
    if (!_mpShuffleOrder.length) {
      _mpShuffleOrder = _mpQueue
        .map((t, i) => i)
        .filter(i => i !== _mpIdx && _mpTrackPlayable(_mpQueue[i]));
      for (let i = _mpShuffleOrder.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [_mpShuffleOrder[i], _mpShuffleOrder[j]] = [_mpShuffleOrder[j], _mpShuffleOrder[i]];
      }
    }
    if (_mpShuffleOrder.length) return _mpShuffleOrder.shift();
    // Bag empty: only continue when repeating all
    return (auto && _mpRepeat === 'all') ? _mpIdx : -1;
  }

  // Sequential
  for (let n = _mpIdx + 1; n < _mpQueue.length; n++) {
    if (_mpTrackPlayable(_mpQueue[n])) return n;
  }
  // Wrap to start when repeating all
  if (_mpRepeat === 'all') {
    for (let n = 0; n <= _mpIdx; n++) {
      if (_mpTrackPlayable(_mpQueue[n])) return n;
    }
  }
  return -1;
}

function _mpPickPrev() {
  if (!_mpQueue.length) return -1;
  for (let p = _mpIdx - 1; p >= 0; p--) {
    if (_mpTrackPlayable(_mpQueue[p])) return p;
  }
  if (_mpRepeat === 'all') {
    for (let p = _mpQueue.length - 1; p >= _mpIdx; p--) {
      if (_mpTrackPlayable(_mpQueue[p])) return p;
    }
  }
  return -1;
}

// Progress bar click
document.getElementById('mp-prog-track')?.addEventListener('click', ev => {
  const rect = ev.currentTarget.getBoundingClientRect();
  const pct  = (ev.clientX - rect.left) / rect.width;
  const dur  = _mpAudio.duration || 30;
  _mpAudio.currentTime = pct * dur;
});

// Controls
document.getElementById('mp-play-btn')?.addEventListener('click', () => {
  if (_mpPlaying) {
    _mpAudio.pause();
    // _mpPlaying will be set false by the 'pause' event handler
    return;
  }
  // A restored session preloads its source but never loaded it; if the element
  // has no usable source or already errored (e.g. the file moved, or preload=none
  // means nothing was fetched yet), (re)load the current track fresh: that runs
  // the full recovery path (transcode / single skip). Otherwise just resume.
  if (_mpIdx >= 0 && _mpQueue[_mpIdx] && (!_mpAudio.src || _mpAudio.error || _mpAudio.readyState === 0)) {
    _mpPlayTrack(_mpIdx, _mpCurrentAlb, true);
  } else if (_mpAudio.src) {
    _mpAudio.play().catch(() => {});
    // _mpPlaying will be set true by the 'play' event handler
  }
});

document.getElementById('mp-prev')?.addEventListener('click', () => {
  // Restart current track if we're more than 3s in, else go to previous
  if (_mpAudio.currentTime > 3 && _mpIdx >= 0) {
    _mpAudio.currentTime = 0;
    return;
  }
  const prev = _mpPickPrev();
  if (prev >= 0) _mpPlayTrack(prev, _mpCurrentAlb);
});

document.getElementById('mp-next')?.addEventListener('click', () => {
  const next = _mpPickNext(false);
  if (next >= 0) _mpPlayTrack(next, _mpCurrentAlb);
});

// ── Shuffle / Repeat / Like buttons ──────────────────────────────────────────
function _mpUpdateModeButtons() {
  const shBtn = document.getElementById('mp-shuffle');
  if (shBtn) {
    shBtn.classList.toggle('active', _mpShuffle);
    shBtn.title = _mpShuffle ? 'Shuffle: on' : 'Shuffle: off';
  }
  const rpBtn = document.getElementById('mp-repeat');
  if (rpBtn) {
    rpBtn.classList.toggle('active', _mpRepeat !== 'off');
    rpBtn.innerHTML = (_mpRepeat === 'one') ? ICON.repeatOne : ICON.repeat;
    rpBtn.classList.add('ico');
    rpBtn.title = _mpRepeat === 'off' ? 'Repeat: off'
                : _mpRepeat === 'all' ? 'Repeat: all'
                : 'Repeat: one';
  }
}

document.getElementById('mp-shuffle')?.addEventListener('click', () => {
  _mpShuffle = !_mpShuffle;
  _mpShuffleOrder = [];   // reset bag so it reshuffles from the current point
  _mpUpdateModeButtons();
  _mpSaveState();
});

document.getElementById('mp-repeat')?.addEventListener('click', () => {
  _mpRepeat = _mpRepeat === 'off' ? 'all' : _mpRepeat === 'all' ? 'one' : 'off';
  _mpUpdateModeButtons();
  _mpSaveState();
});

document.getElementById('mp-like')?.addEventListener('click', () => {
  if (_mpIdx < 0) return;
  const track = _mpQueue[_mpIdx];
  if (!track) return;
  _toggleLike(track, _mpCurrentAlb).then(liked => {
    _mpUpdateLikeBtn(liked);
  });
});

function _mpUpdateLikeBtn(liked) {
  const btn = document.getElementById('mp-like');
  if (!btn) return;
  btn.classList.toggle('liked', !!liked);
  btn.innerHTML = liked ? ICON.heartFilled : ICON.heart;
  btn.classList.add('ico');
  btn.title = liked ? 'Remove from Liked Songs' : 'Add to Liked Songs';
}

document.getElementById('mp-close')?.addEventListener('click', () => {
  _mpStop();
  _mpIdx = -1;
  _mpCurrentAlb = null;
  _mpQueue = [];
  document.getElementById('mini-player').classList.add('hidden');
  try { localStorage.removeItem('tgdl_player'); } catch (_) {}
});

// Restore the last playback session (paused) on startup
_mpRestoreState();

// ── Now Playing panel wiring ──
try { _npOpen = localStorage.getItem('tgdl_np_open') === '1'; } catch (_) {}
_npApply();
document.getElementById('mp-np-toggle')?.addEventListener('click', _npToggle);
document.getElementById('np-collapse')?.addEventListener('click', _npCollapse);
document.getElementById('np-lyrics-toggle')?.addEventListener('click', _npLyricsToggle);

// Make the currently-playing artist / title clickable → navigate to them.
function _openCurrentArtist() {
  const t = _mpQueue[_mpIdx];
  const name = t?.artist || _mpCurrentAlb?.artist || '';
  if (name) _npOpenArtist(name);
}
function _openCurrentAlbum() {
  const t = _mpQueue[_mpIdx];
  if (!t) return;
  const alb = _mpCurrentAlb;
  if (alb && !alb.is_radio && alb.path_hash) {
    switchTab('library');
    const grp = alb.is_playlist
      ? (_libArtistList || []).find(a => a.is_playlist_group)
      : (_libArtistList || []).find(a => !a.is_playlist_group && a.name === (alb.artist || t.artist));
    if (grp) Promise.resolve(_selectLibArtist(grp, true)).then(() => _openLibAlbum(alb));
    else _openLibAlbum(alb);
  } else {
    _openCurrentArtist();   // radio/preview, fall back to the artist
  }
}
document.getElementById('mp-artist-name')?.addEventListener('click', _openCurrentArtist);
document.getElementById('np-track-artist')?.addEventListener('click', _openCurrentArtist);
document.getElementById('mp-title')?.addEventListener('click', _openCurrentAlbum);
document.getElementById('np-track-title')?.addEventListener('click', _openCurrentAlbum);
// Open the artist from the image or name; "Show more" expands the bio.
['np-about-img', 'np-about-name'].forEach(id => {
  document.getElementById(id)?.addEventListener('click', () => {
    _npOpenArtist(document.getElementById('np-about-card')?.dataset.artist || '');
  });
});
document.getElementById('np-about-more')?.addEventListener('click', () => {
  const about = document.getElementById('np-about');
  const expanded = about.classList.toggle('np-bio-expanded');
  document.getElementById('np-about-more').textContent = expanded ? 'Show less' : 'Show more';
});
// Clear the rest of the queue (everything after the current track)
document.getElementById('np-queue-clear')?.addEventListener('click', () => {
  if (_mpIdx >= 0 && _mpQueue.length > _mpIdx + 1) {
    _mpQueue = _mpQueue.slice(0, _mpIdx + 1);
    _npRenderQueue();
  }
});

// Suppress the native browser context menu app-wide: the app uses its own
// right-click menus. Text fields keep it so copy/paste still works there.
document.addEventListener('contextmenu', (ev) => {
  if (ev.target.closest('input, textarea, [contenteditable="true"]')) return;
  ev.preventDefault();
});

document.getElementById('mp-vol-range')?.addEventListener('input', ev => {
  const v = parseFloat(ev.target.value);
  _mpAudio.volume = v;
  localStorage.setItem('tgd_volume', String(v));
  // Update fill gradient
  ev.target.style.setProperty('--vol-pct', (v * 100).toFixed(1) + '%');
  // Update icon
  const icon = document.getElementById('mp-vol-icon');
  if (icon) icon.innerHTML = v === 0 ? _volIconMuted() : _volIconFull();
});

document.getElementById('mp-vol-icon')?.addEventListener('click', () => {
  const range = document.getElementById('mp-vol-range');
  if (!range) return;
  if (_mpAudio.volume > 0) {
    _mpAudio._savedVol = _mpAudio.volume;
    _mpAudio.volume = 0;
    range.value = 0;
    range.style.setProperty('--vol-pct', '0%');
    document.getElementById('mp-vol-icon').innerHTML = _volIconMuted();
  } else {
    const v = _mpAudio._savedVol || 1;
    _mpAudio.volume = v;
    range.value = v;
    range.style.setProperty('--vol-pct', (v * 100).toFixed(1) + '%');
    document.getElementById('mp-vol-icon').innerHTML = _volIconFull();
  }
})
// ═══════════════════════════════════════
//  PAUSE / RESUME
// ═══════════════════════════════════════
let paused = false;

function setPauseBtn(isPaused) {
  paused = isPaused;
  const btn = document.getElementById('btn-pause');
  if (!btn) return;
  _setBtnIco(btn, isPaused ? 'play' : 'pause', isPaused ? 'Resume' : 'Pause');
  btn.title = isPaused ? 'Resume download' : 'Pause download';
}

document.getElementById('btn-pause').addEventListener('click', () => {
  if (!running) return;
  if (paused) {
    wsSend({ action: 'resume' });
    setPauseBtn(false);
    appendLog('  Resuming…\n', 'log-warn');
  } else {
    wsSend({ action: 'pause' });
    setPauseBtn(true);
    appendLog('  Pausing after current file…\n', 'log-warn');
  }
});

// ═══════════════════════════════════════
//  TG DROPDOWN
// ═══════════════════════════════════════
(function() {
  const btn      = document.getElementById('btn-tg-connect');
  const dropdown = document.getElementById('tg-dropdown');
  const ddConn   = document.getElementById('tg-dd-connect');
  const ddDisc   = document.getElementById('tg-dd-disconnect');

  function positionDropdown() {
    const r = btn.getBoundingClientRect();
    dropdown.style.top  = (r.bottom + 4) + 'px';
    dropdown.style.left = Math.max(4, r.right - 160) + 'px';
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (dropdown.style.display === 'block') {
      dropdown.style.display = 'none';
    } else {
      // Show disconnect option when logged in (session exists OR step=done)
      fetch('/telegram-status').then(r => r.json()).then(d => {
        const isConnected = d.session_exists || d.step === 'done';
        ddDisc.style.display = isConnected ? 'block' : 'none';
      }).catch(() => { ddDisc.style.display = 'none'; });
      positionDropdown();
      dropdown.style.display = 'block';
    }
  });

  ddConn.addEventListener('click', () => {
    dropdown.style.display = 'none';
    openTgAuth();
  });

  ddDisc.addEventListener('click', () => {
    dropdown.style.display = 'none';
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
          updateTgDot({ step: 'idle', session_exists: false });
          appendLog(d.ok ? '  Telegram disconnected.\n' : `  Disconnect error: ${d.error}\n`,
                    d.ok ? 'log-warn' : 'log-error');
        } catch(e) { appendLog(`  Disconnect failed: ${e}\n`, 'log-error'); }
      }
    });
  });

  // Close dropdown only if clicking outside of it
  document.addEventListener('click', (e) => {
    if (!dropdown.contains(e.target) && e.target !== btn) {
      dropdown.style.display = 'none';
    }
  });
})();

// Keyboard hint click handler: now opens keybinds panel
document.getElementById('kbd-hint')?.addEventListener('click', toggleKeybindsPanel);

// ═══════════════════════════════════════
//  ARTIST SEARCH MODAL
// ═══════════════════════════════════════
function _openArtistSearch() {
  document.getElementById('artist-search-overlay').classList.add('open');
  document.getElementById('artist-search-input').value = '';
  document.getElementById('artist-search-results').innerHTML =
    '<div class="search-status">Type an artist name to search.</div>';
  setTimeout(() => document.getElementById('artist-search-input').focus(), 60);
}

function _closeArtistSearch() {
  document.getElementById('artist-search-overlay').classList.remove('open');
}

async function _doArtistSearch() {
  const q = document.getElementById('artist-search-input').value.trim();
  if (!q) return;
  const results = document.getElementById('artist-search-results');
  results.innerHTML = '<div class="search-status">Searching…</div>';
  try {
    const resp = await fetch(`/artist-search?q=${encodeURIComponent(q)}`);
    const data = await resp.json();
    if (data.error || !data.data || !data.data.length) {
      results.innerHTML = `<div class="search-status">${data.error ? escHtml(data.error) : 'No artists found.'}</div>`;
      return;
    }
    results.innerHTML = '';
    data.data.forEach(artist => {
      const row = document.createElement('div');
      row.className = 'artist-result-row';
      const fans = artist.nb_fan >= 1_000_000
        ? `${(artist.nb_fan / 1_000_000).toFixed(1)}M fans`
        : artist.nb_fan >= 1_000
          ? `${Math.round(artist.nb_fan / 1_000)}K fans`
          : artist.nb_fan ? `${artist.nb_fan} fans` : '';
      const pic = artist.picture_small || '';
      row.innerHTML = `
        ${pic
          ? `<img class="artist-result-avatar" src="${escHtml(pic)}" alt="" loading="lazy">`
          : `<div class="artist-result-ph">${ICON.note}</div>`}
        <span class="artist-result-name">${escHtml(artist.name)}</span>
        ${fans ? `<span class="artist-result-fans">${fans}</span>` : ''}
      `;
      row.addEventListener('click', () => {
        _closeArtistSearch();
        const existing = _libArtistList.find(a => a.artist_id === String(artist.id));
        if (existing) { _selectLibArtist(existing, true); return; }
        const synth = {
          name:      artist.name,
          artist_id: String(artist.id),
          albums:    [],
          cover_url: artist.picture_medium || artist.picture_small || '',
          _meta: {
            id:             artist.id,
            name:           artist.name,
            nb_fan:         artist.nb_fan,
            nb_album:       artist.nb_album,
            picture_medium: artist.picture_medium || '',
            picture_big:    artist.picture_big    || '',
            picture_xl:     artist.picture_xl     || '',
          },
        };
        _libArtistList.push(synth);
        _libArtistList.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
        _renderLibArtistGrid();
        _selectLibArtist(synth, true);
      });
      results.appendChild(row);
    });
  } catch (e) {
    results.innerHTML = `<div class="search-status">Error: ${escHtml(String(e))}</div>`;
  }
}

{
  document.getElementById('btn-artist-search-go').addEventListener('click', _doArtistSearch);
  document.getElementById('btn-artist-search-cancel').addEventListener('click', _closeArtistSearch);
  document.getElementById('artist-search-overlay').addEventListener('click', e => {
    if (e.target === document.getElementById('artist-search-overlay')) _closeArtistSearch();
  });
  let _asDebounce = null;
  document.getElementById('artist-search-input').addEventListener('input', () => {
    clearTimeout(_asDebounce);
    if (!document.getElementById('artist-search-input').value.trim()) {
      document.getElementById('artist-search-results').innerHTML =
        '<div class="search-status">Type an artist name to search.</div>';
      return;
    }
    _asDebounce = setTimeout(_doArtistSearch, 380);
  });
  document.getElementById('artist-search-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') { clearTimeout(_asDebounce); _doArtistSearch(); }
    if (e.key === 'Escape') _closeArtistSearch();
  });
}

// ── Save-session modal wiring ────────────────────────────────────────────────
{
  const overlay = document.getElementById('save-session-overlay');
  document.getElementById('btn-sso-cancel')?.addEventListener('click', () =>
    overlay?.classList.remove('open'));
  document.getElementById('btn-sso-save')?.addEventListener('click', _commitSaveSession);
  document.getElementById('save-session-name')?.addEventListener('keydown', e => {
    if (e.key === 'Enter')  _commitSaveSession();
    if (e.key === 'Escape') overlay?.classList.remove('open');
  });
  overlay?.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.remove('open');
  });
}

// ── Volume icon SVG helpers ───────────────────────────────────────────────────
function _volIconFull() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
    <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
    <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
  </svg>`;
}
function _volIconMuted() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
    <line x1="23" y1="9" x2="17" y2="15"/>
    <line x1="17" y1="9" x2="23" y2="15"/>
  </svg>`;
}

// ── Volume slider initial gradient + icon ────────────────────────────────────
{
  const volRange = document.getElementById('mp-vol-range');
  const volIcon  = document.getElementById('mp-vol-icon');
  if (volRange) {
    const saved = parseFloat(localStorage.getItem('tgd_volume') ?? '0.5');
    volRange.value = saved;
    _mpAudio.volume = saved;
    volRange.style.setProperty('--vol-pct', (saved * 100).toFixed(1) + '%');
  }
  if (volIcon) volIcon.innerHTML = _volIconFull();
}

// ── Mini-player mode buttons + liked songs init ──────────────────────────────
_mpUpdateModeButtons();
_mpUpdateLikeBtn(false);
_loadLikedKeys();
_loadRatings();

// ── Play button centering class ───────────────────────────────────────────────
{
  const playBtn = document.getElementById('mp-play-btn');
  function _updatePlayClass(playing) {
    playBtn?.classList.toggle('is-play',  !playing);
    playBtn?.classList.toggle('is-pause',  playing);
  }
  _updatePlayClass(false);
  // Patch _mpUpdatePlayBtn to also update the class
  const _origMpUpdate = window._mpUpdatePlayBtn;
}

// ═══════════════════════════════════════
//  INIT
// ═══════════════════════════════════════
fetch('/config')
  .then(r => r.json())
  .then(cfg => {
    if (cfg.home_music_folder)
      document.getElementById('home-input').value = cfg.home_music_folder;
  })
  .catch(() => {});

loadWorkingQueue();
loadSessions();
connectWS();
renderQueue();
showWelcome();
loadManifestUrls();
checkTelegramStatus();
