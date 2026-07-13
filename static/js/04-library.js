//  LIBRARY ANALYTICS  (redesigned)
// ═══════════════════════════════════════

// ── State ─────────────────────────────────────────────────────────────────
let _libAllAlbums   = [];   // full album list from /library-albums
let _libQueuedAlbumIds = new Set(); // Deezer album IDs queued this session (persists across artist switches)
let _libArtistList  = [];   // [{name, artist_id, albums[], cover_url, _meta}]
let _libActiveArtist = null;
let _libActiveAlbum  = null;
let _libView         = 'albums';  // 'albums' | 'tracks'
let _watchedIds      = new Set(); // artist_ids on the new-release watchlist
let _watchedNames    = new Set(); // lowercased names (fallback match when id not yet known)
let _newReleases     = [];        // cached results from /watchlist-check

// ── Queue sync: grey out not-owned library cards when removed from queue ──
function _syncLibQueuedState() {
  const currentIds = new Set();
  entries.forEach(e => {
    const m = e.url.match(/\/album\/(\d+)/);
    if (m) currentIds.add(m[1]);
  });
  // IDs that were queued but are no longer present
  const removed = [..._libQueuedAlbumIds].filter(id => !currentIds.has(id));
  removed.forEach(id => {
    _libQueuedAlbumIds.delete(id);
    // Reset visible cards with this album id
    document.querySelectorAll(`.lib-album-card[data-album-id="${id}"]`).forEach(card => {
      const addBtn = card.querySelector('.lib-add-btn');
      card.classList.add('not-owned');
      if (addBtn) {
        addBtn.innerHTML = ICON.plus;
        addBtn.classList.remove('queued', 'popping');
        addBtn.title = 'Add to queue';
        addBtn.style.opacity = '';
        addBtn.style.pointerEvents = '';
      }
    });
  });
  // Ensure newly added IDs are reflected (cards added while library was open)
  currentIds.forEach(id => {
    if (!_libQueuedAlbumIds.has(id)) {
      document.querySelectorAll(`.lib-album-card[data-album-id="${id}"]`).forEach(card => {
        const addBtn = card.querySelector('.lib-add-btn');
        card.classList.remove('not-owned');
        if (addBtn) {
          addBtn.innerHTML = ICON.check;
          addBtn.classList.add('queued');
          addBtn.title = 'In queue — click to remove';
          addBtn.style.opacity = '1';
          addBtn.style.pointerEvents = '';   // stay clickable so it can be deselected
        }
      });
    }
  });
  _libQueuedAlbumIds = currentIds;
}

// ── Mini player state ──────────────────────────────────────────────────────
let _mpQueue     = [];
let _mpIdx       = -1;
let _mpPlaying   = false;
let _mpCurrentAlb = null;  // album that owns the current playback queue — never changes mid-queue
let _mpShuffle   = false;          // shuffle on/off
let _mpRepeat    = 'off';          // 'off' | 'all' | 'one'
let _mpShuffleOrder = [];          // remaining indices to play when shuffling
let _mpScrobbleState = null;       // { isLocal, scrobbled, meta } for current track
let _mpErrorStreak   = 0;          // consecutive failed-to-play tracks (auto-skip loop guard)
let _mpTriedTranscode = false;     // whether we've already retried the current track via /audio-stream
let _mpPreloadEl     = null;       // hidden <audio> used to warm the next track (near-gapless)
let _mpPreloadedIdx  = -1;
let _mpAutoplayRadio = true;       // continue with a Deezer-seeded radio when the queue ends
try { _mpAutoplayRadio = localStorage.getItem('tgdl_autoplay') !== '0'; } catch (_) {}

// When the queue ends, seed a "radio" of related-artist previews and keep playing.
async function _mpStartRadio() {
  const cur = _mpQueue[_mpIdx] || _mpQueue[_mpQueue.length - 1];
  const artist = _primaryArtist(cur?.artist || _mpCurrentAlb?.artist || '');
  if (!artist) return false;
  const aid = _mpCurrentAlb?.artist_id || cur?.artist_id || '';
  try {
    const r = await fetch(`/radio?artist=${encodeURIComponent(artist)}${aid ? `&artist_id=${encodeURIComponent(aid)}` : ''}`);
    if (!r.ok) return false;
    const d = await r.json();
    const tracks = (d.tracks || []).filter(t => t.preview_url);
    if (!tracks.length) return false;
    const radioAlb = { artist, album: `${artist} Radio`, is_radio: true };
    _mpQueue = tracks;
    _mpCurrentAlb = radioAlb;
    _toast(`Starting radio based on ${artist}.`, 'info');
    _mpPlayTrack(0, radioAlb);
    return true;
  } catch (_) { return false; }
}
let _likedKeys   = new Set();      // "path_hash\x00name" keys of liked songs
const _likedAlbum = { album: 'Liked Songs', artist: '', path_hash: '', cover_url: null, is_liked: true };
// `let` (not const): crossfade swaps the active element between the DOM
// <audio> and a second pooled Audio() — see 06-audio-fx.js.
let _mpAudio  = document.getElementById('mini-audio');
let _mpAudioB = null;              // crossfade partner element (lazy)

// ── Liked songs helpers ──────────────────────────────────────────────────────
function _likeKey(track, alb) {
  const ph   = track.path_hash || alb?.path_hash || '';
  const name = track.name || '';
  return ph + '\x00' + name;
}

function _isTrackLiked(track, alb) {
  return _likedKeys.has(_likeKey(track, alb));
}

async function _loadLikedKeys() {
  try {
    const r = await fetch('/liked-songs');
    const d = await r.json();
    _likedKeys = new Set((d.tracks || []).map(t => (t.path_hash || '') + '\x00' + (t.name || '')));
  } catch (e) { /* keep existing set */ }
}

async function _toggleLike(track, alb) {
  const ph   = track.path_hash || alb?.path_hash || '';
  const name = track.name || '';
  const entry = {
    path_hash: ph,
    name,
    title:     track.title || track.name || '',
    artist:    track.artist || alb?.artist || '',
    album:     alb?.album || track.album || '',
    cover_url: track.cover_url || alb?.cover_url || (ph ? `/cover/${ph}` : ''),
  };
  const key = ph + '\x00' + name;
  try {
    const r = await fetch('/toggle-like', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry),
    });
    const d = await r.json();
    if (d.liked) _likedKeys.add(key); else _likedKeys.delete(key);
    // If the Liked Songs view is currently open, refresh it
    if (_libActiveArtist?.is_liked_group) _selectLibArtist(_libActiveArtist);
    return d.liked;
  } catch (e) {
    return _likedKeys.has(key);
  }
}

// ── Star ratings ────────────────────────────────────────────────────
let _ratingsMap = new Map();       // "path_hash\x00name" → 1..5

async function _loadRatings() {
  try {
    const r = await fetch('/ratings');
    const d = await r.json();
    _ratingsMap = new Map(Object.entries(d.ratings || {}));
  } catch (e) { /* keep existing map */ }
}

function _trackRating(track, alb) {
  return _ratingsMap.get(_likeKey(track, alb)) || 0;
}

async function _setTrackRating(track, alb, rating) {
  const ph   = track.path_hash || alb?.path_hash || '';
  const name = track.name || '';
  if (!ph || !name) return false;
  const key = ph + '\x00' + name;
  try {
    const r = await fetch('/rate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path_hash: ph, name, rating,
        title:  track.title || track.name || '',
        artist: track.artist || alb?.artist || '',
        album:  alb?.album || track.album || '',
      }),
    });
    const d = await r.json();
    if (d.error) return false;
    if (rating > 0) _ratingsMap.set(key, rating); else _ratingsMap.delete(key);
    return true;
  } catch (e) { return false; }
}

// ── Helpers ────────────────────────────────────────────────────────────────
function _fmtFans(n) {
  if (!n) return '';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M fans`;
  if (n >= 1_000)     return `${Math.round(n / 1_000)}K fans`;
  return `${n} fans`;
}

// ── Skeleton helpers ──────────────────────────────────────────────────────
function _libSkeletonArtistRows(n = 8) {
  return Array.from({ length: n }, () => `
    <div class="skeleton-artist-row">
      <div class="skeleton skeleton-avatar"></div>
      <div class="skeleton-text-wrap">
        <div class="skeleton skeleton-line w60"></div>
        <div class="skeleton skeleton-line w35"></div>
      </div>
    </div>`).join('');
}

function _libSkeletonAlbumGrid(n = 6) {
  return `
    <div class="skeleton-album-grid-wrap">
      <div class="skeleton skeleton-section-bar"></div>
      <div class="skeleton-album-grid">
        ${Array.from({ length: n }, () => `
          <div class="skeleton-album-card">
            <div class="skeleton skeleton-cover"></div>
            <div class="skeleton-album-info">
              <div class="skeleton skeleton-line w80"></div>
              <div class="skeleton skeleton-line w50"></div>
            </div>
          </div>`).join('')}
      </div>
    </div>`;
}

// ── Load ───────────────────────────────────────────────────────────────────
async function loadLibrary(opts = {}) {
  const keepView = !!opts.keepView;   // refresh data but stay on current artist
  const content  = document.getElementById('lib-content');
  const artList  = document.getElementById('lib-artist-list');

  _loadWatchlist();   // refresh which artists show as "Watching" in heroes

  // When refreshing in place, remember the open artist + cached discographies
  // so newly-downloaded albums move into "In Your Library" without a full reset.
  const prevArtistName = keepView ? (_libActiveArtist?.name || null) : null;
  const cacheByName = {};
  if (keepView) (_libArtistList || []).forEach(a => {
    if (a && a.name) cacheByName[a.name] = { _discog: a._discog, _meta: a._meta };
  });

  if (!keepView) {
    content.innerHTML  = _libSkeletonAlbumGrid(8);
    artList.innerHTML  = _libSkeletonArtistRows(8);
    document.getElementById('lib-artist-hero').style.display = 'none';
    document.getElementById('lib-breadcrumb').innerHTML = '';
    const albFilter = document.getElementById('lib-album-filter');
    if (albFilter) albFilter.closest('#lib-album-tools').style.display = 'none';
  }

  try {
    const albumsResp  = await fetch('/library-albums');
    const albums      = await albumsResp.json();

    if (albums.error) {
      content.innerHTML  = `<div class="stat-empty" style="color:var(--red)">${escHtml(albums.error)}</div>`;
      artList.innerHTML  = '<div class="lib-artist-list-empty">Error loading library.</div>';
      return;
    }

    _libAllAlbums = albums;

    // Separate generated playlists from regular albums (Item 2)
    const playlistAlbums = albums.filter(a => a.is_playlist);
    const regularAlbums  = albums.filter(a => !a.is_playlist);

    // Build artist list (regular albums only)
    const artistMap = {};
    regularAlbums.forEach(alb => {
      const key = alb.artist;
      if (!artistMap[key]) {
        artistMap[key] = {
          name:      key,
          artist_id: alb.artist_id || '',
          albums:    [],
          cover_url: null,
        };
      }
      artistMap[key].albums.push(alb);
      if (!artistMap[key].cover_url && alb.cover_url) {
        artistMap[key].cover_url = alb.cover_url;
      }
      // Take first non-empty artist_id from any album — some albums may not have it
      if (!artistMap[key].artist_id && alb.artist_id) {
        artistMap[key].artist_id = alb.artist_id;
      }
    });

    _libArtistList = Object.values(artistMap).sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));

    // Pin a synthetic "Playlists" group at the top when any exist
    if (playlistAlbums.length) {
      _libArtistList.unshift({
        name:              'Playlists',
        artist_id:         '',
        albums:            playlistAlbums,
        cover_url:         playlistAlbums.find(p => p.cover_url)?.cover_url || null,
        is_playlist_group: true,
      });
    }

    // Pin a synthetic "Liked Songs" group at the very top (always present)
    _libArtistList.unshift({
      name:           'Liked Songs',
      artist_id:      '',
      albums:         [],
      cover_url:      null,
      is_liked_group: true,
    });

    // Pin a synthetic "Home" overview at the very top (above Liked Songs)
    _libArtistList.unshift({
      name:          'Home',
      artist_id:     '',
      albums:        [],
      cover_url:     null,
      is_home_group: true,
    });

    // Carry over cached discographies/meta so an in-place refresh doesn't
    // re-fetch them (and so not-owned albums keep rendering instantly).
    if (keepView) _libArtistList.forEach(a => {
      const c = cacheByName[a.name];
      if (c) { if (c._discog) a._discog = c._discog; if (c._meta) a._meta = c._meta; }
    });

    _renderLibArtistGrid();

    // Select the previously-open artist when refreshing, else the first one
    if (_libArtistList.length) {
      const target = (keepView && prevArtistName)
        ? _libArtistList.find(a => a.name === prevArtistName)
        : null;
      _selectLibArtist(target || _libArtistList[0], false);
    } else {
      content.innerHTML = '<div class="stat-empty">No albums found in library.</div>';
      artList.innerHTML = '<div class="lib-artist-list-empty">No artists found.</div>';
    }

  } catch (e) {
    content.innerHTML = `<div class="stat-empty" style="color:var(--red)">Error: ${escHtml(String(e))}</div>`;
  }
}

// ── Pinned items (artists + playlists) persisted in localStorage ───────────
let _libPins = (() => {
  try {
    const p = JSON.parse(localStorage.getItem('tgdl_lib_pins'));
    if (p && typeof p === 'object') return { artists: p.artists || [], playlists: p.playlists || [] };
  } catch (_) {}
  return { artists: [], playlists: [] };
})();

function _saveLibPins() {
  try { localStorage.setItem('tgdl_lib_pins', JSON.stringify(_libPins)); } catch (_) {}
}
function _isPinned(kind, name) { return (_libPins[kind] || []).includes(name); }
function _togglePin(kind, name) {
  const arr = _libPins[kind] || (_libPins[kind] = []);
  const i = arr.indexOf(name);
  if (i === -1) arr.push(name); else arr.splice(i, 1);
  _saveLibPins();
  _renderLibArtistGrid();
  // Re-apply the active-row highlight lost on re-render
  if (_libActiveArtist) {
    document.querySelectorAll('.lib-artist-row').forEach(c =>
      c.classList.toggle('active', c.dataset.artist === _libActiveArtist.name));
  }
  return i === -1;
}

// Open a pinned playlist: select the Playlists group, then open that album.
function _openPinnedPlaylist(plName) {
  const plAlb = (_libAllAlbums || []).find(a => a.is_playlist && a.album === plName);
  const group = _libArtistList.find(a => a.is_playlist_group);
  if (!plAlb || !group) { _toast('Playlist not found — it may have been removed.', 'error'); return; }
  Promise.resolve(_selectLibArtist(group, true)).then(() => _openLibAlbum(plAlb));
}

// Build the "Pinned" sidebar section (pinned playlists first, then artists).
function _appendPinnedSection(list) {
  const pinnedPls = (_libPins.playlists || [])
    .map(name => (_libAllAlbums || []).find(a => a.is_playlist && a.album === name))
    .filter(Boolean);
  const pinnedArtists = (_libPins.artists || [])
    .map(name => _libArtistList.find(a => a.name === name &&
      !a.is_home_group && !a.is_liked_group && !a.is_playlist_group))
    .filter(Boolean);
  if (!pinnedPls.length && !pinnedArtists.length) return;

  const div = document.createElement('div');
  div.className = 'lib-sidebar-divider';
  div.dataset.label = 'PINNED';
  list.appendChild(div);

  pinnedPls.forEach(pl => {
    const row = document.createElement('div');
    row.className = 'lib-artist-row lib-pinned-row';
    const cover = pl.cover_url || (pl.path_hash ? `/cover/${pl.path_hash}` : '');
    const av = cover
      ? `<img class="lib-artist-row-avatar" src="${escHtml(cover)}" alt="" loading="lazy"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
         <div class="lib-artist-row-ph" style="display:none">${ICON.playlist}</div>`
      : `<div class="lib-artist-row-ph">${ICON.playlist}</div>`;
    row.innerHTML = `
      ${av}
      <div class="lib-artist-row-info">
        <div class="lib-artist-row-name" title="${escHtml(pl.album)}">${escHtml(pl.album)}</div>
        <div class="lib-artist-row-count">Playlist</div>
      </div>
      <span class="lib-pin-badge" title="Unpin">${ICON.pinFilled}</span>`;
    row.addEventListener('click', (ev) => {
      if (ev.target.closest('.lib-pin-badge')) { _togglePin('playlists', pl.album); return; }
      _openPinnedPlaylist(pl.album);
    });
    list.appendChild(row);
  });

  pinnedArtists.forEach(artist => {
    const row = document.createElement('div');
    row.className = 'lib-artist-row lib-pinned-row';
    row.dataset.artist = artist.name;
    const av = artist.cover_url
      ? `<img class="lib-artist-row-avatar" src="${escHtml(artist.cover_url)}" alt="" loading="lazy"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
         <div class="lib-artist-row-ph" style="display:none">${ICON.note}</div>`
      : `<div class="lib-artist-row-ph">${ICON.note}</div>`;
    row.innerHTML = `
      ${av}
      <div class="lib-artist-row-info">
        <div class="lib-artist-row-name" title="${escHtml(artist.name)}">${escHtml(artist.name)}</div>
        <div class="lib-artist-row-count">Artist</div>
      </div>
      <span class="lib-pin-badge" title="Unpin">${ICON.pinFilled}</span>`;
    row.addEventListener('click', (ev) => {
      if (ev.target.closest('.lib-pin-badge')) { _togglePin('artists', artist.name); return; }
      _selectLibArtist(artist, true);
    });
    list.appendChild(row);
  });
}

// ── Artist sidebar list (replaces card grid) ───────────────────────────────
let _libArtistDragSrc = null;

function _renderLibArtistGrid() {
  const list = document.getElementById('lib-artist-list');
  if (!list) return;
  list.innerHTML = '';

  if (!_libArtistList.length) {
    list.innerHTML = '<div class="lib-artist-list-empty">No artists found.</div>';
    return;
  }

  _libArtistList.forEach((artist, idx) => {
    // Synthetic "Home" overview — distinct row, no drag / delete-artist
    if (artist.is_home_group) {
      const hrow = document.createElement('div');
      hrow.className = 'lib-artist-row lib-home-group';
      hrow.dataset.artist = artist.name;
      hrow.innerHTML = `
        <div class="lib-artist-row-ph lib-home-icon">${ICON.home}</div>
        <div class="lib-artist-row-info">
          <div class="lib-artist-row-name">Home</div>
          <div class="lib-artist-row-count">Overview</div>
        </div>`;
      hrow.addEventListener('click', () => _selectLibArtist(artist, true));
      list.appendChild(hrow);
      return;
    }

    // First regular artist → drop the pinned section, then the ARTISTS divider
    if (!artist.is_liked_group && !artist.is_playlist_group
        && !list.querySelector('[data-label="ARTISTS"]') && list.children.length) {
      _appendPinnedSection(list);
      const div = document.createElement('div');
      div.className = 'lib-sidebar-divider';
      div.dataset.label = 'ARTISTS';
      list.appendChild(div);
    }

    // Synthetic "Liked Songs" group — distinct row, no drag / delete-artist
    if (artist.is_liked_group) {
      const lrow = document.createElement('div');
      lrow.className = 'lib-artist-row lib-liked-group';
      lrow.dataset.artist = artist.name;
      lrow.innerHTML = `
        <div class="lib-artist-row-ph lib-liked-icon ico" data-icon="heartFilled"></div>
        <div class="lib-artist-row-info">
          <div class="lib-artist-row-name">Liked Songs</div>
          <div class="lib-artist-row-count">Your favorites</div>
        </div>`;
      lrow.addEventListener('click', () => _selectLibArtist(artist, true));
      list.appendChild(lrow);
      _applyIcons(lrow);
      return;
    }

    // Synthetic "Playlists" group — distinct row, no drag / delete-artist (Item 2)
    if (artist.is_playlist_group) {
      const prow = document.createElement('div');
      prow.className = 'lib-artist-row lib-playlist-group';
      prow.dataset.artist = artist.name;
      const n = artist.albums.length;
      prow.innerHTML = `
        <div class="lib-artist-row-ph lib-playlist-icon">${ICON.playlist}</div>
        <div class="lib-artist-row-info">
          <div class="lib-artist-row-name">Playlists</div>
          <div class="lib-artist-row-count">${n} playlist${n !== 1 ? 's' : ''}</div>
        </div>`;
      prow.addEventListener('click', () => _selectLibArtist(artist, true));
      list.appendChild(prow);
      return;
    }

    // Pinned artists are already shown in the pinned section above — don't
    // also list them again under ARTISTS.
    if (_isPinned('artists', artist.name)) return;

    const row = document.createElement('div');
    row.className = 'lib-artist-row';
    row.dataset.artist = artist.name;
    row.dataset.dIdx   = idx;
    row.draggable = true;

    const avatarHtml = artist.cover_url
      ? `<img class="lib-artist-row-avatar" src="${escHtml(artist.cover_url)}" alt="" loading="lazy"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
        + `<div class="lib-artist-row-ph" style="display:none">${ICON.note}</div>`
      : `<div class="lib-artist-row-ph">${ICON.note}</div>`;

    const albumCount = artist.albums.length;
    const watchStar  = _isWatched(artist)
      ? '<span class="watch-star" title="Watching for new releases">★</span>' : '';
    row.innerHTML = `
      ${avatarHtml}
      <div class="lib-artist-row-info">
        <div class="lib-artist-row-name" title="${escHtml(artist.name)}"><span class="lib-artist-name-text">${escHtml(artist.name)}</span>${watchStar}</div>
        <div class="lib-artist-row-count">${albumCount} album${albumCount !== 1 ? 's' : ''}</div>
      </div>
      <button class="kebab-btn" title="More options">⋯</button>
    `;

    // Select on click (but not if kebab was clicked)
    row.addEventListener('click', (ev) => {
      if (ev.target.classList.contains('kebab-btn')) return;
      _selectLibArtist(artist, true);
    });

    // Build the artist row's context-menu items (shared by ⋯ and right-click)
    const _artistMenuItems = () => {
      const _pinned = _isPinned('artists', artist.name);
      return [
        { label: 'Open', icon: ICON.note, action: () => _selectLibArtist(artist, true) },
        { label: _pinned ? 'Unpin from sidebar' : 'Pin to sidebar',
          icon: _pinned ? ICON.pinFilled : ICON.pin, action: () => {
            const nowPinned = _togglePin('artists', artist.name);
            _toast(nowPinned ? `Pinned ${artist.name}.` : `Unpinned ${artist.name}.`, 'success');
          }},
        { divider: true },
        { label: 'Delete artist', icon: ICON.trash, danger: true, action: () => {
          showDeleteConfirm({
            title: 'Delete Artist',
            msg:   `All local files for this artist will be permanently removed from disk.`,
            detail: artist.name,
            onConfirm: () => {
          fetch('/config').then(r => r.json()).then(cfg => {
            const home = cfg.home_music_folder;
            if (!home) { appendLog('  No home music folder configured.\n', 'log-error'); return; }
            const idx2 = _libArtistList.indexOf(artist);
            if (idx2 !== -1) _libArtistList.splice(idx2, 1);
            if (_libActiveArtist === artist) {
              _libActiveArtist = _libArtistList[0] || null;
              document.getElementById('lib-artist-hero').style.display = 'none';
              document.getElementById('lib-content').innerHTML = '<div class="stat-empty">Artist deleted.</div>';
              document.getElementById('lib-breadcrumb').innerHTML = '';
            }
            _renderLibArtistGrid();
            artist.albums.forEach(alb => {
              if (alb.deezer_url) {
                fetch('/history-remove', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ url: alb.deezer_url }) });
              }
            });
            fetch('/delete-artist', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ artist: artist.name }) })
              .then(r => r.json())
              .then(d => {
                appendLog(d.ok ? `  Deleted artist: ${artist.name}\n` : `  Delete error: ${d.error}\n`,
                  d.ok ? 'log-warn' : 'log-error');
                // Re-scan so the library reflects what's actually on disk
                _libStatsCache = null;
                if (activeTab === 'library') loadLibrary();
              })
              .catch(() => appendLog(`  Delete request failed\n`, 'log-warn'));
          });
            }
          });
        }},
      ];
    };

    // Kebab button → menu anchored to the button
    row.querySelector('.kebab-btn').addEventListener('click', (ev) => {
      ev.stopPropagation();
      openKebabMenu(ev.currentTarget, _artistMenuItems());
    });

    // Right-click anywhere on the row → same menu at the cursor
    row.addEventListener('contextmenu', (ev) => {
      ev.preventDefault();
      openKebabMenu(row, _artistMenuItems(), { atPoint: { x: ev.clientX, y: ev.clientY } });
    });

    // Drag to reorder — insert before/after based on cursor Y position
    row.addEventListener('dragstart', ev => {
      _libArtistDragSrc = idx;
      ev.dataTransfer.effectAllowed = 'move';
      requestAnimationFrame(() => row.classList.add('dragging-artist'));
    });
    row.addEventListener('dragover', ev => {
      ev.preventDefault(); ev.dataTransfer.dropEffect = 'move';
      list.querySelectorAll('.lib-artist-row').forEach(c =>
        c.classList.remove('drag-insert-before', 'drag-insert-after'));
      if (idx !== _libArtistDragSrc) {
        const rect = row.getBoundingClientRect();
        row.classList.add(ev.clientY < rect.top + rect.height / 2 ? 'drag-insert-before' : 'drag-insert-after');
      }
    });
    row.addEventListener('dragleave', ev => {
      if (!row.contains(ev.relatedTarget))
        row.classList.remove('drag-insert-before', 'drag-insert-after');
    });
    row.addEventListener('drop', ev => {
      ev.preventDefault();
      const isBefore = row.classList.contains('drag-insert-before');
      row.classList.remove('drag-insert-before', 'drag-insert-after');
      if (_libArtistDragSrc === null || _libArtistDragSrc === idx) return;
      const [moved] = _libArtistList.splice(_libArtistDragSrc, 1);
      let targetIdx = idx;
      if (_libArtistDragSrc < idx) targetIdx = idx - 1;
      _libArtistList.splice(isBefore ? targetIdx : targetIdx + 1, 0, moved);
      _libArtistDragSrc = null;
      _renderLibArtistGrid();
      if (_libActiveArtist) {
        document.querySelectorAll('.lib-artist-row').forEach(c =>
          c.classList.toggle('active', c.dataset.artist === _libActiveArtist.name));
      }
    });
    row.addEventListener('dragend', () => {
      _libArtistDragSrc = null;
      list.querySelectorAll('.lib-artist-row').forEach(c =>
        c.classList.remove('dragging-artist', 'drag-insert-before', 'drag-insert-after'));
    });

    list.appendChild(row);
  });

  // If there were no regular artists, the pinned section never got appended
  // above — add it now so pinned playlists still show.
  if (!list.querySelector('[data-label="ARTISTS"]')) _appendPinnedSection(list);

  // Pre-fetch artist photos for all artists that don't have one yet
  _prefetchArtistAvatars();
}

// ── Shared helper: inject or update the avatar img on an artist row ──────
function _updateArtistCardAvatar(artistName, picUrl) {
  const row = document.querySelector(`.lib-artist-row[data-artist="${CSS.escape(artistName)}"]`);
  if (!row) return;
  let img = row.querySelector('.lib-artist-row-avatar');
  const ph = row.querySelector('.lib-artist-row-ph');
  if (!img) {
    img = document.createElement('img');
    img.className = 'lib-artist-row-avatar';
    img.alt = '';
    if (ph) row.insertBefore(img, ph);
  }
  img.src = picUrl;
  img.style.display = '';
  if (ph) ph.style.display = 'none';
  // Mirror to active hero avatar if this artist is selected
  const heroAv = document.getElementById('lib-hero-avatar');
  const heroPh = document.getElementById('lib-hero-ph');
  if (heroAv && _libActiveArtist?.name === artistName) {
    heroAv.src = picUrl; heroAv.style.display = 'block';
    if (heroPh) heroPh.style.display = 'none';
  }
}

async function _prefetchArtistAvatars() {
  // Fire all artist metadata fetches concurrently for maximum speed.
  // Skip the pinned "Liked Songs" / "Playlists" groups — they keep their own
  // icons and must never be overwritten with a Deezer artist-search image.
  const artists = _libArtistList.filter(a =>
    !a._meta && !a.is_liked_group && !a.is_playlist_group && !a.is_home_group);
  if (!artists.length) return;

  // Build the fetch promise for one artist
  async function _fetchOneMeta(artist) {
    try {
      let meta = null;

      if (artist.artist_id) {
        // Fast path: direct ID lookup
        const resp = await fetch(`/artist-meta/${artist.artist_id}`);
        const data = await resp.json();
        if (!data.error) meta = data;
      }

      if (!meta) {
        // Fallback: search Deezer by name
        const resp = await fetch(`/artist-search?q=${encodeURIComponent(artist.name)}`);
        const data = await resp.json();
        if (data.data && data.data.length) {
          meta = data.data[0];
          if (!artist.artist_id && meta.id) {
            artist.artist_id = String(meta.id);
          }
        }
      }

      if (!meta) return;
      artist._meta = meta;

      const pic = meta.picture_medium || meta.picture_big || meta.picture_small || '';
      if (pic) {
        artist.cover_url = pic;
        _updateArtistCardAvatar(artist.name, pic);
        if (_libActiveArtist === artist) _renderLibHero(artist);
      }
    } catch (_) {}
  }

  // Run in parallel — browsers handle concurrency limits automatically
  await Promise.all(artists.map(a => _fetchOneMeta(a)));
}

// ── Select artist ──────────────────────────────────────────────────────────
async function _selectLibArtist(artist, scroll) {
  _libActiveArtist = artist;
  _libActiveAlbum  = null;
  _libView         = 'albums';

  // Highlight row in sidebar
  document.querySelectorAll('.lib-artist-row').forEach(c =>
    c.classList.toggle('active', c.dataset.artist === artist.name));

  if (scroll) {
    const activeRow = document.querySelector('.lib-artist-row.active');
    if (activeRow) activeRow.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // Home overview — its own dashboard view, no album grid / discography
  if (artist.is_home_group) {
    _libView = 'home';
    // Home overview has no album grid — hide the whole sort/search toolbar group.
    const af = document.getElementById('lib-album-filter');
    if (af) af.closest('#lib-album-tools').style.display = 'none';
    document.getElementById('lib-artist-hero').style.display = 'none';
    const bc = document.getElementById('lib-breadcrumb');
    if (bc) bc.innerHTML = '';
    document.getElementById('lib-artist-sidebar')?.classList.remove('sidebar-hidden');
    await _renderHomeView();
    return;
  }

  // Show album filter
  const albFilter = document.getElementById('lib-album-filter');
  if (albFilter) { albFilter.closest('#lib-album-tools').style.display = 'flex'; albFilter.value = ''; }

  // Apply filter to local albums
  const filter = (albFilter?.value || '').trim().toLowerCase();
  const localVisible = filter
    ? artist.albums.filter(a =>
        a.album.toLowerCase().includes(filter) || a.artist.toLowerCase().includes(filter))
    : artist.albums;

  // Playlists / Liked Songs are local-only — never fetch or show a Deezer
  // discography (so no "Loading full discography…" placeholder appears).
  const _localOnly = artist.is_playlist_group || artist.is_liked_group;

  // Liked Songs: render the saved tracks directly. The track-list header
  // already shows "Liked Songs", so suppress the artist hero (avoids the
  // duplicate title + the meaningless 0/—/— album stats).
  if (artist.is_liked_group) {
    _libView = 'tracks';
    document.getElementById('lib-artist-hero').style.display = 'none';
    const bc = document.getElementById('lib-breadcrumb');
    if (bc) bc.innerHTML = '';
    document.getElementById('lib-artist-sidebar')?.classList.remove('sidebar-hidden');
    if (albFilter) albFilter.closest('#lib-album-tools').style.display = 'none';
    await _renderLikedSongs(artist);
    return;
  }

  _renderLibHero(artist);
  _renderLibBreadcrumb();

  _renderLibAlbums(localVisible, _localOnly ? [] : (artist._discog || null));

  if (_localOnly) return;

  // Background fetch of artist meta + biography
  if (!artist._meta) {
    try {
      let meta = null;

      if (artist.artist_id) {
        const resp = await fetch(`/artist-meta/${artist.artist_id}`);
        const data = await resp.json();
        if (!data.error) meta = data;
      }

      if (!meta) {
        // No ID yet — search by name (same fallback as _prefetchArtistAvatars)
        const resp = await fetch(`/artist-search?q=${encodeURIComponent(artist.name)}`);
        const data = await resp.json();
        if (data.data && data.data.length) {
          meta = data.data[0];
          if (!artist.artist_id && meta.id) artist.artist_id = String(meta.id);
        }
      }

      if (meta) {
        artist._meta = meta;
        const pic = meta.picture_medium || meta.picture_big || meta.picture_small || '';
        if (pic && pic !== artist.cover_url) {
          artist.cover_url = pic;
          _updateArtistCardAvatar(artist.name, pic);
        }
        if (_libActiveArtist === artist) _renderLibHero(artist);
      }
    } catch (_) {}
  }

  // Background fetch of full discography (so we can show not-owned albums)
  if (artist.artist_id && !artist._discog) {
    try {
      const resp = await fetch(`/artist-albums/${artist.artist_id}`);
      const data = await resp.json();
      if (data.data) {
        artist._discog = data.data;
        if (_libActiveArtist === artist && _libView === 'albums') {
          const f = (document.getElementById('lib-album-filter')?.value || '').trim().toLowerCase();
          const vis = f
            ? artist.albums.filter(a => a.album.toLowerCase().includes(f))
            : artist.albums;
          _renderLibAlbums(vis, artist._discog);
          _renderLibHero(artist); // update stats with discog count
        }
      }
    } catch (_) {}
  } else if (artist._discog && _libActiveArtist === artist) {
    // Already cached — re-render with discog immediately
    const f = (document.getElementById('lib-album-filter')?.value || '').trim().toLowerCase();
    const vis = f
      ? artist.albums.filter(a => a.album.toLowerCase().includes(f))
      : artist.albums;
    _renderLibAlbums(vis, artist._discog);
  }
}

// ── Hero ───────────────────────────────────────────────────────────────────
// ── Artist watchlist / new-release radar ──────────────────────────────────
async function _loadWatchlist() {
  try {
    const r = await fetch('/watchlist');
    const d = await r.json();
    _watchedIds   = new Set((d.artists || []).map(a => String(a.artist_id)));
    _watchedNames = new Set((d.artists || []).map(a => (a.name || '').toLowerCase()));
    _refreshWatchStars();
  } catch (_) {}
}

// True if this library artist is on the new-release watchlist
function _isWatched(artist) {
  if (!artist) return false;
  const aid = String(artist.artist_id || '');
  return (aid && _watchedIds.has(aid)) || _watchedNames.has((artist.name || '').toLowerCase());
}

// Sync the ★ markers on the artist sidebar rows with the current watchlist
function _refreshWatchStars() {
  (_libArtistList || []).forEach(a => {
    if (a.is_home_group || a.is_liked_group || a.is_playlist_group) return;
    const row = document.querySelector(`.lib-artist-row[data-artist="${(a.name || '').replace(/"/g, '\\"')}"]`);
    if (!row) return;
    const nameEl = row.querySelector('.lib-artist-row-name');
    if (!nameEl) return;
    const has = !!nameEl.querySelector('.watch-star');
    const want = _isWatched(a);
    if (want && !has) {
      const s = document.createElement('span');
      s.className = 'watch-star'; s.title = 'Watching for new releases'; s.textContent = '★';
      nameEl.appendChild(s);
    } else if (!want && has) {
      nameEl.querySelector('.watch-star').remove();
    }
  });
}

// Show a "Download missing (N)" button when the artist has not-yet-downloaded
// albums in its Deezer discography; queues them all in one click.
function _updateDlMissingBtn(artist) {
  const btn = document.getElementById('lib-dl-missing-btn');
  if (!btn) return;
  const missing = (artist && artist._notOwned) || [];
  const showable = artist && !artist.is_playlist_group && !artist.is_liked_group
    && !artist.is_home_group && missing.length > 0;
  if (!showable) { btn.style.display = 'none'; return; }
  btn.style.display = '';
  btn.disabled = false;
  btn.textContent = `⬇ Download missing (${missing.length})`;
  btn.onclick = () => {
    const albums = (artist._notOwned || []);
    if (!albums.length) return;
    let added = 0;
    albums.forEach(dAlb => {
      if (!dAlb.id) return;
      entries.push({
        url: `https://www.deezer.com/album/${dAlb.id}`,
        artist: artist.name || '',
        albumTitle: dAlb.title || '',
        coverUrl: dAlb.cover_medium || dAlb.cover_small || null,
        nbTracks: dAlb.nb_tracks || null,
        isPlaylist: false, playlistName: '',
      });
      added++;
    });
    renderQueue();
    btn.disabled = true;
    btn.textContent = `✓ Added ${added} to queue`;
    _toast(`Queued ${added} missing album${added !== 1 ? 's' : ''} by ${artist.name}. Open the Log tab and hit Run.`, 'success');
    appendLog(`Queued ${added} missing album(s) for ${artist.name}.\n`, 'log-ok');
  };
}

function _updateWatchBtn(artist) {
  const btn = document.getElementById('lib-watch-btn');
  if (!btn) return;
  // Only real Deezer-backed artists can be watched
  const showable = artist && artist.artist_id &&
    !artist.is_playlist_group && !artist.is_liked_group && !artist.is_home_group;
  if (!showable) { btn.style.display = 'none'; return; }
  const aid = String(artist.artist_id);
  const watching = _watchedIds.has(aid);
  btn.style.display = '';
  btn.classList.toggle('watching', watching);
  btn.textContent = watching ? '★ Watching' : '★ Watch artist';
  btn.onclick = () => _toggleWatch(artist);
}

async function _toggleWatch(artist) {
  const aid = String(artist.artist_id || '');
  if (!aid) return;
  const watching = _watchedIds.has(aid);
  const btn = document.getElementById('lib-watch-btn');
  if (btn) { btn.disabled = true; btn.textContent = watching ? 'Removing…' : 'Adding…'; }
  try {
    const r = await fetch('/watchlist', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: watching ? 'remove' : 'add',
        artist_id: aid, name: artist.name,
        cover_url: artist.cover_url || artist._meta?.picture_medium || '',
      }),
    });
    const d = await r.json();
    if (!d.error) {
      const lname = (artist.name || '').toLowerCase();
      if (watching) { _watchedIds.delete(aid); _watchedNames.delete(lname); }
      else          { _watchedIds.add(aid);    _watchedNames.add(lname); }
      if (!watching) appendLog(`Watching ${artist.name} for new releases.\n`, 'log-ok');
      _refreshWatchStars();
    }
  } catch (_) {}
  if (btn) btn.disabled = false;
  _updateWatchBtn(artist);
}

function _updateReleasesBadge() {
  const badge = document.getElementById('home-releases-count');
  if (!badge) return;
  const n = _newReleases.length;
  badge.textContent = n;
  badge.style.display = n ? '' : 'none';
}

async function checkReleases(manual) {
  if (!_watchedIds.size && !manual) return;   // nothing to check on silent runs
  const el = document.getElementById('home-releases');
  if (manual && el) el.innerHTML = '<div class="stat-empty" style="padding:14px 0">Checking…</div>';
  try {
    const r = await fetch('/watchlist-check');
    const d = await r.json();
    _newReleases = d.new_releases || [];
    _updateReleasesBadge();
    renderReleases();
    if (_newReleases.length && typeof _notify === 'function') {
      _notify('New releases available', `${_newReleases.length} new album${_newReleases.length === 1 ? '' : 's'} from artists you watch`);
    }
  } catch (_) {
    if (el) el.innerHTML = '<div class="stat-empty" style="padding:14px 0;color:var(--red)">Check failed.</div>';
  }
}

function renderReleases() {
  const el = document.getElementById('home-releases');
  if (!el) return;   // only present on the Library Home view
  _updateReleasesBadge();
  if (!_newReleases.length) {
    el.innerHTML = `<div class="stat-empty" style="padding:14px 0">${_watchedIds.size
      ? 'No new releases from your watched artists. Hit “Check now” to refresh.'
      : 'Watch an artist from their page (★) to track their new releases here.'}</div>`;
    return;
  }
  el.innerHTML = _newReleases.map((rel, i) => {
    const cover = rel.cover || '';
    const date  = rel.release_date ? ` · ${rel.release_date}` : '';
    return `<div class="release-row" style="display:flex;align-items:center;gap:10px;padding:8px;border:1px solid var(--border);border-radius:6px;margin-bottom:8px;background:var(--bg2)">
      ${cover ? `<img src="${escHtml(cover)}" style="width:46px;height:46px;border-radius:4px;flex-shrink:0;object-fit:cover">`
              : `<div style="width:46px;height:46px;border-radius:4px;flex-shrink:0;background:var(--bg4)"></div>`}
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(rel.title || '')}</div>
        <div style="font-size:11px;color:var(--fg3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(rel.artist || '')}${escHtml(date)}</div>
      </div>
      <button class="btn-secondary" style="width:auto;flex-shrink:0" onclick="_addReleaseToQueue(${i})">Add to queue</button>
      <button class="btn-icon" style="flex-shrink:0" title="Dismiss" onclick="_dismissRelease(${i})">✕</button>
    </div>`;
  }).join('');
}

function _addReleaseToQueue(i) {
  const rel = _newReleases[i];
  if (!rel) return;
  entries.push({
    url: rel.link || `https://www.deezer.com/album/${rel.album_id}`,
    artist: rel.artist || '', albumTitle: rel.title || '',
    coverUrl: rel.cover || null, nbTracks: null,
  });
  renderQueue();
  _markReleaseSeen(rel);
  _newReleases.splice(i, 1);
  _updateReleasesBadge();
  renderReleases();
  appendLog(`Added new release “${rel.title}” to the queue.\n`, 'log-ok');
}

function _dismissRelease(i) {
  const rel = _newReleases[i];
  if (!rel) return;
  _markReleaseSeen(rel);
  _newReleases.splice(i, 1);
  _updateReleasesBadge();
  renderReleases();
}

function _markReleaseSeen(rel) {
  fetch('/watchlist', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'mark_seen', artist_id: rel.artist_id, album_ids: [rel.album_id] }),
  }).catch(() => {});
}

// ── Maintenance: integrity scan + duplicate finder ────────────────────────
let _lastScanCorrupt = [];

// Re-queue the albums that contain corrupt/unreadable files so the user can
// re-download fresh copies (which overwrite the broken ones in place).
function _recoverCorrupt() {
  if (!_lastScanCorrupt.length) return;
  const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  // Corrupt paths look like "<Artists>/<Artist>/<Album>/<file>"
  const wantAlbums = new Map();   // key artist|album → {artist, album}
  _lastScanCorrupt.forEach(p => {
    const parts = String(p).split(/[\\/]/);
    if (parts.length >= 4) {
      const artist = parts[parts.length - 3];
      const album  = parts[parts.length - 2];
      wantAlbums.set(norm(artist) + '|' + norm(album), { artist, album });
    }
  });
  let added = 0, missing = 0;
  wantAlbums.forEach(({ artist, album }) => {
    const alb = (_libAllAlbums || []).find(a =>
      !a.is_playlist && norm(a.artist) === norm(artist) && norm(a.album) === norm(album));
    const albId = alb && (alb._album_id || (alb.deezer_url && (alb.deezer_url.match(/\/album\/(\d+)/) || [])[1]));
    const url = alb && (alb.deezer_url || (albId ? `https://www.deezer.com/album/${albId}` : ''));
    if (url) {
      entries.push({ url, artist: alb.artist, albumTitle: alb.album,
        coverUrl: alb.cover_url || null, nbTracks: alb.track_count || null });
      added++;
    } else { missing++; }
  });
  if (added) {
    renderQueue();
    _toast(`Queued ${added} album${added !== 1 ? 's' : ''} to re-download. Open the Log tab and hit Run.`, 'success');
    appendLog(`Re-downloading ${added} album(s) with corrupt files.\n`, 'log-ok');
  }
  if (missing) _toast(`${missing} affected album(s) had no Deezer link to re-download.`, 'info');
}

async function runDiagnostics() {
  const el  = document.getElementById('health-results');
  const btn = document.getElementById('btn-run-health');
  el.innerHTML = '<div style="font-family:var(--mono);font-size:10px;color:var(--fg3)">Checking…</div>';
  if (btn) btn.disabled = true;
  try {
    const d = await (await fetch('/health')).json();
    const rows = (d.checks || []).map(c => `
      <div class="stat-row" style="margin-top:6px;align-items:flex-start">
        <span style="color:${c.ok ? 'var(--accent)' : 'var(--red)'}">${c.ok ? '✓' : '✕'} ${escHtml(c.label)}</span>
        <span style="color:var(--fg3);font-family:var(--mono);font-size:10px;text-align:right;max-width:60%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(c.detail)}">${escHtml(c.detail)}</span>
      </div>`).join('');
    const banner = d.ok
      ? '<div class="stat-row" style="color:var(--accent)">✓ Everything looks good.</div>'
      : '<div class="stat-row" style="color:var(--yellow)">Some checks need attention:</div>';
    el.innerHTML = banner + rows;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--red);font-size:11px">Diagnostics failed: ${escHtml(String(e))}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runIntegrityScan() {
  const el  = document.getElementById('scan-results');
  const btn = document.getElementById('btn-run-scan');
  el.innerHTML = '<div style="font-family:var(--mono);font-size:10px;color:var(--fg3)">Scanning…</div>';
  if (btn) btn.disabled = true;
  try {
    const d = await (await fetch('/library-scan')).json();
    if (d.error) { el.innerHTML = `<div style="color:var(--red);font-size:11px">${escHtml(d.error)}</div>`; return; }
    const section = (title, items, color) => items.length
      ? `<div class="stat-row" style="margin-top:6px"><span style="color:${color}">${title}</span><strong>${items.length}</strong></div>
         <div style="max-height:160px;overflow-y:auto;font-family:var(--mono);font-size:10px;color:var(--fg3)">${
           items.map(p => `<div style="padding:1px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(p)}">${escHtml(p)}</div>`).join('')}</div>`
      : '';
    const clean = !d.corrupt.length && !d.missing_cover.length;
    _lastScanCorrupt = d.corrupt || [];
    const recoverBtn = d.corrupt.length
      ? `<button class="btn-secondary" style="width:auto;margin-top:8px" onclick="_recoverCorrupt()">↻ Re-download affected albums</button>`
      : '';
    el.innerHTML = `
      <div class="stat-row"><span>Albums scanned</span><strong>${d.scanned_albums.toLocaleString()}</strong></div>
      <div class="stat-row"><span>Tracks scanned</span><strong>${d.scanned_files.toLocaleString()}</strong></div>
      ${clean ? '<div class="stat-row" style="margin-top:6px;color:var(--accent)">✓ No issues found.</div>' : ''}
      ${section('Corrupt / unreadable', d.corrupt, 'var(--red)')}
      ${recoverBtn}
      ${section('Missing cover art', d.missing_cover, 'var(--yellow)')}`;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--red);font-size:11px">Scan failed: ${escHtml(String(e))}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function findDuplicates() {
  const el  = document.getElementById('dupes-results');
  const btn = document.getElementById('btn-find-dupes');
  el.innerHTML = '<div style="font-family:var(--mono);font-size:10px;color:var(--fg3)">Hashing library…</div>';
  if (btn) btn.disabled = true;
  try {
    const d = await (await fetch('/duplicates')).json();
    if (d.error) { el.innerHTML = `<div style="color:var(--red);font-size:11px">${escHtml(d.error)}</div>`; return; }
    if (!d.groups.length) { el.innerHTML = '<div class="stat-row" style="color:var(--accent)">✓ No duplicates found.</div>'; return; }
    renderDupes(d);
  } catch (e) {
    el.innerHTML = `<div style="color:var(--red);font-size:11px">Failed: ${escHtml(String(e))}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

let _dupeData = null;
function renderDupes(d) {
  _dupeData = d;
  const el = document.getElementById('dupes-results');
  if (!d.groups.length) { el.innerHTML = '<div class="stat-row" style="color:var(--accent)">✓ No duplicates remaining.</div>'; return; }
  el.innerHTML = `<div class="stat-row"><span>Duplicate groups</span><strong>${d.groups.length}</strong></div>
    <div class="stat-row"><span>Reclaimable</span><strong style="color:var(--accent)">${_fmtBytes(d.wasted_bytes)}</strong></div>
    <div style="margin:6px 0"><button class="btn-secondary" id="btn-dedupe-auto" style="width:auto">Keep best, trash the rest</button></div>` +
    d.groups.map((g, gi) => `
      <div style="border:1px solid var(--border);border-radius:5px;padding:6px 8px;margin-top:8px">
        <div style="font-size:10px;color:var(--fg3);margin-bottom:4px">${g.files.length} copies · ${_fmtBytes(g.size)} each</div>
        ${g.files.map((f, fi) => `
          <div style="display:flex;align-items:center;gap:8px;padding:2px 0">
            ${f.best ? '<span title="Best quality — kept" style="flex-shrink:0;color:var(--accent);font-size:9px;font-weight:600">KEEP</span>' : '<span style="flex-shrink:0;width:30px"></span>'}
            <span style="flex:1;min-width:0;font-family:var(--mono);font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(f.rel)}">${escHtml(f.rel)}</span>
            <span style="flex-shrink:0;font-size:9px;color:var(--fg3);text-transform:uppercase">${escHtml(f.ext || '')}</span>
            <button class="btn-icon" style="flex-shrink:0" title="Delete this copy" onclick="_deleteDupe(${gi},${fi})">✕</button>
          </div>`).join('')}
      </div>`).join('');
  document.getElementById('btn-dedupe-auto')?.addEventListener('click', async () => {
    const ok = await _confirm('Trash every duplicate except the best-quality copy in each group?', { title: 'Auto de-duplicate', confirmLabel: 'Keep best', danger: true });
    if (!ok) return;
    try {
      const r = await (await fetch('/dedupe-auto', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
      if (r.error) { _toast(r.error, 'error'); return; }
      _toast(`Moved ${r.trashed} duplicate(s) to the recycle bin.`, 'success');
      findDuplicates();
    } catch (e) { _toast(String(e), 'error'); }
  });
}

async function _deleteDupe(gi, fi) {
  const grp = _dupeData?.groups[gi];
  const file = grp?.files[fi];
  if (!file) return;
  const ok = await _confirm(`Delete this copy?\n\n${file.rel}`, { title: 'Delete duplicate', confirmLabel: 'Delete', danger: true });
  if (!ok) return;
  try {
    const d = await (await fetch('/delete-file', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: file.path }),
    })).json();
    if (d.error) { appendLog(`Delete failed: ${d.error}\n`, 'log-error'); return; }
    grp.files.splice(fi, 1);
    if (grp.files.length < 2) _dupeData.groups.splice(gi, 1);
    _dupeData.wasted_bytes = _dupeData.groups.reduce((s, g) => s + g.size * (g.files.length - 1), 0);
    renderDupes(_dupeData);
    appendLog(`Deleted duplicate: ${file.rel}\n`, 'log-ok');
  } catch (e) {
    appendLog(`Delete failed: ${e}\n`, 'log-error');
  }
}

function _renderLibHero(artist) {
  const hero = document.getElementById('lib-artist-hero');
  hero.style.display = 'block';
  const isPlaylists = !!artist.is_playlist_group;
  hero.classList.toggle('is-playlists', isPlaylists);

  const meta      = artist._meta || {};
  const bgUrl     = meta.picture_xl || meta.picture_big || artist.cover_url || '';
  const avatarUrl = meta.picture_medium || meta.picture_big || artist.cover_url || '';
  const fans      = _fmtFans(meta.nb_fan);
  const localAlbs = artist.albums.length;
  const totalAlbs = meta.nb_album != null ? meta.nb_album : null;

  // About blurb — strip HTML tags from Deezer biography if present
  const rawBio    = meta.biography || '';
  const cleanBio  = rawBio.replace(/<[^>]*>/g, ' ').replace(/\s{2,}/g, ' ').trim();

  const bg = document.getElementById('lib-hero-bg');
  const av = document.getElementById('lib-hero-avatar');
  const ph = document.getElementById('lib-hero-ph');

  bg.style.backgroundImage = isPlaylists ? '' : (bgUrl ? `url(${JSON.stringify(bgUrl)})` : '');

  if (isPlaylists) {
    // Distinct collection look — a playlist tile, not an artist avatar
    av.style.display = 'none';
    ph.style.display = 'flex';
    ph.innerHTML = ICON.playlist;
  } else if (avatarUrl) {
    av.src = avatarUrl; av.style.display = 'block'; ph.style.display = 'none';
  } else {
    av.style.display = 'none'; ph.style.display = 'flex';
    ph.innerHTML = ICON.note;
  }

  document.getElementById('lib-hero-name').textContent = artist.name;

  // Subtitle line: for playlists show counts; for artists show fans
  const genresEl = document.getElementById('lib-hero-genres');
  if (genresEl) {
    if (isPlaylists) {
      const n = artist.albums.length;
      const tracks = artist.albums.reduce((s, a) => s + (a.track_count || 0), 0);
      genresEl.textContent = `${n} playlist${n !== 1 ? 's' : ''}` + (tracks ? ` · ${tracks} tracks` : '');
      genresEl.style.display = '';
    } else {
      const parts = [];
      if (fans) parts.push(fans);
      genresEl.textContent = parts.join(' · ');
      genresEl.style.display = parts.length ? '' : 'none';
    }
  }

  const aboutEl = document.getElementById('lib-hero-about');
  if (aboutEl) {
    aboutEl.textContent  = cleanBio || '';
    aboutEl.style.display = cleanBio ? '' : 'none';
  }

  _updateWatchBtn(artist);

  // Stats: IN LIBRARY / TOTAL ALBUMS / TO DOWNLOAD
  const _norm = s => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  const ownedNorm = new Set(artist.albums.map(a => _norm(a.album)));
  const notOwnedList = (artist._discog || []).filter(d => {
    const t = (d.record_type || '').toLowerCase();
    if (t && t !== 'album') return false;
    return !ownedNorm.has(_norm(d.title || ''));
  });
  artist._notOwned = notOwnedList;   // used by the "Download missing" button
  const notOwned = notOwnedList.length;
  const totalNum = totalAlbs ?? (localAlbs + notOwned);
  _updateDlMissingBtn(artist);

  const statOwned   = document.getElementById('lib-stat-owned');
  const statTotal   = document.getElementById('lib-stat-total');
  const statMissing = document.getElementById('lib-stat-missing');
  const seps = hero.querySelectorAll('.lib-hero-stat-sep');
  const statsWrap = document.getElementById('lib-hero-stats');
  if (artist.is_playlist_group) {
    // Counts live in the subtitle now — hide the artist-style stat boxes entirely
    if (statsWrap) statsWrap.style.display = 'none';
    if (aboutEl)   aboutEl.style.display = 'none';
    return;
  }
  if (statsWrap) statsWrap.style.display = '';
  if (statTotal)   statTotal.style.display   = '';
  if (statMissing) statMissing.style.display = '';
  seps.forEach(s => s.style.display = '');
  if (statOwned) {
    const lbl = statOwned.querySelector('.lib-hero-stat-label');
    if (lbl) lbl.textContent = 'IN LIBRARY';
  }
  if (statOwned)   statOwned.querySelector('.lib-hero-stat-num').textContent   = localAlbs;
  if (statTotal)   statTotal.querySelector('.lib-hero-stat-num').textContent   = totalNum || '—';
  if (statMissing) statMissing.querySelector('.lib-hero-stat-num').textContent = artist._discog ? notOwned : '—';
}

// ── Breadcrumb ─────────────────────────────────────────────────────────────
function _renderLibBreadcrumb() {
  const bc = document.getElementById('lib-breadcrumb');
  if (!bc) return;
  const albFilter = document.getElementById('lib-album-filter');

  // Playlists group calls its collection "Playlists" rather than "Albums"
  const _localOnly = !!(_libActiveArtist?.is_playlist_group || _libActiveArtist?.is_liked_group);
  const rootLabel  = _libActiveArtist?.is_playlist_group ? 'Playlists' : 'Albums';

  if (_libView === 'albums') {
    // Root view: no "Albums"/"Playlists" crumb — the hero + the "In Your Library"
    // section header already convey context. Keep the crumb only in track view.
    bc.innerHTML = '';
    if (albFilter) albFilter.closest('#lib-album-tools').style.display = 'flex';
    document.getElementById('lib-artist-sidebar')?.classList.remove('sidebar-hidden');
  } else {
    bc.innerHTML = `<span class="lib-crumb" id="bc-back">${rootLabel}</span>
      <span class="lib-crumb-sep">›</span>
      <span class="lib-crumb active">${escHtml(_libActiveAlbum?.album || '')}</span>`;
    if (albFilter) albFilter.closest('#lib-album-tools').style.display = 'none';
    document.getElementById('lib-artist-sidebar')?.classList.add('sidebar-hidden');
    document.getElementById('bc-back')?.addEventListener('click', () => {
      _libView        = 'albums';
      _libActiveAlbum = null;
      _renderLibBreadcrumb();
      const filter = (document.getElementById('lib-album-filter')?.value || '').trim().toLowerCase();
      const visible = filter
        ? _libActiveArtist.albums.filter(a =>
            a.album.toLowerCase().includes(filter))
        : _libActiveArtist.albums;
      // Local-only groups (Playlists) have no Deezer discography — pass [] so the
      // "Loading full discography…" placeholder never appears; else cached discog.
      _renderLibAlbums(visible, _localOnly ? [] : (_libActiveArtist._discog || null));
    });
  }
}

// ── Mouse back/forward (X1/X2 side buttons) navigate the library hierarchy ────
// Back climbs up: album tracks → artist albums → Home overview. Forward
// re-descends into the last item you backed out of (single-step memory). Only
// acts on the Library tab; the browser's own back/forward is suppressed
// app-wide so a side-button press never navigates away from the app.
let _libFwd = null;   // { kind:'album'|'artist', ... } — last thing backed out of

function _libNavBack() {
  if (_libView === 'tracks') {
    _libFwd = { kind: 'album', album: _libActiveAlbum };
    document.getElementById('bc-back')?.click();   // reuse the breadcrumb path
  } else if (_libView === 'albums') {
    _libFwd = { kind: 'artist', artist: _libActiveArtist };
    const home = (_libArtistList || []).find(a => a.is_home_group);
    if (home) _selectLibArtist(home, true);
  }
}
function _libNavForward() {
  if (!_libFwd) return;
  if (_libFwd.kind === 'artist' && _libView === 'home') {
    const a = _libFwd.artist; _libFwd = null;
    if (a) _selectLibArtist(a, true);
  } else if (_libFwd.kind === 'album' && _libView === 'albums') {
    const alb = _libFwd.album; _libFwd = null;
    if (alb) _openLibAlbum(alb);
  }
}
// X1 = button 3 (back), X2 = button 4 (forward). Block the browser default on
// these buttons everywhere, and drive the library when it's the active tab.
document.addEventListener('mousedown', (e) => {
  if (e.button === 3 || e.button === 4) e.preventDefault();
});
document.addEventListener('mouseup', (e) => {
  if (e.button !== 3 && e.button !== 4) return;
  e.preventDefault();
  if (typeof activeTab !== 'undefined' && activeTab !== 'library') return;
  if (e.button === 3) _libNavBack(); else _libNavForward();
});

// Shared "+" add-to-queue wiring for an album card. Used by BOTH the artist page
// and the Recommended shelf so they behave identically — one source of truth.
// `alb` is a normalised {id, url, artist, title, cover, nb_tracks}. On add: pop +
// card flash, then the card drops `not-owned` (brightening the dimmed cover — the
// "it's queued now" signal) and the button shows a check with a `just-added`
// guard so it doesn't flash the red remove state under the resting cursor. A
// queued album can be removed by clicking again (✕ on hover). Returns handles so
// the kebab menu can drive the same add / remove / paint.
function _wireAddButton(card, addBtn, alb, opts = {}) {
  const url   = alb.url || `https://www.deezer.com/album/${alb.id}`;
  const albId = String(alb.id || '');
  if (albId) card.dataset.albumId = albId;

  const _add = () => {
    if (!entries.some(e => e.url === url)) {
      entries.push({
        url,
        artist:     alb.artist || '',
        albumTitle: alb.title || '',
        coverUrl:   alb.cover || null,
        nbTracks:   alb.nb_tracks || null,
      });
    }
    if (albId) _libQueuedAlbumIds.add(albId);
    renderQueue();
    if (opts.onAdd) { try { opts.onAdd(); } catch (_) {} }
  };
  const _remove = () => {
    for (let i = entries.length - 1; i >= 0; i--) {
      if (entries[i].url === url) entries.splice(i, 1);
    }
    if (albId) _libQueuedAlbumIds.delete(albId);
    renderQueue();
  };
  const _setQueued = (on) => {
    card.classList.toggle('not-owned', !on);
    if (!addBtn) return;
    addBtn.classList.toggle('queued', on);
    if (!on) addBtn.classList.remove('just-added');
    addBtn.innerHTML = on ? ICON.check : ICON.plus;
    addBtn.title     = on ? 'In queue — click to remove' : 'Add to queue';
    addBtn.style.opacity = on ? '1' : '';
    addBtn.style.pointerEvents = '';
  };

  if (albId && _libQueuedAlbumIds.has(albId)) _setQueued(true);

  if (addBtn) {
    addBtn.addEventListener('mouseenter', () => {
      if (addBtn.classList.contains('queued')) addBtn.innerHTML = ICON.close;
    });
    addBtn.addEventListener('mouseleave', () => {
      addBtn.classList.remove('just-added');   // re-arm the red remove affordance
      if (addBtn.classList.contains('queued')) addBtn.innerHTML = ICON.check;
    });
    addBtn.addEventListener('click', ev => {
      ev.stopPropagation();
      if (addBtn.classList.contains('queued')) { _remove(); _setQueued(false); return; }
      _add();
      addBtn.classList.add('popping');
      card.classList.add('queue-flash');
      addBtn.addEventListener('animationend', () => addBtn.classList.remove('popping'), { once: true });
      card.addEventListener('animationend', () => card.classList.remove('queue-flash'), { once: true });
      setTimeout(() => { _setQueued(true); addBtn.classList.add('just-added'); }, 120);
    });
  }
  return { add: _add, remove: _remove, setQueued: _setQueued, url };
}

// ── Album grid — owned + full discography ──────────────────────────────────
let _libAlbumSort = 'az';   // az | za | tracks | recent
function _sortAlbums(list) {
  const byName = (a, b) => (a.album || '').localeCompare(b.album || '', undefined, { sensitivity: 'base' });
  switch (_libAlbumSort) {
    case 'za':     return list.sort((a, b) => byName(b, a));
    case 'tracks': return list.sort((a, b) => (b.track_count || 0) - (a.track_count || 0) || byName(a, b));
    case 'recent': return list.sort((a, b) => (b.mtime || 0) - (a.mtime || 0) || byName(a, b));
    default:       return list.sort(byName);
  }
}

function _renderLibAlbums(ownedAlbums, discog) {
  const content = document.getElementById('lib-content');
  content.innerHTML = '';

  // Put the "In Your Library" header on the toolbar row — the same line as the
  // A–Z / search controls — so the separator visibly runs from the header across
  // to those controls. (The controls live in #lib-album-tools, to the right of
  // the breadcrumb.) The matching owned section header inside the scroll area is
  // therefore suppressed below to avoid showing it twice.
  const _bc = document.getElementById('lib-breadcrumb');
  if (_bc && _libView === 'albums') {
    _bc.innerHTML = ownedAlbums.length
      ? `<div class="lib-discog-section" data-type="owned" style="padding:0;width:100%">
           <span class="lib-discog-section-label">In Your Library</span>
           <span class="lib-discog-count">${ownedAlbums.length}</span>
           <span class="lib-discog-sep"></span>
         </div>`
      : '';
  }

  // Build a set of normalised owned album titles for matching
  const _norm = s => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  const ownedNorm = new Set(ownedAlbums.map(a => _norm(a.album)));

  // ── Section helper ──────────────────────────────────────────────────────
  function makeSection(label, count, extra, type) {
    const row = document.createElement('div');
    row.className = 'lib-discog-section';
    if (type) row.dataset.type = type;
    row.innerHTML = `
      <span class="lib-discog-section-label">${label}</span>
      <span class="lib-discog-count">${count}</span>
      ${extra || ''}
      <span class="lib-discog-sep"></span>
    `;
    return row;
  }

  // ── Render a single album card ──────────────────────────────────────────
  // ── Album drag state ─────────────────────────────────────────────────────
  let _albumDragSrc = null;
  let _albumDragGrid = null;

  function _attachAlbumDrag(card, alb, getGrid) {
    card.draggable = true;
    card.addEventListener('dragstart', ev => {
      _albumDragSrc  = alb;
      _albumDragGrid = getGrid();
      ev.dataTransfer.effectAllowed = 'move';
      requestAnimationFrame(() => card.classList.add('dragging'));
    });
    card.addEventListener('dragover', ev => {
      ev.preventDefault(); ev.dataTransfer.dropEffect = 'move';
      const g = getGrid();
      if (g) g.querySelectorAll('.lib-album-card').forEach(c =>
        c.classList.remove('drag-insert-before', 'drag-over-artist'));
      if (_albumDragSrc !== alb) {
        const rect = card.getBoundingClientRect();
        card.classList.add(ev.clientX < rect.left + rect.width / 2 ? 'drag-insert-before' : 'drag-over-artist');
      }
    });
    card.addEventListener('dragleave', () => card.classList.remove('drag-insert-before', 'drag-over-artist'));
    card.addEventListener('drop', ev => {
      ev.preventDefault();
      const isBefore = card.classList.contains('drag-insert-before');
      card.classList.remove('drag-insert-before', 'drag-over-artist');
      if (!_albumDragSrc || _albumDragSrc === alb) return;
      // Reorder within ownedAlbums for the active artist
      const arr = _libActiveArtist?.albums;
      if (!arr) return;
      const srcIdx = arr.indexOf(_albumDragSrc);
      const dstIdx = arr.indexOf(alb);
      if (srcIdx === -1 || dstIdx === -1) return;
      const [moved] = arr.splice(srcIdx, 1);
      arr.splice(isBefore ? dstIdx : dstIdx + 1, 0, moved);
      _albumDragSrc = null;
      // Re-render
      const f = (document.getElementById('lib-album-filter')?.value || '').trim().toLowerCase();
      const vis = f ? arr.filter(a => a.album.toLowerCase().includes(f)) : arr;
      _renderLibAlbums(vis, _libActiveArtist._discog || null);
    });
    card.addEventListener('dragend', () => {
      _albumDragSrc = null;
      const g = getGrid();
      if (g) g.querySelectorAll('.lib-album-card').forEach(c =>
        c.classList.remove('dragging', 'drag-insert-before', 'drag-over-artist'));
    });
  }

  function makeCard(alb, isOwned) {
    const card = document.createElement('div');
    card.className = 'lib-album-card' + (isOwned ? '' : ' not-owned');

    const coverSrc = alb.cover_url || alb.cover_medium || alb.cover_small
      || (alb.path_hash ? `/cover/${alb.path_hash}` : null);
    const _phIcon = alb.is_playlist ? ICON.playlist : ICON.note;
    // Playlists: if the remote cover fails to load, fall back to the locally
    // saved cover.jpg / embedded art via /cover before showing a placeholder.
    const _localFb = (alb.is_playlist && alb.cover_url && alb.path_hash)
      ? `/cover/${alb.path_hash}` : '';
    const _onerr = _localFb
      ? `if(this.dataset.fb){this.src=this.dataset.fb;this.dataset.fb='';}else{this.style.display='none';this.nextElementSibling.style.display='flex';}`
      : `this.style.display='none';this.nextElementSibling.style.display='flex';`;
    const coverHtml = coverSrc
      ? `<img class="lib-cover" src="${escHtml(coverSrc)}" loading="lazy" alt=""${_localFb ? ` data-fb="${escHtml(_localFb)}"` : ''}
             onerror="${_onerr}">
         <div class="lib-cover-ph" style="display:none">${_phIcon}</div>`
      : `<div class="lib-cover-ph">${_phIcon}</div>`;

    const trackCount = alb.track_count ?? alb.nb_tracks;
    const trackStr   = trackCount != null
      ? `${trackCount} track${trackCount !== 1 ? 's' : ''}`
      : '';

    card.innerHTML = `
      <div class="lib-cover-wrap">
        ${coverHtml}
        ${!isOwned ? `<button class="lib-add-btn" title="Add to queue">${ICON.plus}</button>` : ''}
        <button class="kebab-btn" title="More options" style="position:absolute;top:4px;right:4px;width:20px;height:20px;border-radius:50%;background:rgba(0,0,0,0.55);font-size:11px;opacity:0;pointer-events:none;">⋯</button>
      </div>
      <div class="lib-album-info">
        <div class="lib-album-title" title="${escHtml(alb.album || alb.title || '')}">${escHtml(alb.album || alb.title || '')}</div>
        ${alb.is_playlist ? '' : `<div class="lib-album-artist">${escHtml(alb.artist || _libActiveArtist?.name || '')}</div>`}
        ${trackStr ? `<div class="lib-album-tracks">${trackStr}</div>` : ''}
      </div>
      ${''/* orphan badge removed */}
    `;

    // Show/hide kebab on hover
    const kb = card.querySelector('.kebab-btn');
    card.addEventListener('mouseenter', () => { if (kb) { kb.style.opacity='1'; kb.style.pointerEvents='auto'; } });
    card.addEventListener('mouseleave', () => { if (kb) { kb.style.opacity='0'; kb.style.pointerEvents='none'; } });

    if (isOwned) {
      card.addEventListener('click', (ev) => {
        if (ev.target.classList.contains('kebab-btn')) return;
        _openLibAlbum(alb);
      });
      // Owned album kebab menu
      if (kb) kb.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const _plPinned = alb.is_playlist && _isPinned('playlists', alb.album);
        openKebabMenu(ev.currentTarget, [
          { label: 'Open in Explorer', icon: ICON.folder, action: () => {
            fetch('/open-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ path: alb.album_dir }) }).catch(() => {});
          }},
          { label: 'Open tracks', icon: ICON.note, action: () => _openLibAlbum(alb) },
          ...(alb.is_playlist ? [{
            label: _plPinned ? 'Unpin from sidebar' : 'Pin to sidebar',
            icon: _plPinned ? ICON.pinFilled : ICON.pin, action: () => {
              const nowPinned = _togglePin('playlists', alb.album);
              _toast(nowPinned ? `Pinned "${alb.album}".` : `Unpinned "${alb.album}".`, 'success');
            }}] : []),
          { divider: true },
          { label: 'Remove from list', icon: ICON.close, danger: false, action: () => {
            if (!_libActiveArtist) return;
            const idx = _libActiveArtist.albums.indexOf(alb);
            if (idx !== -1) _libActiveArtist.albums.splice(idx, 1);
            const f = (document.getElementById('lib-album-filter')?.value || '').trim().toLowerCase();
            const vis = f ? _libActiveArtist.albums.filter(a => a.album.toLowerCase().includes(f)) : _libActiveArtist.albums;
            _renderLibAlbums(vis, _libActiveArtist._discog || null);
          }},
          { label: 'Delete album', icon: ICON.trash, danger: true, action: () => {
            showDeleteConfirm({
              title: 'Delete Album',
              msg:   `All local files for this album will be permanently removed from disk.`,
              detail: `${alb.album}  ·  ${alb.artist}`,
              onConfirm: () => {
            // Remove from artist albums list
            if (_libActiveArtist) {
              const idx = _libActiveArtist.albums.indexOf(alb);
              if (idx !== -1) _libActiveArtist.albums.splice(idx, 1);
            }
            // Remove manifest entry
            if (alb.deezer_url) {
              fetch('/history-remove', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: alb.deezer_url }) });
            }
            // Tell server to delete the folder
            fetch('/delete-album', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ album_dir: alb.album_dir }) })
              .then(r => r.json())
              .then(d => {
                appendLog(d.ok ? `  Deleted ${alb.is_playlist ? 'playlist' : 'album'}: ${alb.album}\n` : `  Delete error: ${d.error}\n`,
                  d.ok ? 'log-warn' : 'log-error');
                // Re-scan so sidebar counts / Playlists group / Home stats update
                _libStatsCache = null;
                if (activeTab === 'library') loadLibrary();
              })
              .catch(() => appendLog(`  Delete request failed (server may not support it yet)\n`, 'log-warn'));
            // Re-render
            const f = (document.getElementById('lib-album-filter')?.value || '').trim().toLowerCase();
            const vis = _libActiveArtist
              ? (f ? _libActiveArtist.albums.filter(a => a.album.toLowerCase().includes(f)) : _libActiveArtist.albums)
              : [];
            _renderLibAlbums(vis, _libActiveArtist?._discog || null);
              }
            });
          }},
        ]);
      });
      // Drag to reorder owned albums
      _attachAlbumDrag(card, alb, () => card.closest('.lib-album-grid'));
    } else {
      // Not-owned: + button toggles the album in/out of the download queue —
      // shared logic with the Recommended shelf (see _wireAddButton).
      const addBtn = card.querySelector('.lib-add-btn');
      const _h = _wireAddButton(card, addBtn, {
        id:        alb.id,
        url:       `https://www.deezer.com/album/${alb.id}`,
        artist:    _libActiveArtist?.name || alb.artist_name || '',
        title:     alb.title || alb.album || '',
        cover:     alb.cover_medium || alb.cover_small || null,
        nb_tracks: alb.nb_tracks || null,
      });
      // Not-owned kebab menu — offer add or remove depending on current state
      if (kb) kb.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const queued = entries.some(e => e.url === _h.url);
        openKebabMenu(ev.currentTarget, [
          queued
            ? { label: 'Remove from queue', icon: ICON.close, action: () => { _h.remove(); _h.setQueued(false); } }
            : { label: 'Add to queue',      icon: ICON.plus,  action: () => { _h.add();    _h.setQueued(true);  } },
        ]);
      });
    }
    return card;
  }

  // ── Owned section ───────────────────────────────────────────────────────
  // (Header lives on the toolbar row now — see the breadcrumb block above.)
  if (ownedAlbums.length) {
    const ownedGrid = document.createElement('div');
    ownedGrid.className = 'lib-album-grid';
    _sortAlbums(ownedAlbums.slice()).forEach(alb => ownedGrid.appendChild(makeCard(alb, true)));
    content.appendChild(ownedGrid);
  } else {
    const empty = document.createElement('div');
    empty.className = 'stat-empty';
    empty.textContent = 'No albums in library for this artist.';
    content.appendChild(empty);
  }

  // ── Not owned sections (from Deezer discography), grouped by type ───────
  if (discog && discog.length) {
    // Honour the album-search box across albums, EPs and singles alike.
    const _dzFilter = (document.getElementById('lib-album-filter')?.value || '').trim().toLowerCase();
    const notOwned = discog.filter(dAlb =>
      !ownedNorm.has(_norm(dAlb.title || '')) &&
      (!_dzFilter || (dAlb.title || '').toLowerCase().includes(_dzFilter)));
    const ofType = (types) => notOwned.filter(d => types.includes((d.record_type || 'album').toLowerCase()));
    const shapeDz = (dAlb) => ({
      album:        dAlb.title,
      artist:       _libActiveArtist?.name || '',
      cover_url:    dAlb.cover_medium || dAlb.cover_small || '',
      cover_medium: dAlb.cover_medium || '',
      cover_small:  dAlb.cover_small  || '',
      nb_tracks:    dAlb.nb_tracks,
      id:           dAlb.id,
    });
    const renderGroup = (label, list) => {
      if (!list.length) return;
      content.appendChild(makeSection(label, list.length, null, 'missing'));
      const grid = document.createElement('div');
      grid.className = 'lib-album-grid';
      list.forEach(dAlb => grid.appendChild(makeCard(shapeDz(dAlb), false)));
      content.appendChild(grid);
    };
    renderGroup('Albums',             ofType(['album', '']));
    renderGroup('EPs',                ofType(['ep']));
    renderGroup('Singles',            ofType(['single']));
  } else if (discog === null
             && !_libActiveArtist?.is_playlist_group
             && !_libActiveArtist?.is_liked_group) {
    // Still loading a real artist's discography (never for local-only groups)
    const loading = document.createElement('div');
    loading.style.cssText = 'font-family:var(--mono);font-size:10px;color:var(--fg3);padding:10px 0';
    loading.textContent = 'Loading full discography…';
    content.appendChild(loading);
  }
}

// ── Home overview ───────────────────────────────────────────────────────────
async function _renderHomeView() {
  const content = document.getElementById('lib-content');
  content.innerHTML = '<div class="stat-empty">Loading…</div>';

  const regularAlbums  = (_libAllAlbums || []).filter(a => !a.is_playlist);
  const playlistAlbums = (_libAllAlbums || []).filter(a => a.is_playlist);
  const artistCount    = new Set(regularAlbums.map(a => a.artist)).size;

  let history = [], likedCount = 0;
  try {
    const [h, l] = await Promise.all([
      fetch('/history').then(r => r.json()).catch(() => []),
      fetch('/liked-songs').then(r => r.json()).catch(() => ({ tracks: [] })),
    ]);
    history    = Array.isArray(h) ? h : [];
    likedCount = (l.tracks || []).length;
  } catch (_) {}

  const norm   = s => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const findAlb = (artistName, albumName, isPl) => (_libAllAlbums || []).find(a =>
    (!!a.is_playlist === !!isPl) && norm(a.album) === norm(albumName) &&
    (isPl || norm(a.artist) === norm(artistName)));

  // Recently downloaded (history is newest-first), de-duplicated
  const seen = new Set();
  const recent = [];
  for (const item of history) {
    const isPl = !!item.is_playlist;
    const name = (item.albums && item.albums[0]) || item.artist || '—';
    const key  = (isPl ? 'p:' : 'a:') + norm(item.artist) + '|' + norm(name);
    if (seen.has(key)) continue;
    seen.add(key);
    recent.push({ name, artist: isPl ? 'Playlist' : (item.artist || ''), isPl,
                  alb: findAlb(item.artist, name, isPl) });
    if (recent.length >= 12) break;
  }

  const card = (r, idx) => {
    const src   = r.alb ? (r.alb.cover_url || (r.alb.path_hash ? `/cover/${r.alb.path_hash}` : '')) : '';
    const phIco = r.isPl ? ICON.playlist : ICON.note;
    const cover = src
      ? `<img class="lib-cover" src="${escHtml(src)}" loading="lazy" alt=""
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
         <div class="lib-cover-ph" style="display:none">${phIco}</div>`
      : `<div class="lib-cover-ph">${phIco}</div>`;
    return `<div class="lib-album-card home-recent-card" data-ri="${idx}">
      <div class="lib-cover-wrap">${cover}</div>
      <div class="lib-album-info">
        <div class="lib-album-title" title="${escHtml(r.name)}">${escHtml(r.name)}</div>
        <div class="lib-album-artist">${escHtml(r.artist)}</div>
      </div>
    </div>`;
  };

  // Recently added shelf: album-folder mtime, so folder imports and
  // manual copies surface here too — not just bot downloads.
  const addedRecently = regularAlbums
    .filter(a => a.mtime)
    .sort((x, y) => (y.mtime || 0) - (x.mtime || 0))
    .slice(0, 12);
  const addedCard = (a, idx) => {
    const src = a.cover_url || (a.path_hash ? `/cover/${a.path_hash}` : '');
    const cover = src
      ? `<img class="lib-cover" src="${escHtml(src)}" loading="lazy" alt=""
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
         <div class="lib-cover-ph" style="display:none">${ICON.note}</div>`
      : `<div class="lib-cover-ph">${ICON.note}</div>`;
    return `<div class="lib-album-card home-added-card" data-ai="${idx}">
      <div class="lib-cover-wrap">${cover}</div>
      <div class="lib-album-info">
        <div class="lib-album-title" title="${escHtml(a.album)}">${escHtml(a.album)}</div>
        <div class="lib-album-artist">${escHtml(a.artist)}</div>
      </div>
    </div>`;
  };

  // Time-of-day greeting for a little ambience
  const _hr = new Date().getHours();
  const greeting = _hr < 5 ? 'Good night' : _hr < 12 ? 'Good morning'
                 : _hr < 18 ? 'Good afternoon' : 'Good evening';

  // Ambient backdrop: a blurred collage of recent / library cover art
  const bgSrcs = [];
  for (const r of recent) {
    const s = r.alb ? (r.alb.cover_url || (r.alb.path_hash ? `/cover/${r.alb.path_hash}` : '')) : '';
    if (s && !bgSrcs.includes(s)) bgSrcs.push(s);
    if (bgSrcs.length >= 6) break;
  }
  if (bgSrcs.length < 3) {
    for (const a of (_libAllAlbums || [])) {
      const s = a.cover_url || (a.path_hash ? `/cover/${a.path_hash}` : '');
      if (s && !bgSrcs.includes(s)) bgSrcs.push(s);
      if (bgSrcs.length >= 6) break;
    }
  }
  const heroBg = bgSrcs.length
    ? `<div class="home-hero-bg">${bgSrcs.map(s =>
         `<span style="background-image:url('${escHtml(s)}')"></span>`).join('')}</div>`
    : '';

  const subBits = [];
  if (regularAlbums.length) subBits.push(`${regularAlbums.length} album${regularAlbums.length !== 1 ? 's' : ''}`);
  if (artistCount)          subBits.push(`${artistCount} artist${artistCount !== 1 ? 's' : ''}`);
  if (playlistAlbums.length) subBits.push(`${playlistAlbums.length} playlist${playlistAlbums.length !== 1 ? 's' : ''}`);
  if (likedCount)           subBits.push(`${likedCount} liked song${likedCount !== 1 ? 's' : ''}`);

  content.innerHTML = `
    <div class="home-hero">
      ${heroBg}
      <div class="home-hero-scrim"></div>
      <div class="home-hero-inner">
        <div class="home-hero-eyebrow">${ICON.headphones} Your library</div>
        <div class="home-hero-title">${greeting}</div>
        <div class="home-hero-sub">${subBits.length ? escHtml(subBits.join('  ·  ')) : 'Add some music to get started'}</div>
        ${regularAlbums.length ? `<button class="btn-secondary" id="btn-home-surprise"
            style="width:auto;margin-top:12px;display:inline-flex;align-items:center;gap:6px"
            title="Open a random album from your library">${ICON.shuffle} Surprise me</button>` : ''}
      </div>
    </div>
    <div class="home-section-label home-releases-head">
      <span>New releases<span id="home-releases-count" class="home-count-badge" style="display:none"></span></span>
      <button class="btn-secondary" id="btn-home-check-releases" style="width:auto">Check now</button>
    </div>
    <div id="home-releases"><div class="stat-empty" style="padding:14px 0">Loading…</div></div>
    <div class="home-section-label" style="margin-top:22px">Recommended for you</div>
    <div id="home-recs"></div>
    ${addedRecently.length ? `
      <div class="home-section-label">Recently added</div>
      <div class="lib-album-grid home-recent-grid">${addedRecently.map((a, i) => addedCard(a, i)).join('')}</div>
    ` : ''}
    ${recent.length ? `
      <div class="home-section-label">Recently downloaded</div>
      <div class="lib-album-grid home-recent-grid">${recent.map((r, i) => card(r, i)).join('')}</div>
    ` : '<div class="stat-empty">No downloads yet — add some music from the Log tab.</div>'}
  `;

  // Releases section: always shown. Render cached results, refresh the watchlist
  // (so the count is right), then check for new releases in the background.
  document.getElementById('btn-home-check-releases')?.addEventListener('click', () => checkReleases(true));
  renderReleases();
  _loadWatchlist().then(() => { renderReleases(); checkReleases(false); });

  // Recommended for you — seeded from the library's most-stocked artists
  _renderHomeRecs();

  content.querySelectorAll('.home-recent-card').forEach(el => {
    el.addEventListener('click', () => {
      const r = recent[+el.dataset.ri];
      if (!r) return;
      const group = r.isPl
        ? _libArtistList.find(a => a.is_playlist_group)
        : _libArtistList.find(a => a.name === r.artist);
      if (group) {
        Promise.resolve(_selectLibArtist(group, true)).then(() => {
          if (r.alb) _openLibAlbum(r.alb);
        });
      }
    });
  });

  // Recently-added shelf cards → open that album
  content.querySelectorAll('.home-added-card').forEach(el => {
    el.addEventListener('click', () => {
      const a = addedRecently[+el.dataset.ai];
      if (!a) return;
      const group = _libArtistList.find(g => g.name === a.artist);
      if (group) Promise.resolve(_selectLibArtist(group, true)).then(() => _openLibAlbum(a));
    });
  });

  document.getElementById('btn-home-surprise')?.addEventListener('click', _libSurpriseMe);
}

// ── Surprise me: open a random album from the library ──────────────
function _libSurpriseMe() {
  const pool = (_libAllAlbums || []).filter(a => !a.is_playlist);
  if (!pool.length) { _toast('No albums in your library yet.', 'info'); return; }
  const alb = pool[Math.floor(Math.random() * pool.length)];
  const group = _libArtistList.find(g => g.name === alb.artist);
  if (!group) return;
  _toast(`How about "${alb.album}" by ${alb.artist}?`, 'info');
  Promise.resolve(_selectLibArtist(group, true)).then(() => _openLibAlbum(alb));
}

// ── Recommended for you (Home) ──────────────────────────────────────────────
let _homeRecsCache = null;
async function _renderHomeRecs() {
  const el = document.getElementById('home-recs');
  if (!el) return;
  const seeds = (_libArtistList || [])
    .filter(a => !a.is_home_group && !a.is_liked_group && !a.is_playlist_group)
    .sort((a, b) => (b.albums?.length || 0) - (a.albums?.length || 0))
    .slice(0, 4).map(a => a.name);
  if (!seeds.length) { el.innerHTML = '<div class="stat-empty" style="padding:10px 0">Add some artists to get recommendations.</div>'; return; }
  if (_homeRecsCache) { _paintHomeRecs(el, _homeRecsCache); return; }
  el.innerHTML = '<div class="stat-empty" style="padding:14px 0">Finding music you might like…</div>';
  try {
    const r = await fetch('/recommendations?seeds=' + encodeURIComponent(seeds.join('|')));
    const d = await r.json();
    const norm  = s => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const owned = new Set((_libAllAlbums || []).filter(a => !a.is_playlist).map(a => norm(a.album)));
    const recs  = (d.albums || []).filter(a => !owned.has(norm(a.title))).slice(0, 12);
    _homeRecsCache = recs;
    _paintHomeRecs(el, recs);
  } catch (_) {
    el.innerHTML = '<div class="stat-empty" style="padding:10px 0">Couldn’t load recommendations.</div>';
  }
}
function _paintHomeRecs(el, recs) {
  if (!recs.length) { el.innerHTML = '<div class="stat-empty" style="padding:10px 0">No recommendations right now.</div>'; return; }
  el.className = 'lib-album-grid home-recent-grid';
  el.innerHTML = recs.map((a, i) => `
    <div class="lib-album-card not-owned" data-ri="${i}">
      <div class="lib-cover-wrap">
        ${a.cover ? `<img class="lib-cover" src="${escHtml(a.cover)}" loading="lazy" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><div class="lib-cover-ph" style="display:none">${ICON.note}</div>`
                  : `<div class="lib-cover-ph">${ICON.note}</div>`}
        <button class="lib-add-btn rec-add" data-ri="${i}" title="Add to download queue">${ICON.plus}</button>
      </div>
      <div class="lib-album-info">
        <div class="lib-album-title" title="${escHtml(a.title)}">${escHtml(a.title)}</div>
        <div class="lib-album-artist" title="${escHtml(a.artist)}">${escHtml(a.artist)}</div>
      </div>
    </div>`).join('');
  // Identical add behaviour to the artist page — one shared code path.
  el.querySelectorAll('.lib-album-card').forEach(card => {
    const a = recs[+card.dataset.ri]; if (!a) return;
    _wireAddButton(card, card.querySelector('.rec-add'), {
      id:        a.id,
      url:       a.link || `https://www.deezer.com/album/${a.id}`,
      artist:    a.artist,
      title:     a.title,
      cover:     a.cover || null,
      nb_tracks: a.nb_tracks || null,
    }, { onAdd: () => _toast(`Queued "${a.title}" by ${a.artist}.`, 'success') });
  });
}

// ── Open album → track list ────────────────────────────────────────────────
async function _openLibAlbum(alb) {
  _libActiveAlbum = alb;
  _libView        = 'tracks';
  _renderLibBreadcrumb();

  const content = document.getElementById('lib-content');
  content.innerHTML = '<div class="stat-empty">Loading tracks…</div>';

  try {
    const params = new URLSearchParams({ path_hash: alb.path_hash });
    // Prefer the Deezer ID from deezer_url, fall back to the directly exposed deezer_album_id
    let albumId = '';
    if (alb.deezer_url) {
      const m = alb.deezer_url.match(/\/album\/(\d+)/);
      if (m) albumId = m[1];
    }
    if (!albumId && alb.deezer_album_id) albumId = alb.deezer_album_id;
    if (albumId) params.set('album_id', albumId);

    const resp = await fetch(`/album-tracks?${params}`);
    const data = await resp.json();

    if (data.error) {
      content.innerHTML = `<div class="stat-empty" style="color:var(--red)">${escHtml(data.error)}</div>`;
      return;
    }

    // Only update the playback queue if this album is already the one playing,
    // or if nothing is playing yet. Otherwise the displayed tracks belong to a
    // different album and must not hijack the active queue.
    if (!_mpCurrentAlb || _mpCurrentAlb === alb) {
      _mpQueue = data.tracks;
    }
    // Playlists show per-track album + date-added columns (like Spotify)
    _renderLibTracks(data.tracks, alb, alb.is_playlist
      ? { showArtist: true, showAlbum: true, showDate: true }
      : {});

  } catch (e) {
    content.innerHTML = `<div class="stat-empty" style="color:var(--red)">Error: ${escHtml(String(e))}</div>`;
  }
}

// Format an ISO "date added" into a short, Spotify-style date label
function _fmtDateAdded(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' });
}

// Resolve an album by name on Deezer and add it to the queue.
// opts.silent → no confirm dialog / no toast (used for batch context-menu adds).
// Returns true if an album was added.
async function _queueAlbumByName(artist, album, opts = {}) {
  if (!album) return false;
  let info = null;
  try {
    info = await (await fetch(
      `/find-album?artist=${encodeURIComponent(artist || '')}&album=${encodeURIComponent(album)}`
    )).json();
  } catch (_) {}
  if (!info || info.error || !info.id) {
    if (!opts.silent) _toast(`Couldn't find "${album}" on Deezer.`, 'error');
    return false;
  }
  if (!opts.silent) {
    const ok = await _confirm(
      `Add "${info.title || album}"${info.artist ? ' by ' + info.artist : ''} to the download queue?`,
      { title: 'Add album to queue', confirmLabel: 'Add to queue' });
    if (!ok) return false;
  }
  entries.push({
    url: `https://www.deezer.com/album/${info.id}`,
    artist: info.artist || artist || 'Unknown Artist',
    albumTitle: info.title || album,
    coverUrl: info.cover || null, nbTracks: info.nb_tracks || null,
    isPlaylist: false, playlistName: '',
  });
  renderQueue();
  if (!opts.silent) _toast(`Added "${info.title || album}" to the queue.`, 'success');
  return true;
}

// ── Track multi-selection (click / shift-click) + context menu ──────────────
let _trackSel = new Set();
let _trackSelAnchor = -1;

function _applyTrackSelection() {
  document.querySelectorAll('.lib-track-row').forEach(r =>
    r.classList.toggle('selected', _trackSel.has(+r.dataset.idx)));
}

function _refreshTrackHearts(tracks, alb) {
  document.querySelectorAll('.lib-track-row').forEach(r => {
    const i = +r.dataset.idx;
    const t = tracks[i];
    if (!t) return;
    const liked = _isTrackLiked(t, alb);
    const btn = r.querySelector('.lib-track-like');
    if (btn) { btn.classList.toggle('liked', liked); btn.innerHTML = liked ? ICON.heartFilled : ICON.heart; }
  });
}

// Repaint the star-rating badges in the visible track rows
function _refreshTrackStars(tracks, alb) {
  document.querySelectorAll('.lib-track-row').forEach(r => {
    const t = tracks[+r.dataset.idx];
    if (!t) return;
    const titleEl = r.querySelector('.lib-track-title');
    if (!titleEl) return;
    let stars = titleEl.querySelector('.lib-track-stars');
    const rating = _trackRating(t, alb);
    if (!rating) { stars?.remove(); return; }
    if (!stars) {
      stars = document.createElement('span');
      stars.className = 'lib-track-stars';
      titleEl.appendChild(stars);
    }
    stars.textContent = '★'.repeat(rating);
    stars.title = `Rated ${rating}/5`;
  });
}

// Build {path_hash, name} refs for the selected tracks so the backend can copy
// them. Per-track path_hash (Liked Songs) wins; else the album/playlist dir's.
function _trackRefs(selTracks, alb) {
  return selTracks
    .map(t => ({ path_hash: t.path_hash || alb?.path_hash || '', name: t.name }))
    .filter(r => r.path_hash && r.name);
}

// Copy selected tracks into a playlist folder (create=true makes a new one).
async function _addTracksToPlaylist(name, refs, create) {
  if (!refs.length) { _toast('No tracks could be located.', 'error'); return; }
  try {
    const r = await fetch('/playlist-add-tracks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, tracks: refs, create }),
    });
    const d = await r.json();
    if (d.error) { _toast(d.error, 'error'); return; }
    _toast(`Added ${d.added} track${d.added !== 1 ? 's' : ''} to "${d.name}".`, 'success');
    _libStatsCache = null;
    loadLibrary({ keepView: true });
  } catch (e) {
    _toast('Could not add to playlist: ' + e, 'error');
  }
}

function _openTrackCtx(rowEl, tracks, alb, opts, point) {
  const sel = [..._trackSel].sort((a, b) => a - b);
  const selTracks = sel.map(i => tracks[i]).filter(Boolean);
  if (!selTracks.length) return;
  const n = selTracks.length;
  const allLiked = selTracks.every(t => _isTrackLiked(t, alb));
  const refs = _trackRefs(selTracks, alb);
  const items = [];

  items.push({ label: n > 1 ? `Play (${n} selected)` : 'Play', icon: ICON.play,
    action: () => { _mpQueue = tracks; _mpPlayTrack(sel[0], alb); } });

  if (allLiked) {
    items.push({ label: n > 1 ? `Remove ${n} from Liked Songs` : 'Remove from Liked Songs',
      icon: ICON.heartFilled, action: async () => {
        for (const t of selTracks) if (_isTrackLiked(t, alb)) await _toggleLike(t, alb);
        _refreshTrackHearts(tracks, alb); _toast('Updated Liked Songs.', 'success');
      } });
  } else {
    items.push({ label: n > 1 ? `Save ${n} to Liked Songs` : 'Save to Liked Songs',
      icon: ICON.heart, action: async () => {
        for (const t of selTracks) if (!_isTrackLiked(t, alb)) await _toggleLike(t, alb);
        _refreshTrackHearts(tracks, alb); _toast('Added to Liked Songs.', 'success');
      } });
  }

  // ── Star rating: 1–5 submenu + clear ──
  {
    const current = n === 1 ? _trackRating(selTracks[0], alb) : 0;
    items.push({
      label: n > 1 ? `Rate ${n} tracks` : 'Rate',
      icon: ICON.star,
      submenu: () => {
        const sub = [5, 4, 3, 2, 1].map(r => ({
          label: '★'.repeat(r) + '☆'.repeat(5 - r) + (current === r ? '   ✓' : ''),
          action: async () => {
            for (const t of selTracks) await _setTrackRating(t, alb, r);
            _refreshTrackStars(tracks, alb);
            _toast(n > 1 ? `Rated ${n} tracks ${r} star${r !== 1 ? 's' : ''}.`
                         : `Rated ${r} star${r !== 1 ? 's' : ''}.`, 'success');
          },
        }));
        if (selTracks.some(t => _trackRating(t, alb))) {
          sub.push({ divider: true });
          sub.push({
            label: 'Clear rating',
            action: async () => {
              for (const t of selTracks) if (_trackRating(t, alb)) await _setTrackRating(t, alb, 0);
              _refreshTrackStars(tracks, alb);
              _toast('Rating cleared.', 'success');
            },
          });
        }
        return sub;
      },
    });
  }

  // Queue the album(s) the selected tracks belong to (playlist view)
  if (opts.showAlbum) {
    const byAlbum = new Map();
    selTracks.forEach(t => {
      if (t.album && !byAlbum.has(t.album)) byAlbum.set(t.album, t.artist || alb?.artist || '');
    });
    if (byAlbum.size) {
      items.push({ label: byAlbum.size > 1 ? `Add ${byAlbum.size} albums to queue` : 'Add album to queue',
        icon: ICON.plus, action: async () => {
          let added = 0;
          for (const [album, artist] of byAlbum) {
            if (await _queueAlbumByName(artist, album, { silent: true })) added++;
          }
          _toast(added ? `Added ${added} album(s) to the queue.` : 'No matching albums found.',
                 added ? 'success' : 'error');
        } });
    }
  }

  // ── Add to playlist (Spotify-style submenu: New playlist + existing) ──
  items.push({ divider: true });
  items.push({
    label: 'Add to playlist', icon: ICON.playlist,
    submenu: () => {
      const sub = [];
      sub.push({
        label: 'New playlist', icon: ICON.plus,
        action: async () => {
          const name = await _prompt('Name your new playlist', {
            title: 'Create playlist', placeholder: 'My playlist',
            confirmLabel: 'Create',
          });
          if (name) _addTracksToPlaylist(name, refs, true);
        },
      });
      const existing = (_libAllAlbums || []).filter(a => a.is_playlist)
        .sort((a, b) => a.album.localeCompare(b.album, undefined, { sensitivity: 'base' }));
      if (existing.length) {
        sub.push({ divider: true });
        existing.forEach(pl => {
          // Don't offer to add a playlist's tracks back into itself
          if (alb && alb.is_playlist && alb.path_hash === pl.path_hash) return;
          sub.push({
            label: pl.album, icon: ICON.playlist,
            action: () => _addTracksToPlaylist(pl.album, refs, false),
          });
        });
      }
      return sub;
    },
  });

  // ── Remove from this playlist (only inside a playlist view) ──
  if (alb && alb.is_playlist && alb.path_hash) {
    items.push({
      label: n > 1 ? `Remove ${n} from this playlist` : 'Remove from this playlist',
      icon: ICON.trash, danger: true,
      action: async () => {
        const names = selTracks.map(t => t.name).filter(Boolean);
        try {
          const r = await fetch('/playlist-remove-tracks', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path_hash: alb.path_hash, names }),
          });
          const d = await r.json();
          if (d.error) { _toast(d.error, 'error'); return; }
          _toast(`Removed ${d.removed} track${d.removed !== 1 ? 's' : ''} from the playlist.`, 'success');
          _libStatsCache = null;
          _openLibAlbum(alb);
        } catch (e) {
          _toast('Could not remove tracks: ' + e, 'error');
        }
      },
    });
  }

  openKebabMenu(rowEl, items, point ? { atPoint: point } : undefined);
}

// Format a total number of seconds into "X min" or "X hr Y min"
function _fmtTotalDuration(secs) {
  secs = Math.round(secs || 0);
  if (secs <= 0) return '';
  const h = Math.floor(secs / 3600);
  const m = Math.round((secs % 3600) / 60);
  if (h > 0) return `${h} hr ${m} min`;
  if (m > 0) return `${m} min`;
  return `${secs} sec`;
}

// ── Track list view ────────────────────────────────────────────────────────
// opts: { showArtist:bool, showAlbum:bool, showDate:bool, hideHeader:bool }
function _renderLibTracks(tracks, alb, opts) {
  opts = opts || {};
  const content = document.getElementById('lib-content');
  _trackSel = new Set(); _trackSelAnchor = -1;   // reset selection on (re)render

  // Per-track thumbnails only make sense where tracks span multiple covers
  // (playlists, Liked Songs). On a single album page every track shares the
  // same artwork already shown in the header, so the thumbnails are hidden.
  const showThumb = (opts.showThumb !== undefined)
    ? opts.showThumb : !!(opts.showArtist || opts.showAlbum);

  if (!tracks.length) {
    content.innerHTML = `<div class="stat-empty">${opts.emptyMsg || 'No audio files found in this folder.'}</div>`;
    return;
  }

  const coverSrc = alb.cover_url || (alb.path_hash ? `/cover/${alb.path_hash}` : null);

  // Album header (skippable for views that already show the name in the hero)
  let headerDiv = null;
  if (!opts.hideHeader) {
    headerDiv = document.createElement('div');
    headerDiv.className = 'lib-album-header header-enter';
    const coverEl = document.createElement('div');
    coverEl.className = 'lib-album-header-cover ico';
    if (coverSrc) {
      const img = document.createElement('img');
      img.src = coverSrc;
      img.alt = '';
      coverEl.appendChild(img);
    } else {
      coverEl.innerHTML = opts.headerIcon ? (ICON[opts.headerIcon] || ICON.note) : ICON.note;
    }
    headerDiv.appendChild(coverEl);

    // Total duration of the album/playlist
    const totalSecs = tracks.reduce((s, t) => s + (t.duration || 0), 0);
    const durStr    = _fmtTotalDuration(totalSecs);
    const meta      = `${tracks.length} track${tracks.length !== 1 ? 's' : ''}`
                    + (durStr ? ` · ${durStr}` : '');

    // The album title acts as a link that offers to (re)queue the album
    const _albId = alb.deezer_album_id
      || (alb.deezer_url && (alb.deezer_url.match(/\/album\/(\d+)/) || [])[1]) || '';
    const canQueue  = !!_albId && !alb.is_playlist;
    const titleHtml = canQueue
      ? `<span class="lib-album-link" id="lib-alb-title" title="Add this album to the download queue">${escHtml(alb.album)}</span>`
      : escHtml(alb.album);

    const infoDiv = document.createElement('div');
    infoDiv.innerHTML = `
      <div style="font-size:15px;font-weight:700;color:var(--fg);letter-spacing:-0.01em">${titleHtml}</div>
      <div style="font-size:11px;color:var(--accent);font-family:var(--mono);margin-top:3px">${escHtml(alb.artist)}</div>
      ${opts.hideMeta ? '' : `<div style="font-size:10px;color:var(--fg3);font-family:var(--mono);margin-top:2px">${meta}</div>`}
    `;
    // Export-as-m3u — only for local folders (those backed by a path_hash)
    if (alb.path_hash) {
      const exp = document.createElement('button');
      exp.className = 'btn-secondary';
      exp.style.cssText = 'width:auto;margin-top:6px;font-size:10px';
      exp.textContent = 'Export .m3u';
      exp.addEventListener('click', () => {
        const url = `/export-m3u?path_hash=${encodeURIComponent(alb.path_hash)}&name=${encodeURIComponent(alb.album || 'playlist')}`;
        const a = document.createElement('a');
        a.href = url; a.download = `${alb.album || 'playlist'}.m3u`;
        document.body.appendChild(a); a.click(); a.remove();
      });
      infoDiv.appendChild(exp);
    }
    headerDiv.appendChild(infoDiv);

    if (canQueue) {
      infoDiv.querySelector('#lib-alb-title').addEventListener('click', async () => {
        const ok = await _confirm(`Add "${alb.album}" by ${alb.artist} to the download queue?`,
          { title: 'Add to queue', confirmLabel: 'Add to queue' });
        if (!ok) return;
        entries.push({
          url: `https://www.deezer.com/album/${_albId}`,
          artist: alb.artist, albumTitle: alb.album,
          coverUrl: alb.cover_url || null, nbTracks: tracks.length,
          isPlaylist: false, playlistName: '',
        });
        renderQueue();
        _toast(`Added "${alb.album}" to the queue.`, 'success');
      });
    }
  }

  // Track rows
  const listDiv = document.createElement('div');
  listDiv.className = 'lib-track-list-wrap' + (opts.showAlbum ? ' cols-playlist' : '')
    + (showThumb ? '' : ' no-thumb');

  // Drag-to-reorder is enabled only inside a real playlist folder (not Liked
  // Songs, which has no path_hash and is ordered by date added).
  const reorderable = !!(alb && alb.is_playlist && alb.path_hash);
  let _plDragFrom = -1;

  // Column header row (Spotify-style) — for playlists with album/date columns
  if (opts.showAlbum) {
    const head = document.createElement('div');
    head.className = 'lib-track-head';
    head.innerHTML = `
      <span class="lib-track-index">#</span>
      <span class="lib-track-thumb" style="background:none"></span>
      <span class="lib-track-main">Title</span>
      <span class="lib-track-album">Album</span>
      <span class="lib-track-date">Date added</span>
      <span></span>
      <span class="lib-track-dur">${ICON.clock}</span>
    `;
    listDiv.appendChild(head);
  }

  tracks.forEach((track, i) => {
    const row = document.createElement('div');
    row.className = 'lib-track-row';
    row.dataset.idx = i;

    const hasLocal   = !!((track.path_hash || alb?.path_hash) && track.name);
    const hasPreview = !!track.preview_url;
    const canPlay    = hasLocal || hasPreview;
    const liked      = _isTrackLiked(track, alb);
    if (!canPlay) row.classList.add('no-play');

    // Per-track album-art thumbnail (embedded art via /track-cover)
    const tph = track.path_hash || alb?.path_hash || '';
    const thumbInner = tph
      ? `<img src="/track-cover?path_hash=${encodeURIComponent(tph)}&name=${encodeURIComponent(track.name)}"
              alt="" loading="lazy"
              onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
         <span class="ico" style="display:none">${ICON.note}</span>`
      : `<span class="ico">${ICON.note}</span>`;

    // Title cell: title over optional artist subline (+ star-rating badge)
    const rating = _trackRating(track, alb);
    const mainCell = `
      <span class="lib-track-main">
        <span class="lib-track-title" title="${escHtml(track.title || track.name)}">${escHtml(track.title || track.name)}${rating ? `<span class="lib-track-stars" title="Rated ${rating}/5">${'★'.repeat(rating)}</span>` : ''}</span>
        ${opts.showArtist ? `<span class="lib-track-sub">${escHtml(track.artist || alb?.artist || '')}</span>` : ''}
      </span>`;

    // Album (clickable → queue that album) + date columns appear only for playlists
    const albName  = track.album || '';
    const albArt   = track.artist || alb?.artist || '';
    const albumCell = opts.showAlbum
      ? `<span class="lib-track-album${albName ? ' clickable' : ''}"
              data-album="${escHtml(albName)}" data-artist="${escHtml(albArt)}"
              title="${albName ? escHtml(albName) + ' — click to add to queue' : ''}">${escHtml(albName)}</span>` : '';
    const dateCell  = opts.showAlbum
      ? `<span class="lib-track-date">${opts.showDate ? escHtml(_fmtDateAdded(track.date_added)) : ''}</span>` : '';

    const likeBtn = `<button class="lib-track-like ico${liked ? ' liked' : ''}"
              title="${liked ? 'Remove from Liked Songs' : 'Add to Liked Songs'}"
              data-idx="${i}">${liked ? ICON.heartFilled : ICON.heart}</button>`;

    row.innerHTML = `
      <span class="lib-track-index">
        <span class="lib-track-num">${track.track_num || (i + 1)}</span>
        <button class="lib-track-play${canPlay ? ' ico' : ' no-preview'}"
                title="${canPlay ? 'Play' : 'No audio available'}"
                data-idx="${i}">${ICON.play}</button>
      </span>
      ${showThumb ? `<span class="lib-track-thumb">${thumbInner}</span>` : ''}
      ${mainCell}
      ${albumCell}
      ${dateCell}
      ${likeBtn}
      <span class="lib-track-dur">${track.duration_str || '—'}</span>
    `;

    // Like button (always available)
    row.querySelector('.lib-track-like').addEventListener('click', async ev => {
      ev.stopPropagation();
      const btn = ev.currentTarget;
      const nowLiked = await _toggleLike(track, alb);
      btn.classList.toggle('liked', nowLiked);
      btn.innerHTML = nowLiked ? ICON.heartFilled : ICON.heart;
      btn.title = nowLiked ? 'Remove from Liked Songs' : 'Add to Liked Songs';
      // Keep the mini-player heart in sync if this is the playing track
      if (_mpIdx === i && _mpCurrentAlb === alb) _mpUpdateLikeBtn(nowLiked);
    });

    // Per-track album name → queue that album
    const _albEl = row.querySelector('.lib-track-album.clickable');
    if (_albEl) _albEl.addEventListener('click', ev => {
      ev.stopPropagation();
      _queueAlbumByName(_albEl.dataset.artist, _albEl.dataset.album);
    });

    if (canPlay) {
      // Play/pause toggle (used by the play button)
      const togglePlay = () => {
        const isThisAlbumPlaying = _mpCurrentAlb === alb;
        if (isThisAlbumPlaying && _mpIdx === i && _mpAudio.src) {
          if (_mpPlaying) {
            _mpAudio.pause();
          } else {
            _mpAudio.play().catch(() => {});
          }
        } else {
          _mpQueue = tracks;
          _mpPlayTrack(i, alb);
        }
      };
      row.querySelector('.lib-track-play').addEventListener('click', ev => {
        ev.stopPropagation();
        togglePlay();
      });
      // Double-click anywhere on the row starts the song
      row.addEventListener('dblclick', () => {
        _mpQueue = tracks;
        _mpPlayTrack(i, alb);
      });
    }

    // Single-click selection (Spotify-style): click = select, shift = range,
    // ctrl/cmd = toggle. Ignores clicks on the row's interactive controls.
    row.addEventListener('click', (ev) => {
      if (ev.target.closest('.lib-track-play, .lib-track-like, .lib-track-album.clickable')) return;
      if (ev.shiftKey && _trackSelAnchor >= 0) {
        const a = Math.min(_trackSelAnchor, i), b = Math.max(_trackSelAnchor, i);
        _trackSel = new Set();
        for (let k = a; k <= b; k++) _trackSel.add(k);
      } else if (ev.ctrlKey || ev.metaKey) {
        if (_trackSel.has(i)) _trackSel.delete(i); else _trackSel.add(i);
        _trackSelAnchor = i;
      } else {
        _trackSel = new Set([i]); _trackSelAnchor = i;
      }
      _applyTrackSelection();
    });

    // Right-click → context menu for the current selection
    row.addEventListener('contextmenu', (ev) => {
      ev.preventDefault();
      if (!_trackSel.has(i)) { _trackSel = new Set([i]); _trackSelAnchor = i; _applyTrackSelection(); }
      _openTrackCtx(row, tracks, alb, opts, { x: ev.clientX, y: ev.clientY });
    });

    // ── Drag-to-reorder (playlists only) ──
    if (reorderable) {
      row.draggable = true;
      row.classList.add('reorderable');
      const _clearDropMarks = () => listDiv.querySelectorAll('.drop-before, .drop-after')
        .forEach(r => r.classList.remove('drop-before', 'drop-after'));

      row.addEventListener('dragstart', (ev) => {
        _plDragFrom = i;
        ev.dataTransfer.effectAllowed = 'move';
        try { ev.dataTransfer.setData('text/plain', String(i)); } catch (_) {}
        requestAnimationFrame(() => row.classList.add('dragging-track'));
      });
      row.addEventListener('dragend', () => {
        row.classList.remove('dragging-track');
        _clearDropMarks();
      });
      row.addEventListener('dragover', (ev) => {
        if (_plDragFrom < 0) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
        const rect  = row.getBoundingClientRect();
        const after = (ev.clientY - rect.top) > rect.height / 2;
        _clearDropMarks();
        row.classList.add(after ? 'drop-after' : 'drop-before');
      });
      row.addEventListener('drop', (ev) => {
        ev.preventDefault();
        _clearDropMarks();
        const from = _plDragFrom; _plDragFrom = -1;
        if (from < 0 || from === i) return;
        const rect  = row.getBoundingClientRect();
        const after = (ev.clientY - rect.top) > rect.height / 2;
        let to = i + (after ? 1 : 0);
        if (from < to) to -= 1;          // account for the removed source
        if (to === from || to < 0) return;

        const moved = tracks.splice(from, 1)[0];
        tracks.splice(to, 0, moved);
        tracks.forEach((t, k) => { t.track_num = k + 1; });   // renumber locally

        // Keep the active playback queue + highlight consistent
        if (_mpCurrentAlb === alb) {
          const playing = _mpQueue[_mpIdx];
          _mpQueue = tracks;
          if (playing) { const ni = tracks.indexOf(playing); if (ni >= 0) _mpIdx = ni; }
        }
        // Re-render in place: no entrance animation, preserve scroll position
        const _sc = document.getElementById('lib-content')?.scrollTop || 0;
        _renderLibTracks(tracks, alb, { ...opts, _skipAnim: true });
        const _c = document.getElementById('lib-content');
        if (_c) _c.scrollTop = _sc;

        // Persist the new order (rewrites track-number tags on disk)
        fetch('/playlist-reorder', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path_hash: alb.path_hash, names: tracks.map(t => t.name).filter(Boolean) }),
        }).then(r => r.json()).then(d => { if (d && d.error) _toast(d.error, 'error'); })
          .catch(() => _toast('Could not save the new order.', 'error'));
      });
    }

    // Staggered entrance animation (skipped on in-place re-renders like reorder)
    if (!opts._skipAnim) {
      row.style.animationDelay = `${i * 28}ms`;
      row.classList.add('track-enter');
    }
    listDiv.appendChild(row);
  });

  content.innerHTML = '';
  if (headerDiv) content.appendChild(headerDiv);
  content.appendChild(listDiv);
}

// ── Liked Songs view ─────────────────────────────────────────────────────────
async function _renderLikedSongs(artist) {
  const content = document.getElementById('lib-content');
  content.innerHTML = '<div class="stat-empty">Loading liked songs…</div>';
  let tracks = [];
  try {
    const r = await fetch('/liked-songs');
    const d = await r.json();
    tracks = d.tracks || [];
  } catch (e) {
    content.innerHTML = `<div class="stat-empty" style="color:var(--red)">Error: ${escHtml(String(e))}</div>`;
    return;
  }
  // Keep the in-memory liked set fresh
  _likedKeys = new Set(tracks.map(t => (t.path_hash || '') + '\x00' + (t.name || '')));

  // Synthetic album wrapper (stable singleton so playback highlight survives
  // re-renders) — each track carries its own path_hash/cover_url.
  _likedAlbum.artist = `${tracks.length} song${tracks.length !== 1 ? 's' : ''}`;
  _libActiveAlbum = _likedAlbum;
  _renderLibTracks(tracks, _likedAlbum, {
    showArtist: true,
    headerIcon: 'heartFilled',
    hideMeta: true,   // the artist line already shows "N songs" — avoid duplicate count
    emptyMsg: 'No liked songs yet. Tap the heart on any track to add it here.',
  });
}

// ── Filter handler for library ─────────────────────────────────────────────
// ── Artist sidebar filter ───────────────────────────────────────────────────
function _libApplyArtistFilter() {
  const filter = (document.getElementById('lib-filter')?.value || '').trim().toLowerCase();
  const list   = document.getElementById('lib-artist-list');
  if (!list) return;

  // Show/hide artist rows based on name match
  list.querySelectorAll('.lib-artist-row').forEach(row => {
    const name = (row.dataset.artist || '').toLowerCase();
    row.style.display = (!filter || name.includes(filter)) ? '' : 'none';
  });
}

// ── Album filter within current artist view ─────────────────────────────────
function _libApplyAlbumFilter() {
  if (!_libActiveArtist || _libView !== 'albums') return;
  const filter = (document.getElementById('lib-album-filter')?.value || '').trim().toLowerCase();
  const visible = filter
    ? _libActiveArtist.albums.filter(a => a.album.toLowerCase().includes(filter))
    : _libActiveArtist.albums;
  _renderLibAlbums(visible, _libActiveArtist._discog || null);
}

// ── Legacy alias so any remaining call-sites don't break ───────────────────
function _libApplyFilter() {
  _libApplyArtistFilter();
}

// ═══════════════════════════════════════
