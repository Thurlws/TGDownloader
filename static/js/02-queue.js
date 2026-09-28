//  QUEUE RENDERING
// ═══════════════════════════════════════
function renderQueue() {
  const list  = document.getElementById('queue-list');
  const count = document.getElementById('q-count');

  count.textContent = entries.length
    ? `${entries.length} item${entries.length !== 1 ? 's' : ''}`
    : '0 items';

  // Sync library "queued" badge state with current entries
  _syncLibQueuedState();
  _updateRetryFailedBtn();

  if (!entries.length) {
    list.className = '';
    list.innerHTML = '<div class="queue-empty">Queue is empty</div>';
    updateEtaIndicator();
    saveWorkingQueue();
    return;
  }

  if (viewMode === 'list')       renderQueueList(list);
  else if (viewMode === 'grid')  renderQueueGrid(list);
  else                           renderQueueGrouped(list);

  updateEtaIndicator();
  saveWorkingQueue();
  if (activeTab === 'stats') renderStats();
}

function renderQueueList(list) {
  list.className = '';
  list.innerHTML = '';

  entries.forEach((e, i) => {
    const item = document.createElement('div');
    item.className = 'queue-item' + (e.source === 'spotify' ? ' from-spotify' : '');
    item.draggable = true;
    item.tabIndex = 0;
    item.dataset.idx = i;

    const result      = sessionResults[e.url];
    const meta        = getEntryMeta(e.url);
    const displayName = e.albumTitle || (e.url.length > 34 ? e.url.slice(0, 31) + '\u2026' : e.url);
    const isDone      = manifestUrls.has(e.url);
    const badge       = result ? getBadge(result, i) : '';

    const tagHtml = [
      meta.tags.includes('fav') ? '<span class="tag-pill tag-fav">fav</span>' : '',
      meta.tags.includes('rev') ? '<span class="tag-pill tag-rev">later</span>' : '',
    ].filter(Boolean).join('');

    const metaHtml = (tagHtml || meta.note)
      ? `<div class="q-meta-row">
          ${tagHtml}
          ${meta.note ? `<span class="q-note-pill" title="${escHtml(meta.note)}">${escHtml(meta.note)}</span>` : ''}
         </div>`
      : '';

    item.innerHTML = `
      <span class="drag-handle" title="Drag to reorder">\u2807</span>
      <span class="q-num">${i + 1}</span>
      <div class="q-info">
        <div class="q-title-row">
          <span class="q-name">${escHtml(displayName)}</span>
          <span class="q-url-field" title="Click to copy URL">${escHtml(e.url)}</span>
          ${isDone ? '<span class="manifest-ok" title="Already in library">\u2713</span>' : ''}
          ${badge}
        </div>
        <div class="q-artist">${escHtml(e.artist)}</div>
        ${metaHtml}
      </div>
      <button class="q-remove" title="Remove">\u00d7</button>
    `;

    item.querySelector('.q-url-field').addEventListener('click', (ev) => {
      ev.stopPropagation();
      const field = ev.currentTarget;
      navigator.clipboard.writeText(e.url).then(() => {
        field.textContent = '\u2713 copied';
        field.classList.add('copied');
        setTimeout(() => { field.textContent = e.url; field.classList.remove('copied'); }, 1200);
      }).catch(() => {});
    });

    const retryBtn = item.querySelector('.btn-retry');
    if (retryBtn) retryBtn.addEventListener('click', (ev) => { ev.stopPropagation(); retryEntry(i); });

    item.addEventListener('contextmenu', (ev) => { ev.preventDefault(); openCtxMenu(ev, i); });
    item.querySelector('.q-remove').addEventListener('click', (ev) => {
      ev.stopPropagation();
      entries.splice(i, 1);
      renderQueue();
    });

    // Improved drag: insert before/after based on cursor position in item
    item.addEventListener('dragstart', (ev) => {
      dragSrcIdx = i; ev.dataTransfer.effectAllowed = 'move';
      requestAnimationFrame(() => item.classList.add('dragging'));
    });
    item.addEventListener('dragover', (ev) => {
      ev.preventDefault(); ev.dataTransfer.dropEffect = 'move';
      list.querySelectorAll('.queue-item').forEach(el => el.classList.remove('drag-insert-before', 'drag-insert-after', 'drag-target'));
      if (i !== dragSrcIdx) {
        const rect = item.getBoundingClientRect();
        const mid  = rect.top + rect.height / 2;
        item.classList.add(ev.clientY < mid ? 'drag-insert-before' : 'drag-insert-after');
      }
    });
    item.addEventListener('dragleave', () => item.classList.remove('drag-insert-before', 'drag-insert-after'));
    item.addEventListener('drop', (ev) => {
      ev.preventDefault();
      const isBefore = item.classList.contains('drag-insert-before');
      item.classList.remove('drag-insert-before', 'drag-insert-after');
      if (dragSrcIdx === null || dragSrcIdx === i) return;
      const [moved] = entries.splice(dragSrcIdx, 1);
      // After splice, recalculate target index
      let targetIdx = i;
      if (dragSrcIdx < i) targetIdx = i - 1; // shifted left after removing src
      entries.splice(isBefore ? targetIdx : targetIdx + 1, 0, moved);
      dragSrcIdx = null; renderQueue();
    });
    item.addEventListener('dragend', () => {
      dragSrcIdx = null;
      list.querySelectorAll('.queue-item').forEach(el =>
        el.classList.remove('dragging', 'drag-insert-before', 'drag-insert-after', 'drag-target'));
    });

    // Keyboard navigation
    item.addEventListener('keydown', (ev) => {
      const items = [...list.querySelectorAll('.queue-item')];
      const idx   = items.indexOf(ev.currentTarget);
      if (ev.key === 'ArrowDown') { ev.preventDefault(); items[idx + 1]?.focus(); }
      if (ev.key === 'ArrowUp')   { ev.preventDefault(); items[idx - 1]?.focus(); }
      if (ev.key === 'Home') { ev.preventDefault(); items[0]?.focus(); }
      if (ev.key === 'End')  { ev.preventDefault(); items[items.length - 1]?.focus(); }
      if ((ev.key === 'Delete' || ev.key === 'Backspace') && !ev.shiftKey) {
        ev.preventDefault();
        entries.splice(i, 1); renderQueue();
        // Focus next or prev item
        const nextFocus = list.querySelectorAll('.queue-item')[Math.min(i, entries.length - 1)];
        nextFocus?.focus();
      }
      if (ev.key === 'ArrowUp' && ev.shiftKey) {
        ev.preventDefault();
        if (i > 0) { [entries[i], entries[i-1]] = [entries[i-1], entries[i]]; renderQueue(); list.querySelectorAll('.queue-item')[i-1]?.focus(); }
      }
      if (ev.key === 'ArrowDown' && ev.shiftKey) {
        ev.preventDefault();
        if (i < entries.length - 1) { [entries[i], entries[i+1]] = [entries[i+1], entries[i]]; renderQueue(); list.querySelectorAll('.queue-item')[i+1]?.focus(); }
      }
    });
    item.addEventListener('focus', () => {
      list.querySelectorAll('.queue-item').forEach(el => el.classList.remove('kb-focus'));
      item.classList.add('kb-focus');
    });
    item.addEventListener('blur', () => item.classList.remove('kb-focus'));

    list.appendChild(item);
  });
}

function renderQueueGrid(list) {
  list.className = 'queue-grid';
  list.innerHTML = '';
  entries.forEach((e, i) => {
    const card   = document.createElement('div');
    card.className = 'grid-card';
    const result  = sessionResults[e.url];
    const badge   = result ? getBadge(result, i) : '';
    const title   = e.albumTitle || e.url.split('/').pop() || '\u2014';
    const isDone  = manifestUrls.has(e.url);

    card.innerHTML = `
      ${e.coverUrl
        ? `<img class="grid-cover" src="${escHtml(e.coverUrl)}" alt="" loading="lazy">`
        : `<div class="grid-cover-ph">\u266a</div>`}
      <div class="grid-info">
        <div class="grid-title">${isDone ? '\u2713 ' : ''}${escHtml(title)}</div>
        <div class="grid-artist">${escHtml(e.artist)}</div>
        ${e.nbTracks ? `<div class="grid-tracks">${e.nbTracks} tracks</div>` : ''}
        ${badge ? `<div style="margin-top:4px">${badge}</div>` : ''}
      </div>
      <button class="grid-remove kebab-btn" title="More options" style="position:absolute;top:4px;right:4px;opacity:0;pointer-events:none;">\u22ef</button>
    `;

    card.addEventListener('contextmenu', (ev) => { ev.preventDefault(); openCtxMenu(ev, i); });
    card.querySelector('.grid-remove').addEventListener('click', (ev) => {
      ev.stopPropagation(); openCtxMenu(ev, i);
    });
    // Show kebab on hover via pointer events override
    card.addEventListener('mouseenter', () => { const b = card.querySelector('.grid-remove'); if (b) { b.style.opacity='1'; b.style.pointerEvents='auto'; } });
    card.addEventListener('mouseleave', () => { const b = card.querySelector('.grid-remove'); if (b) { b.style.opacity='0'; b.style.pointerEvents='none'; } });
    list.appendChild(card);
  });
}

function renderQueueGrouped(list) {
  list.className = '';
  list.innerHTML = '';

  const groups = {};
  entries.forEach((e, i) => {
    const key = e.artist || '(no artist)';
    if (!groups[key]) groups[key] = [];
    groups[key].push({ entry: e, idx: i });
  });

  Object.keys(groups).sort().forEach(artist => {
    const grp       = groups[artist];
    const collapsed = collapsedArtists.has(artist);

    const header = document.createElement('div');
    header.className = 'group-header';
    header.innerHTML = `
      <span class="group-chevron">${collapsed ? '\u25b6' : '\u25bc'}</span>
      <span class="group-artist">${escHtml(artist)}</span>
      <span class="group-count">${grp.length}</span>
    `;
    header.addEventListener('click', () => {
      if (collapsedArtists.has(artist)) collapsedArtists.delete(artist);
      else collapsedArtists.add(artist);
      renderQueue();
    });
    list.appendChild(header);

    if (!collapsed) {
      grp.forEach(({ entry: e, idx }) => {
        const item = document.createElement('div');
        item.className = 'queue-item indented' + (e.source === 'spotify' ? ' from-spotify' : '');
        item.draggable = true;
        item.tabIndex = 0;

        const result      = sessionResults[e.url];
        const meta        = getEntryMeta(e.url);
        const displayName = e.albumTitle || (e.url.length > 30 ? e.url.slice(0, 27) + '\u2026' : e.url);
        const isDone      = manifestUrls.has(e.url);
        const badge       = result ? getBadge(result, idx) : '';

        const tagHtml = [
          meta.tags.includes('fav') ? '<span class="tag-pill tag-fav">fav</span>' : '',
          meta.tags.includes('rev') ? '<span class="tag-pill tag-rev">later</span>' : '',
        ].filter(Boolean).join('');

        item.innerHTML = `
          <span class="drag-handle">\u2807</span>
          <span class="q-num">${idx + 1}</span>
          <div class="q-info">
            <div class="q-title-row">
              <span class="q-name">${escHtml(displayName)}</span>
              <span class="q-url-field" title="Click to copy URL">${escHtml(e.url)}</span>
              ${isDone ? '<span class="manifest-ok">\u2713</span>' : ''}
              ${tagHtml}
              ${badge}
            </div>
            ${e.nbTracks ? `<div style="font-size:10px;color:var(--fg3);font-family:var(--mono)">${e.nbTracks} tracks</div>` : ''}
          </div>
          <button class="q-remove" title="Remove">\u00d7</button>
        `;

        item.querySelector('.q-url-field').addEventListener('click', (ev) => {
          ev.stopPropagation();
          const field = ev.currentTarget;
          navigator.clipboard.writeText(e.url).then(() => {
            field.textContent = '\u2713 copied';
            field.classList.add('copied');
            setTimeout(() => { field.textContent = e.url; field.classList.remove('copied'); }, 1200);
          }).catch(() => {});
        });

        const retryBtn = item.querySelector('.btn-retry');
        if (retryBtn) retryBtn.addEventListener('click', (ev) => { ev.stopPropagation(); retryEntry(idx); });

        item.addEventListener('contextmenu', (ev) => { ev.preventDefault(); openCtxMenu(ev, idx); });
        item.querySelector('.q-remove').addEventListener('click', (ev) => {
          ev.stopPropagation();
          entries.splice(idx, 1);
          renderQueue();
        });
        item.addEventListener('dragstart', (ev) => {
          dragSrcIdx = idx; ev.dataTransfer.effectAllowed = 'move';
          requestAnimationFrame(() => item.classList.add('dragging'));
        });
        item.addEventListener('dragover', (ev) => {
          ev.preventDefault();
          list.querySelectorAll('.queue-item').forEach(el => el.classList.remove('drag-insert-before', 'drag-insert-after'));
          if (idx !== dragSrcIdx) {
            const rect = item.getBoundingClientRect();
            item.classList.add(ev.clientY < rect.top + rect.height / 2 ? 'drag-insert-before' : 'drag-insert-after');
          }
        });
        item.addEventListener('dragleave', () => item.classList.remove('drag-insert-before', 'drag-insert-after'));
        item.addEventListener('drop', (ev) => {
          ev.preventDefault();
          const isBefore = item.classList.contains('drag-insert-before');
          item.classList.remove('drag-insert-before', 'drag-insert-after');
          if (dragSrcIdx === null || dragSrcIdx === idx) return;
          const [moved] = entries.splice(dragSrcIdx, 1);
          let targetIdx = idx;
          if (dragSrcIdx < idx) targetIdx = idx - 1;
          entries.splice(isBefore ? targetIdx : targetIdx + 1, 0, moved);
          dragSrcIdx = null; renderQueue();
        });
        item.addEventListener('dragend', () => {
          dragSrcIdx = null;
          list.querySelectorAll('.queue-item').forEach(el =>
            el.classList.remove('dragging', 'drag-insert-before', 'drag-insert-after'));
        });
        item.addEventListener('keydown', (ev) => {
          const items = [...list.querySelectorAll('.queue-item')];
          const pos   = items.indexOf(ev.currentTarget);
          if (ev.key === 'ArrowDown') { ev.preventDefault(); items[pos + 1]?.focus(); }
          if (ev.key === 'ArrowUp')   { ev.preventDefault(); items[pos - 1]?.focus(); }
          if (ev.key === 'Delete' || ev.key === 'Backspace') {
            ev.preventDefault(); entries.splice(idx, 1); renderQueue();
          }
        });
        item.addEventListener('focus', () => {
          list.querySelectorAll('.queue-item').forEach(el => el.classList.remove('kb-focus'));
          item.classList.add('kb-focus');
        });
        item.addEventListener('blur', () => item.classList.remove('kb-focus'));
        list.appendChild(item);
      });
    }
  });
}

function getBadge(result, idx) {
  const map = {
    ok:      `<span class="status-badge badge-ok ico-badge">${ICON.check}</span>`,
    partial: '<span class="status-badge badge-partial">~</span>',
    skipped: '<span class="status-badge badge-skip">skip</span>',
    error:   `<span class="status-badge badge-error ico-badge">${ICON.close}</span><button class="btn-retry" data-idx="${idx}" title="Retry this URL"><span class="bi">${ICON.refresh}</span>retry</button>`,
  };
  return map[result.status] || '';
}


// ═══════════════════════════════════════
//  CONTEXT MENU
// ═══════════════════════════════════════
function openCtxMenu(ev, idx) {
  ctxIdx = idx;
  const menu = document.getElementById('ctx-menu');
  const meta = getEntryMeta(entries[idx].url);

  document.getElementById('ctx-fav').querySelector('.ctx-label').textContent =
    meta.tags.includes('fav') ? 'Remove Favorite' : 'Toggle Favorite';
  document.getElementById('ctx-rev').querySelector('.ctx-label').textContent =
    meta.tags.includes('rev') ? 'Remove Review Later' : 'Toggle Review Later';

  menu.classList.add('open');
  menu.style.visibility = 'hidden';
  menu.style.left = '0px';
  menu.style.top = '0px';

  requestAnimationFrame(() => {
    // #ctx-menu is position:absolute inside the CSS-scaled #app.
    // ev.pageX/pageY are in document coords; dividing by scale converts them
    // into the #app-local coordinate space where left/top are applied.
    const scale = parseFloat(
      document.getElementById('app').style.transform?.match(/scale\(([^)]+)\)/)?.[1] || '1'
    ) || 1;
    const appRect = document.getElementById('app').getBoundingClientRect();
    // Convert mouse position to coords within the scaled #app element
    let x = (ev.clientX - appRect.left) / scale;
    let y = (ev.clientY - appRect.top)  / scale;
    // Clamp so menu doesn't overflow the visible area
    const menuW = menu.offsetWidth  || 180;
    const menuH = menu.offsetHeight || 220;
    const maxX  = document.getElementById('app').offsetWidth  - menuW - 4;
    const maxY  = document.getElementById('app').offsetHeight - menuH - 4;
    if (x > maxX) x = maxX;
    if (y > maxY) y = maxY;
    if (x < 4)    x = 4;
    if (y < 4)    y = 4;
    menu.style.left = x + 'px';
    menu.style.top  = y + 'px';
    menu.style.visibility = 'visible';
  });
}

function closeCtxMenu() {
  document.getElementById('ctx-menu').classList.remove('open');
  ctxIdx = null;
}


// ═══════════════════════════════════════
//  KEBAB MENU  (shared floating dropdown for library cards)
// ═══════════════════════════════════════
let _kebabDropdown = null;

function _getKebabDropdown() {
  if (!_kebabDropdown) _kebabDropdown = document.getElementById('kebab-dropdown');
  return _kebabDropdown;
}

function closeKebabMenu() {
  const dd = _getKebabDropdown();
  if (dd) { dd.classList.remove('open'); dd.innerHTML = ''; }
  _kebabDropdown && (_kebabDropdown._cleanup?.());
}

// openKebabMenu(anchorEl, items, opts)
//   opts.atPoint = {x, y}  → open at a cursor point (viewport coords) instead
//                            of below the anchor element (used for right-click
//                            context menus so the menu appears at the pointer).
// Item shapes:
//   { label, icon, action, danger }                  : normal item
//   { divider: true }                                : separator
//   { label, icon, submenu: [...] | () => [...] }    : drills into a submenu
function openKebabMenu(anchorEl, items, opts) {
  closeKebabMenu();
  const dd = _getKebabDropdown();
  if (!dd) return;
  opts = opts || {};

  const appEl = document.getElementById('app');

  // Render a level of the menu (supports drill-in submenus + a Back row)
  function _renderLevel(levelItems, backTo) {
    dd.innerHTML = '';
    if (backTo) {
      const back = document.createElement('div');
      back.className = 'kebab-item kebab-back';
      back.innerHTML = `<span class="kebab-ico ico">${ICON.chevronLeft || '‹'}</span>${escHtml(backTo.title || 'Back')}`;
      back.addEventListener('click', (ev) => {
        ev.stopPropagation();
        _renderLevel(backTo.items, backTo.parent || null);
        _reposition();
      });
      dd.appendChild(back);
      const sep = document.createElement('div');
      sep.className = 'kebab-divider';
      dd.appendChild(sep);
    }
    levelItems.forEach(item => {
      if (item.divider) {
        const sep = document.createElement('div');
        sep.className = 'kebab-divider';
        dd.appendChild(sep);
        return;
      }
      const el = document.createElement('div');
      const hasSub = !!item.submenu;
      el.className = 'kebab-item' + (item.danger ? ' danger' : '') + (hasSub ? ' has-sub' : '');
      el.innerHTML =
        `${item.icon ? `<span class="kebab-ico ico">${item.icon}</span>` : ''}` +
        `<span class="kebab-label">${escHtml(item.label)}</span>` +
        `${hasSub ? `<span class="kebab-arrow ico">${ICON.chevronRight || '›'}</span>` : ''}`;
      el.addEventListener('click', (ev) => {
        if (hasSub) {
          ev.stopPropagation();
          const sub = typeof item.submenu === 'function' ? item.submenu() : item.submenu;
          _renderLevel(sub, { title: 'Back', items: levelItems, parent: backTo });
          _reposition();
          return;
        }
        closeKebabMenu();
        item.action && item.action();
      });
      dd.appendChild(el);
    });
  }

  // Position the dropdown: at a cursor point, else below the anchor element.
  // kebab-dropdown is position:absolute inside the CSS-scaled #app, so convert
  // viewport coords to #app-local unscaled coords (divide offsets by scale).
  function _reposition() {
    dd.style.visibility = 'hidden';
    requestAnimationFrame(() => {
      const scale = parseFloat(
        appEl.style.transform?.match(/scale\(([^)]+)\)/)?.[1] || '1'
      ) || 1;
      const appRect = appEl.getBoundingClientRect();

      let x, y, altX, altY;
      if (opts.atPoint) {
        x = altX = (opts.atPoint.x - appRect.left) / scale;
        y = altY = (opts.atPoint.y - appRect.top)  / scale;
      } else {
        const anchor = anchorEl.getBoundingClientRect();
        x = (anchor.left   - appRect.left) / scale;
        y = (anchor.bottom - appRect.top)  / scale;
        altX = (anchor.right - appRect.left) / scale;
        altY = (anchor.top   - appRect.top)  / scale;
      }

      const ddW  = dd.offsetWidth  || 160;
      const ddH  = dd.offsetHeight || 160;
      const appW = appEl.offsetWidth;
      const appH = appEl.offsetHeight;

      // Flip if overflowing the right / bottom edges
      if (x + ddW > appW - 4) x = altX - ddW;
      if (y + ddH > appH - 4) y = altY - ddH;
      if (x < 4) x = 4;
      if (y < 4) y = 4;

      dd.style.left       = x + 'px';
      dd.style.top        = y + 'px';
      dd.style.visibility = 'visible';
    });
  }

  _renderLevel(items, null);
  dd.classList.add('open');
  dd.style.left = '0px';
  dd.style.top  = '0px';
  _reposition();

  // Close on any click outside the menu. Not {once:true} so the handler
  // survives submenu drill-ins (clicking a submenu trigger is "inside").
  function _outside(ev) {
    if (!dd.contains(ev.target) && ev.target !== anchorEl) closeKebabMenu();
  }
  dd._cleanup = () => {
    document.removeEventListener('click', _outside, true);
    dd._cleanup = null;
  };
  setTimeout(() => document.addEventListener('click', _outside, true), 0);
}


// ═══════════════════════════════════════
//  NOTE EDITOR
// ═══════════════════════════════════════
function openNoteEditor(idx) {
  noteIdx = idx;
  const meta = getEntryMeta(entries[idx].url);
  document.getElementById('note-input').value = meta.note || '';
  document.getElementById('note-overlay').classList.add('open');
  setTimeout(() => document.getElementById('note-input').focus(), 50);
}

function saveNote() {
  if (noteIdx === null) return;
  const val = document.getElementById('note-input').value.trim();
  getEntryMeta(entries[noteIdx].url).note = val;
  saveEntryMeta();
  document.getElementById('note-overlay').classList.remove('open');
  noteIdx = null;
  renderQueue();
}


// ═══════════════════════════════════════
//  ADD TO QUEUE
// ═══════════════════════════════════════
async function addEntry() {
  const urlEl = document.getElementById('url-input');
  const url   = urlEl.value.trim();

  if (!url) {
    flash(urlEl);
    return;
  }

  let artist       = 'Unknown Artist';
  let albumTitle   = null;
  let coverUrl     = null;
  let nbTracks     = null;
  let dlUrl        = url;          // URL actually sent to the download bot
  let source       = null;        // 'spotify' when resolved from a Spotify link
  let isPlaylist   = /\/playlist\//.test(url);  // route into the Playlists tab
  let playlistName = '';

  // Helper: absorb a normalised API response into local vars
  const absorb = (data) => {
    if (!data || data.error) return;
    artist     = data.artist?.name || 'Unknown Artist';
    albumTitle = data.title        || null;
    coverUrl   = data.cover_medium || data.cover_small || null;
    nbTracks   = data.nb_tracks    || null;
    // Spotify links are downloaded by the bot directly, keep the Spotify URL.
    if (data.source === 'spotify') { source = 'spotify'; if (data.link) dlUrl = data.link; }
    // Playlists go into <home>/Playlists/<name>/ instead of being split into
    // per-album folders under an artist.
    const _kind = data.kind || data.spotify_kind || '';
    if (_kind === 'playlist' || /\/playlist\//.test(dlUrl)) {
      isPlaylist   = true;
      playlistName = data.title || playlistName || '';
    }
  };

  const albumMatch   = url.match(/deezer\.com\/(?:[a-z]{2,3}\/)?album\/(\d+)/);
  const trackMatch   = !albumMatch && url.match(/deezer\.com\/(?:[a-z]{2,3}\/)?track\/(\d+)/);
  const shareMatch   = !albumMatch && !trackMatch && /link\.deezer\.com/.test(url);
  const spotifyMatch = !albumMatch && !trackMatch && !shareMatch && /spotify\.com/.test(url);
  const dzOtherMatch = !albumMatch && !trackMatch && !shareMatch && !spotifyMatch
                       && /deezer\.com\/(?:[a-z]{2,3}\/)?(?:playlist|artist)\/\d+/.test(url);

  try {
    if (albumMatch) {
      const resp = await fetch(`/album/${albumMatch[1]}`);
      absorb(await resp.json());
    } else if (trackMatch) {
      const resp = await fetch(`/track/${trackMatch[1]}`);
      absorb(await resp.json());
    } else if (shareMatch || spotifyMatch || dzOtherMatch) {
      const resp = await fetch(`/resolve?url=${encodeURIComponent(url)}`);
      const data = await resp.json();
      if ((spotifyMatch || dzOtherMatch) && data.error) {
        appendLog(`  ${data.error}\n`, 'log-error');
        flash(urlEl);
        return;
      }
      absorb(data);
    }
  } catch (e) {
    console.warn('Metadata fetch failed:', e);
  }

  // Ensure a folder name when this is a playlist (worker also has a fallback).
  if (isPlaylist && !playlistName) {
    const pm = url.match(/playlist\/([A-Za-z0-9]+)/);
    playlistName = pm ? ('Playlist ' + pm[1]) : 'Playlist';
  }

  entries.push({ url: dlUrl, artist, albumTitle, coverUrl, nbTracks, source,
                 isPlaylist, playlistName });
  lastArtist  = artist;
  urlEl.value = '';
  _updateUrlSource();
  renderQueue();
  urlEl.focus();
}

// ── Bulk URL import ────────────────────────────────────────────────────────
async function addBulkEntries() {
  const ta = document.getElementById('bulk-url-input');
  const btn = document.getElementById('btn-bulk-add');
  const rawLines = (ta.value || '').split('\n').map(l => l.trim()).filter(Boolean);
  const urls = [...new Set(rawLines)]; // deduplicate
  if (!urls.length) { flash(ta); return; }

  btn.disabled = true;
  _setBtnIco(btn, 'refresh', `0 / ${urls.length}`);

  const fetchMeta = async (url) => {
    let artist = 'Unknown Artist', albumTitle = null, coverUrl = null, nbTracks = null;
    let dlUrl = url, source = null;
    let isPlaylist = /\/playlist\//.test(url), playlistName = '';
    try {
      const albumMatch   = url.match(/deezer\.com\/(?:[a-z]{2,3}\/)?album\/(\d+)/);
      const trackMatch   = !albumMatch && url.match(/deezer\.com\/(?:[a-z]{2,3}\/)?track\/(\d+)/);
      const shareMatch   = !albumMatch && !trackMatch && /link\.deezer\.com/.test(url);
      const spotifyMatch = !albumMatch && !trackMatch && !shareMatch && /spotify\.com/.test(url);
      const dzOtherMatch = !albumMatch && !trackMatch && !shareMatch && !spotifyMatch
                           && /deezer\.com\/(?:[a-z]{2,3}\/)?(?:playlist|artist)\/\d+/.test(url);
      let data = null;
      if (albumMatch)                    data = await fetch(`/album/${albumMatch[1]}`).then(r => r.json());
      else if (trackMatch)               data = await fetch(`/track/${trackMatch[1]}`).then(r => r.json());
      else if (shareMatch || spotifyMatch || dzOtherMatch) data = await fetch(`/resolve?url=${encodeURIComponent(url)}`).then(r => r.json());
      if (data && !data.error) {
        artist     = data.artist?.name || 'Unknown Artist';
        albumTitle = data.title        || null;
        coverUrl   = data.cover_medium || data.cover_small || null;
        nbTracks   = data.nb_tracks    || null;
        // Spotify links are downloaded by the bot directly, keep the Spotify URL.
        if (data.source === 'spotify') { source = 'spotify'; if (data.link) dlUrl = data.link; }
        const _kind = data.kind || data.spotify_kind || '';
        if (_kind === 'playlist' || /\/playlist\//.test(dlUrl)) {
          isPlaylist   = true;
          playlistName = data.title || playlistName || '';
        }
      } else if ((spotifyMatch || dzOtherMatch) && data && data.error) {
        return null;  // unresolvable link, skip
      }
    } catch (_) {}
    if (isPlaylist && !playlistName) {
      const pm = url.match(/playlist\/([A-Za-z0-9]+)/);
      playlistName = pm ? ('Playlist ' + pm[1]) : 'Playlist';
    }
    return { url: dlUrl, artist, albumTitle, coverUrl, nbTracks, source, isPlaylist, playlistName };
  };

  // Fetch in batches of 4 so we don't hammer Deezer
  let done = 0;
  const batchSize = 4;
  const newEntries = [];
  for (let i = 0; i < urls.length; i += batchSize) {
    const batch = urls.slice(i, i + batchSize);
    const results = await Promise.all(batch.map(fetchMeta));
    newEntries.push(...results.filter(Boolean));
    done += batch.length;
    _setBtnIco(btn, 'refresh', `${Math.min(done, urls.length)} / ${urls.length}`);
  }

  // Skip duplicates already in queue
  const existingUrls = new Set(entries.map(e => e.url));
  const toAdd = newEntries.filter(e => !existingUrls.has(e.url));
  entries.push(...toAdd);

  ta.value = '';
  document.getElementById('bulk-count-label').textContent = '';
  btn.disabled = false;
  _setBtnIco(btn, 'plusCircle', 'Add All');

  renderQueue();
  appendLog(`  Bulk import: ${toAdd.length} added, ${newEntries.length - toAdd.length} skipped (already in queue)\n`, 'log-success');
  switchTab('log');
}


// ═══════════════════════════════════════
//  SESSIONS
// ═══════════════════════════════════════
async function loadSessions() {
  try {
    const resp = await fetch('/sessions');
    sessions   = await resp.json();
  } catch (_) { sessions = {}; }
  _renderSessionsModalList();
}

function _renderSessionsModalList() {
  const inner = document.getElementById('sessions-list-inner');
  if (!inner) return;
  const keys = Object.keys(sessions).sort();
  if (!keys.length) {
    inner.innerHTML = '<div id="sessions-empty">No saved sessions yet.</div>';
    return;
  }
  inner.innerHTML = '';
  keys.forEach(name => {
    const items = sessions[name] || [];
    const row = document.createElement('div');
    row.className = 'session-row';
    row.innerHTML = `
      <div class="session-row-name" title="${escHtml(name)}">${escHtml(name)}</div>
      <div class="session-row-count">${items.length} item${items.length !== 1 ? 's' : ''}</div>
      <div class="session-row-actions">
        <button class="session-act-btn load-btn" title="Load this session">${_btnIco('download', 'Load')}</button>
        <button class="session-act-btn ren-btn"  title="Rename this session">${_btnIco('pencil', 'Rename')}</button>
        <button class="session-act-btn del-btn"  title="Delete this session">${_btnIco('close')}</button>
      </div>
    `;
    _applyIcons(row);
    row.querySelector('.load-btn').addEventListener('click', () => {
      const doLoad = () => {
        entries = items.map(e => ({ ...e }));
        sessionResults = {};
        renderQueue();
        document.getElementById('sessions-overlay').classList.remove('open');
        appendLog(`Session loaded: "${name}"\n`, 'log-success');
      };
      if (entries.length) {
        showLoadSessionConfirm({
          sessionName: name,
          itemCount:   items.length,
          queueCount:  entries.length,
          onConfirm:   doLoad,
        });
      } else {
        doLoad();
      }
    });

    // ── Inline rename ──────────────────────────────────────────────────────
    row.querySelector('.ren-btn').addEventListener('click', () => {
      const nameEl  = row.querySelector('.session-row-name');
      const renBtn  = row.querySelector('.ren-btn');
      const loadBtn = row.querySelector('.load-btn');
      const delBtn  = row.querySelector('.del-btn');

      // Already in edit mode? Skip.
      if (row.querySelector('.session-rename-input')) return;

      const input = document.createElement('input');
      input.type = 'text';
      input.value = name;
      input.className = 'session-rename-input';
      input.style.cssText = `flex:1;background:var(--bg3);border:1px solid var(--accent);
        border-radius:2px;color:var(--fg);font-family:var(--mono);font-size:11px;
        padding:2px 6px;outline:none;min-width:0;`;

      const saveBtn = document.createElement('button');
      saveBtn.className = 'session-act-btn';
      saveBtn.style.cssText = 'background:var(--accent);color:#0a0a0a;border-color:var(--accent);';
      saveBtn.title = 'Confirm rename';
      _setBtnIco(saveBtn, 'check');

      const cancelBtn = document.createElement('button');
      cancelBtn.className = 'session-act-btn';
      cancelBtn.title = 'Cancel';
      _setBtnIco(cancelBtn, 'close');

      nameEl.style.display = 'none';
      loadBtn.style.display = renBtn.style.display = delBtn.style.display = 'none';
      row.querySelector('.session-row-count').style.display = 'none';

      const actionsEl = row.querySelector('.session-row-actions');
      row.insertBefore(input, actionsEl);
      actionsEl.appendChild(saveBtn);
      actionsEl.appendChild(cancelBtn);
      input.focus(); input.select();

      const doRename = async () => {
        const newName = input.value.trim();
        if (!newName || newName === name) { doCancel(); return; }
        // Save new, delete old
        const savedItems = sessions[name];
        await fetch('/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newName, entries: savedItems }) });
        await fetch('/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ delete: name }) });
        delete sessions[name];
        sessions[newName] = savedItems;
        appendLog(`Session renamed: "${name}" → "${newName}"\n`, 'log-success');
        _renderSessionsModalList();
      };

      const doCancel = () => {
        input.remove(); saveBtn.remove(); cancelBtn.remove();
        nameEl.style.display = '';
        loadBtn.style.display = renBtn.style.display = delBtn.style.display = '';
        row.querySelector('.session-row-count').style.display = '';
      };

      saveBtn.addEventListener('click', doRename);
      cancelBtn.addEventListener('click', doCancel);
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter')  doRename();
        if (e.key === 'Escape') doCancel();
      });
    });
    // ──────────────────────────────────────────────────────────────────────
    row.querySelector('.del-btn').addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      if (!btn.dataset.confirming) {
        btn.dataset.confirming = '1';
        btn.textContent = 'Delete?';
        btn.style.background = 'var(--red)'; btn.style.color = '#fff'; btn.style.borderColor = 'var(--red)';
        setTimeout(() => {
          delete btn.dataset.confirming;
          _setBtnIco(btn, 'close');
          btn.style.background = btn.style.color = btn.style.borderColor = '';
        }, 2000);
        return;
      }
      await fetch('/sessions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delete: name }),
      });
      delete sessions[name];
      _renderSessionsModalList();
      appendLog(`Session deleted: "${name}"\n`, 'log-warn');
    });
    inner.appendChild(row);
  });
}

function openSessionsModal() {
  _renderSessionsModalList();
  document.getElementById('sessions-overlay').classList.add('open');
}

function saveSession() {
  if (!entries.length) {
    appendLog('Queue is empty - nothing to save.\n', 'log-warn');
    return;
  }
  const nameEl = document.getElementById('save-session-name');
  nameEl.value = '';
  document.getElementById('save-session-overlay').classList.add('open');
  setTimeout(() => { nameEl.focus(); }, 50);
}

async function _commitSaveSession() {
  const name = document.getElementById('save-session-name').value.trim();
  if (!name) { flash(document.getElementById('save-session-name')); return; }
  document.getElementById('save-session-overlay').classList.remove('open');
  await fetch('/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, entries }),
  });
  sessions[name] = [...entries];
  _renderSessionsModalList();
  appendLog(`Session saved: "${name}"\n`, 'log-success');
}

async function deleteSession() { /* handled in modal now */ }


// ═══════════════════════════════════════
//  HISTORY TAB
// ═══════════════════════════════════════
async function loadHistory() {
  const list = document.getElementById('history-list');
  list.innerHTML = '<div class="search-status">Loading…</div>';
  try {
    const resp = await fetch('/history');
    historyData = await resp.json();
    applyHistoryFilter();
  } catch (_) {
    list.innerHTML = '<div class="search-status">Failed to load history.</div>';
  }
}

function applyHistoryFilter() {
  const filter = document.getElementById('history-filter').value.trim().toLowerCase();
  let visible  = historyData;

  if (filter) {
    visible = visible.filter(d =>
      d.artist.toLowerCase().includes(filter) ||
      (d.albums || []).some(a => a.toLowerCase().includes(filter)));
  }

  if (histTagFilter === 'fav') {
    visible = visible.filter(d => (getEntryMeta(d.url).tags || []).includes('fav'));
  } else if (histTagFilter === 'rev') {
    visible = visible.filter(d => (getEntryMeta(d.url).tags || []).includes('rev'));
  } else if (histTagFilter === 'note') {
    visible = visible.filter(d => !!(getEntryMeta(d.url).note));
  }

  renderHistoryList(visible);
}

function renderHistoryList(data) {
  const list = document.getElementById('history-list');

  if (!data.length) {
    list.innerHTML = `<div class="search-status">${
      historyData.length ? 'No matches.' : 'No download history yet.'
    }</div>`;
    return;
  }
  list.innerHTML = '';

  data.forEach(item => {
    const row = document.createElement('div');
    row.className = 'history-item';
    row.dataset.url = item.url;

    const ts   = item.timestamp ? new Date(item.timestamp) : null;
    const date = ts ? ts.toLocaleDateString('en-GB', { day:'numeric', month:'short', year:'numeric' }) : '-';
    const time = ts ? ts.toLocaleTimeString('en-GB', { hour:'2-digit', minute:'2-digit', hour12: false }) : '';

    const albumNames   = item.albums && item.albums.length ? item.albums : null;
    const albumDisplay = albumNames
      ? (albumNames.length <= 2
          ? albumNames.join(', ')
          : albumNames.slice(0, 2).join(', ') + ` +${albumNames.length - 2} more`)
      : null;

    const meta    = getEntryMeta(item.url);
    const tagHtml = [
      meta.tags.includes('fav') ? '<span class="tag-pill tag-fav">fav</span>' : '',
      meta.tags.includes('rev') ? '<span class="tag-pill tag-rev">later</span>' : '',
    ].filter(Boolean).join('');

    row.innerHTML = `
      <div class="history-info">
        <div class="history-url-field" title="Click to copy URL">${escHtml(item.url.replace('https://', ''))}</div>
        ${albumDisplay ? `<div class="history-album">${escHtml(albumDisplay)}</div>` : ''}
        <div class="history-artist">${escHtml(item.artist)}</div>
        <div class="history-meta">
          <span>${item.files.length} file(s)</span>
          <span class="history-dot">·</span>
          <span>${date}${time ? ' ' + time : ''}</span>
        </div>
        ${tagHtml ? `<div class="history-tags">${tagHtml}</div>` : ''}
        ${meta.note ? `<div class="history-note-pill" title="${escHtml(meta.note)}">${escHtml(meta.note)}</div>` : ''}
      </div>
      <div class="history-actions">
        <button class="btn-icon history-btn" data-act="add" title="Add to queue">${_btnIco('plus', 'Queue')}</button>
        <button class="btn-icon history-btn btn-danger" data-act="redown" title="Remove manifest entry and re-queue">${_btnIco('refresh', 'Re-DL')}</button>
        <button class="history-remove-btn" data-act="remove" title="Remove from history">${_btnIco('close')}</button>
      </div>
    `;
    _applyIcons(row);

    const urlField = row.querySelector('.history-url-field');
    urlField.addEventListener('click', () => {
      navigator.clipboard.writeText(item.url).then(() => {
        urlField.textContent = 'Copied';
        urlField.classList.add('copied');
        setTimeout(() => { urlField.textContent = item.url.replace('https://', ''); urlField.classList.remove('copied'); }, 1200);
      }).catch(() => {});
    });

    row.querySelector('[data-act="add"]').addEventListener('click', () => {
      entries.push({ url: item.url, artist: item.artist,
        albumTitle: albumDisplay || null, coverUrl: null, nbTracks: item.files.length || null,
        isPlaylist: !!item.is_playlist, playlistName: item.playlist || '' });
      renderQueue();
      switchTab('log');
    });

    row.querySelector('[data-act="redown"]').addEventListener('click', async () => {
      if (!await _confirm(`Remove "${item.artist}" from the manifest so it will be re-downloaded, then add to the queue?`, { title: 'Re-download', confirmLabel: 'Re-download' })) return;
      await fetch('/history-remove', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: item.url }),
      });
      entries.unshift({ url: item.url, artist: item.artist, albumTitle: null, coverUrl: null, nbTracks: null,
        isPlaylist: !!item.is_playlist, playlistName: item.playlist || '' });
      manifestUrls.delete(item.url);
      renderQueue();
      switchTab('log');
      historyData = historyData.filter(d => d.url !== item.url);
      applyHistoryFilter();
    });

    // Individual × remove button
    row.querySelector('[data-act="remove"]').addEventListener('click', async () => {
      await _histRemoveSingle(item.url);
    });

    list.appendChild(row);
  });
}

async function _histRemoveSingle(url) {
  await fetch('/history-remove', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  manifestUrls.delete(url);
  historyData = historyData.filter(d => d.url !== url);
  applyHistoryFilter();
  renderQueue();
}




// ═══════════════════════════════════════
//  STATS TAB
// ═══════════════════════════════════════
let _libStatsCache = null;
let _libStatsFetching = false;
let _libStatsForceNext = false;   // one-shot: force a fresh server recompute on next fetch

let _playStatsCache = null;   // /play-stats payload (local listening history)

async function _fetchLibStats(force = false) {
  if (_libStatsFetching) return;
  _libStatsFetching = true;
  if (_libStatsForceNext) { force = true; _libStatsForceNext = false; }
  try {
    // The server caches the (expensive) stats payload and validates it against a
    // cheap library signature, so this is fast unless the library actually
    // changed. `force` bypasses that cache for an explicit user refresh.
    const resp = await fetch('/library-stats' + (force ? '?refresh=1' : ''));
    const data = await resp.json();
    if (!data.error) {
      _libStatsCache = data;
      renderStats(); // re-render with fresh data
    }
  } catch (_) {}
  try {
    // Cheap (local JSONL aggregation), refresh alongside the library stats.
    const ps = await (await fetch('/play-stats')).json();
    if (ps && !ps.error) { _playStatsCache = ps; renderStats(); }
  } catch (_) {}
  _libStatsFetching = false;
}

function _fmtBytes(b) {
  if (b >= 1_073_741_824) return `${(b / 1_073_741_824).toFixed(2)} GB`;
  if (b >= 1_048_576)     return `${(b / 1_048_576).toFixed(0)} MB`;
  return `${Math.round(b / 1024)} KB`;
}

function renderStats() {
  const el = document.getElementById('stats-content');
  if (!el) return;

  let knownTracks = 0, unknownCount = 0;
  entries.forEach(e => e.nbTracks ? (knownTracks += e.nbTracks) : unknownCount++);
  const totalEstimated = knownTracks + unknownCount * 10;
  const estimatedMB    = totalEstimated * 8;

  let etaStr = '-';
  if (avgSpeedMBs && avgSpeedMBs > 0 && estimatedMB > 0) {
    const secs = estimatedMB / avgSpeedMBs;
    etaStr = secs < 60 ? `~${Math.round(secs)}s` : `~${Math.round(secs / 60)} min`;
  }

  const resultList = Object.values(sessionResults);
  const okCount    = resultList.filter(r => r.status === 'ok').length;
  const partCount  = resultList.filter(r => r.status === 'partial').length;
  const errCount   = resultList.filter(r => r.status === 'error').length;
  const skipCount  = resultList.filter(r => r.status === 'skipped').length;
  const totalDl    = resultList.reduce((s, r) => s + (r.downloaded || 0), 0);

  let progCard = '';
  if (currentProgress && (running || resultList.length)) {
    const { filesDone, filesTotal, mbDone, mbTotal } = currentProgress;
    const smoothSpd = _prog.smoothSpeed || currentProgress.speedMBs;
    const pct     = mbTotal > 0 ? Math.round((mbDone / mbTotal) * 100) : 0;
    const elapsed = sessionStartTime ? Math.round((Date.now() - sessionStartTime) / 1000) : 0;
    const elStr   = elapsed < 60 ? `${elapsed}s` : `${Math.round(elapsed/60)}m ${elapsed%60}s`;
    progCard = `
      <div class="stat-card">
        <div class="stat-card-title">Active Download</div>
        <div class="stat-row"><span>Files</span><strong>${filesDone} / ${filesTotal}</strong></div>
        <div class="stat-row"><span>Data</span><strong>${mbDone.toFixed(1)} / ${mbTotal.toFixed(1)} MB</strong></div>
        <div class="stat-row"><span>Progress</span><strong>${pct}%</strong></div>
        <div class="stat-row"><span>Speed (avg)</span><strong>${smoothSpd.toFixed(2)} MB/s</strong></div>
        <div class="stat-row"><span>Elapsed</span><strong>${elStr}</strong></div>
      </div>`;
  }

  let speedCard = '';
  if (speedHistory.length) {
    speedCard = `
      <div class="stat-card">
        <div class="stat-card-title">Speed History (last ${speedHistory.length} session(s) / 20)</div>
        <div class="stat-row"><span>Average</span><strong>${avgSpeedMBs.toFixed(2)} MB/s</strong></div>
        <div class="stat-row"><span>Peak</span><strong>${Math.max(...speedHistory).toFixed(2)} MB/s</strong></div>
        <div class="stat-sparkline">${makeSparkline(speedHistory)}</div>
      </div>`;
  }

  let sessionCard = '';
  if (resultList.length) {
    sessionCard = `
      <div class="stat-card">
        <div class="stat-card-title">This Session</div>
        <div class="stat-row"><span>OK</span><strong style="color:var(--accent)">${okCount}</strong></div>
        ${partCount ? `<div class="stat-row"><span>Partial</span><strong style="color:var(--yellow)">${partCount}</strong></div>` : ''}
        ${errCount  ? `<div class="stat-row"><span>Errors</span><strong style="color:var(--red)">${errCount}</strong></div>` : ''}
        ${skipCount ? `<div class="stat-row"><span>Skipped</span><strong style="color:var(--fg3)">${skipCount}</strong></div>` : ''}
        <div class="stat-row"><span>Tracks Downloaded</span><strong>${totalDl}</strong></div>
      </div>`;
  }

  // ── Library stats card ──────────────────────────────────────────────────
  let libCard = '';
  const ls = _libStatsCache;
  if (ls && !ls.error) {
    const topArtist = ls.artists_by_tracks?.[0];
    const favArtistHtml = topArtist
      ? `<div class="stat-row"><span>Top artist</span><strong style="color:var(--accent)">${escHtml(topArtist.name)}</strong></div>
         <div class="stat-row"><span>↳ tracks</span><strong>${topArtist.tracks} · ${_fmtBytes(topArtist.bytes)}</strong></div>`
      : '';

    const histSum = ls.download_history_summary || {};
    const histHtml = histSum.total_entries
      ? `<div class="stat-row"><span>Downloads logged</span><strong>${histSum.total_entries}</strong></div>
         <div class="stat-row"><span>Unique artists</span><strong>${histSum.unique_artists}</strong></div>
         ${histSum.earliest ? `<div class="stat-row"><span>First download</span><strong>${histSum.earliest}</strong></div>` : ''}
         ${histSum.latest   ? `<div class="stat-row"><span>Latest download</span><strong>${histSum.latest}</strong></div>` : ''}`
      : '';

    const avgTracksStr = ls.avg_tracks_per_album != null
      ? ls.avg_tracks_per_album.toFixed(1)
      : '-';

    libCard = `
      <div class="stat-card">
        <div class="stat-card-title">Library Overview</div>
        <div class="stat-row"><span>Total tracks</span><strong style="color:var(--accent)">${ls.total_files.toLocaleString()}</strong></div>
        <div class="stat-row"><span>Total size</span><strong>${_fmtBytes(ls.total_bytes)}</strong></div>
        <div class="stat-row"><span>Artists</span><strong>${ls.artist_count}</strong></div>
        <div class="stat-row"><span>Albums</span><strong>${ls.album_count}</strong></div>
        <div class="stat-row"><span>Avg tracks / album</span><strong>${avgTracksStr}</strong></div>
        ${favArtistHtml}
      </div>
      ${histHtml ? `<div class="stat-card"><div class="stat-card-title">Download History</div>${histHtml}</div>` : ''}
      ${ls.artists_by_tracks?.length > 1 ? `
      <div class="stat-card">
        <div class="stat-card-title">Top Artists by Track Count</div>
        ${ls.artists_by_tracks.slice(0, 8).map((a, i) => {
          const maxT = ls.artists_by_tracks[0].tracks;
          const barW = Math.round((a.tracks / maxT) * 100);
          return `<div class="stat-row" style="gap:8px;">
            <span style="min-width:16px;text-align:right;color:var(--fg3);font-size:10px">${i+1}</span>
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(a.name)}</span>
            <strong style="font-size:10px;min-width:28px;text-align:right">${a.tracks}</strong>
            <div style="width:60px;height:3px;background:var(--bg4);border-radius:2px;overflow:hidden;flex-shrink:0">
              <div style="width:${barW}%;height:100%;background:var(--accent);border-radius:2px"></div>
            </div>
          </div>`;
        }).join('')}
      </div>` : ''}
      ${ls.growth?.length > 1 ? `
      <div class="stat-card">
        <div class="stat-card-title">Library Growth</div>
        ${makeGrowthChart(ls.growth)}
        <div class="stat-row" style="margin-top:6px"><span>Tracks added (90d)</span><strong style="color:var(--accent)">${_growthRecent(ls.growth)}</strong></div>
      </div>` : ''}
      ${ls.top_genres?.length ? `
      <div class="stat-card">
        <div class="stat-card-title">Top Genres</div>
        ${makeBarChart(ls.top_genres.map(g => ({ label: g.name, value: g.tracks })))}
      </div>` : ''}
      ${ls.formats?.length ? `
      <div class="stat-card">
        <div class="stat-card-title">Format Breakdown</div>
        ${makeBarChart(ls.formats.map(f => ({ label: f.ext, value: f.count })))}
      </div>` : ''}
    `;
  } else if (!ls) {
    // Trigger fetch in background
    _fetchLibStats();
    libCard = `<div class="stat-card"><div class="stat-card-title">Library Overview</div><div style="font-family:var(--mono);font-size:10px;color:var(--fg3);padding:8px 0">Loading library stats…</div></div>`;
  } else {
    libCard = `<div class="stat-card"><div class="stat-card-title">Library Overview</div><div style="font-family:var(--mono);font-size:10px;color:var(--red);padding:8px 0">${escHtml(ls.error)}</div></div>`;
  }

  if (!entries.length && !resultList.length && !currentProgress && !ls) {
    el.innerHTML = '<div class="stat-empty">Loading…</div>';
    _fetchLibStats();
    return;
  }

  // ── Listening card (local play history, see /play-stats) ────────────────
  let listenCard = '';
  const ps = _playStatsCache;
  if (ps && ps.total > 0) {
    listenCard = `
      <div class="stat-card">
        <div class="stat-card-title">Listening</div>
        <div class="stat-row"><span>Plays (all time)</span><strong style="color:var(--accent)">${ps.total.toLocaleString()}</strong></div>
        <div class="stat-row"><span>Last 7 days</span><strong>${ps.last7}</strong></div>
        <div class="stat-row"><span>Last 30 days</span><strong>${ps.last30}</strong></div>
        ${ps.top_artists?.length ? `
        <div class="stat-card-title" style="margin-top:10px;font-size:10px">Top artists</div>
        ${makeBarChart(ps.top_artists.slice(0, 5).map(a => ({ label: a.artist, value: a.plays })))}` : ''}
        ${ps.top_tracks?.length ? `
        <div class="stat-card-title" style="margin-top:10px;font-size:10px">Top tracks</div>
        ${ps.top_tracks.slice(0, 5).map(t => `
          <div class="stat-row" style="gap:8px">
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(t.title)} <span style="color:var(--fg3)">· ${escHtml(t.artist)}</span></span>
            <strong style="font-size:10px">${t.plays}×</strong>
          </div>`).join('')}` : ''}
        <div style="display:flex;gap:6px;margin-top:12px">
          <button class="btn-secondary" id="btn-open-wrapped" style="width:auto;font-size:11px;padding:4px 10px">Your year in music</button>
          <button class="btn-secondary" id="btn-open-phistory" style="width:auto;font-size:11px;padding:4px 10px">Full history</button>
        </div>
      </div>`;
  }

  el.innerHTML = `
    ${progCard}
    ${entries.length ? `
    <div class="stat-card">
      <div class="stat-card-title">Queue Estimate</div>
      <div class="stat-row"><span>URLs</span><strong>${entries.length}</strong></div>
      <div class="stat-row">
        <span>Tracks</span>
        <strong>${knownTracks > 0 ? knownTracks : ''}${unknownCount > 0 ? ` + ~${unknownCount * 10} est.` : ''}</strong>
      </div>
      <div class="stat-row"><span>Est. size</span><strong>~${estimatedMB} MB</strong></div>
      <div class="stat-row"><span>Est. time</span><strong style="color:${etaStr !== '-' ? 'var(--accent)' : 'var(--fg3)'}">${etaStr}</strong></div>
      ${avgSpeedMBs ? '' : '<div class="stat-row"><span style="font-size:10px;color:var(--fg3)">Run a session to calibrate ETA</span></div>'}
    </div>` : ''}
    ${sessionCard}
    ${listenCard}
    ${libCard}
    ${speedCard}
  `;
}

// ═══════════════════════════════════════
//  WRAPPED + LISTENING HISTORY
// ═══════════════════════════════════════
async function openWrapped(year) {
  document.getElementById('wrapped-overlay')?.classList.add('open');
  const content = document.getElementById('wrapped-content');
  if (content) content.innerHTML = '<div class="stat-empty">Crunching your year…</div>';
  try {
    const d = await (await fetch('/wrapped' + (year ? `?year=${year}` : ''))).json();
    _renderWrapped(d);
  } catch (e) {
    if (content) content.innerHTML = `<div class="stat-empty" style="color:var(--red)">Could not load your year: ${escHtml(String(e))}</div>`;
  }
}

function _renderWrapped(d) {
  const content = document.getElementById('wrapped-content');
  const yearSel = document.getElementById('wrapped-year');
  if (!content) return;
  if (yearSel) {
    const years = d.years?.length ? d.years : [d.year];
    yearSel.innerHTML = years.map(y =>
      `<option value="${y}"${y === d.year ? ' selected' : ''}>${y}</option>`).join('');
  }
  if (!d.total_plays) {
    content.innerHTML = `<div class="stat-empty">No plays recorded in ${d.year} - play some music and come back.</div>`;
    return;
  }
  const monthMax  = Math.max(...d.by_month, 1);
  const monthLbls = ['J','F','M','A','M','J','J','A','S','O','N','D'];
  const rows = (list, fmt) => list.map((x, i) => `
    <div class="wrapped-row"><span class="rank">${i + 1}</span>${fmt(x)}
      <span class="plays">${x.plays}×</span></div>`).join('');
  content.innerHTML = `
    <div class="wrapped-hero">
      <div class="wrapped-hero-cell"><div class="wrapped-hero-num">${d.total_plays.toLocaleString()}</div><div class="wrapped-hero-lbl">plays</div></div>
      <div class="wrapped-hero-cell"><div class="wrapped-hero-num">${d.unique_tracks.toLocaleString()}</div><div class="wrapped-hero-lbl">unique tracks</div></div>
      <div class="wrapped-hero-cell"><div class="wrapped-hero-num">${d.unique_artists.toLocaleString()}</div><div class="wrapped-hero-lbl">artists</div></div>
      <div class="wrapped-hero-cell"><div class="wrapped-hero-num">${d.listening_days}</div><div class="wrapped-hero-lbl">listening days</div></div>
      <div class="wrapped-hero-cell"><div class="wrapped-hero-num">${d.longest_streak_days}</div><div class="wrapped-hero-lbl">longest day streak</div></div>
      <div class="wrapped-hero-cell"><div class="wrapped-hero-num">${d.busiest_day ? d.busiest_day.plays : '-'}</div><div class="wrapped-hero-lbl">${d.busiest_day ? 'plays on ' + escHtml(d.busiest_day.date) : 'busiest day'}</div></div>
    </div>
    <div class="wrapped-sec-label">Plays by month</div>
    <div class="wrapped-months">${d.by_month.map(v =>
      `<div class="m" style="height:${Math.max(4, Math.round((v / monthMax) * 100))}%" title="${v} plays"></div>`).join('')}</div>
    <div class="wrapped-months-lbls">${monthLbls.map(l => `<span>${l}</span>`).join('')}</div>
    ${d.top_artists?.length ? `<div class="wrapped-sec-label">Top artists</div>` +
      rows(d.top_artists.slice(0, 5), a => `<span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(a.artist)}</span>`) : ''}
    ${d.top_tracks?.length ? `<div class="wrapped-sec-label">Top tracks</div>` +
      rows(d.top_tracks.slice(0, 5), t => `<span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(t.title)} <span style="color:var(--fg3)">· ${escHtml(t.artist)}</span></span>`) : ''}
    ${d.top_albums?.length ? `<div class="wrapped-sec-label">Top albums</div>` +
      rows(d.top_albums, al => `<span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(al.album)} <span style="color:var(--fg3)">· ${escHtml(al.artist)}</span></span>`) : ''}
  `;
}

let _phOffset = 0, _phQuery = '', _phTimer = null;
const _PH_PAGE = 200;

async function openPlayHistory() {
  _phOffset = 0; _phQuery = '';
  const q = document.getElementById('phistory-q');
  if (q) q.value = '';
  document.getElementById('phistory-overlay')?.classList.add('open');
  _phLoad(false);
  setTimeout(() => q?.focus(), 20);
}

async function _phLoad(append) {
  const list = document.getElementById('phistory-list');
  if (!list) return;
  if (!append) list.innerHTML = '<div class="stat-empty">Loading…</div>';
  try {
    const d = await (await fetch(`/play-history?q=${encodeURIComponent(_phQuery)}&offset=${_phOffset}&limit=${_PH_PAGE}`)).json();
    const rows = (d.events || []).map(e => `
      <div class="phistory-row">
        <span class="t">${escHtml(e.title)}</span>
        <span class="a">${escHtml(e.artist)}${e.album ? ' · ' + escHtml(e.album) : ''}</span>
        <span class="ts">${escHtml((e.ts || '').replace('T', ' '))}</span>
      </div>`).join('');
    if (append) list.insertAdjacentHTML('beforeend', rows);
    else list.innerHTML = rows || '<div class="stat-empty">No plays match.</div>';
    const shown = _phOffset + (d.events || []).length;
    const count = document.getElementById('phistory-count');
    if (count) count.textContent = `${shown.toLocaleString()} of ${(d.total || 0).toLocaleString()} plays`;
    const more = document.getElementById('btn-phistory-more');
    if (more) more.style.display = shown < (d.total || 0) ? '' : 'none';
  } catch (e) {
    if (!append) list.innerHTML = `<div class="stat-empty" style="color:var(--red)">Could not load history: ${escHtml(String(e))}</div>`;
  }
}

// Wire the (persistent) modal chrome + delegate the stats-card buttons
document.getElementById('stats-content')?.addEventListener('click', (e) => {
  if (e.target.closest('#btn-open-wrapped'))  openWrapped();
  if (e.target.closest('#btn-open-phistory')) openPlayHistory();
});
document.getElementById('btn-wrapped-close')?.addEventListener('click', () =>
  document.getElementById('wrapped-overlay')?.classList.remove('open'));
document.getElementById('wrapped-overlay')?.addEventListener('click', (e) => {
  if (e.target === document.getElementById('wrapped-overlay'))
    document.getElementById('wrapped-overlay').classList.remove('open');
});
document.getElementById('wrapped-year')?.addEventListener('change', (e) =>
  openWrapped(parseInt(e.target.value, 10)));
document.getElementById('btn-phistory-close')?.addEventListener('click', () =>
  document.getElementById('phistory-overlay')?.classList.remove('open'));
document.getElementById('phistory-overlay')?.addEventListener('click', (e) => {
  if (e.target === document.getElementById('phistory-overlay'))
    document.getElementById('phistory-overlay').classList.remove('open');
});
document.getElementById('phistory-q')?.addEventListener('input', (e) => {
  clearTimeout(_phTimer);
  _phTimer = setTimeout(() => {
    _phQuery = e.target.value.trim();
    _phOffset = 0;
    _phLoad(false);
  }, 250);
});
document.getElementById('btn-phistory-more')?.addEventListener('click', () => {
  _phOffset += _PH_PAGE;
  _phLoad(true);
});

function makeSparkline(values) {
  if (!values.length) return '';
  const w = 160, h = 32;
  const max = Math.max(...values);
  const pts = values.map((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * w;
    const y = h - (v / max) * (h - 6) - 3;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="stat-spark-svg">
    <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round" opacity="0.85" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

// Horizontal labelled bar chart for {label, value} rows (genres, formats, …)
function makeBarChart(rows) {
  if (!rows || !rows.length) return '';
  const max = Math.max(...rows.map(r => r.value), 1);
  return rows.map(r => {
    const barW = Math.round((r.value / max) * 100);
    return `<div class="stat-row" style="gap:8px;">
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(String(r.label))}</span>
      <strong style="font-size:10px;min-width:36px;text-align:right">${r.value.toLocaleString()}</strong>
      <div style="width:70px;height:3px;background:var(--bg4);border-radius:2px;overflow:hidden;flex-shrink:0">
        <div style="width:${barW}%;height:100%;background:var(--accent);border-radius:2px"></div>
      </div>
    </div>`;
  }).join('');
}

// "YYYY-MM" -> "Mon ’YY" for compact axis labels
function _fmtMonthLabel(m) {
  const parts = String(m).split('-');
  const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const idx = Math.max(0, Math.min(11, (parseInt(parts[1], 10) || 1) - 1));
  return `${names[idx]} ’${String(parts[0]).slice(2)}`;
}

// Cumulative library-growth line chart from [{month, added, cumulative}].
// Fills the (full-width) card at a fixed height: the area/line live in an SVG
// stretched edge-to-edge via preserveAspectRatio="none" (with non-scaling
// strokes so the line stays crisp), while all text + the endpoint dot are HTML
// overlays so they never distort regardless of how wide the card gets.
function makeGrowthChart(growth) {
  const vals = growth.map(g => g.cumulative);
  if (vals.length < 2) return '';
  const H = 160;                  // fixed container height (px)
  const padT = 14, padB = 26;     // top breathing room / bottom x-label room
  const axisL = 38, axisR = 12;   // gutters: y-labels (left), endpoint (right)
  const plotH = H - padT - padB;
  const max = Math.max(...vals), min = 0;          // anchor to 0 so growth reads honestly
  const span = Math.max(max - min, 1);
  const VBW = 1000;               // viewBox width, stretched to fill the plot area
  const xAt = i => (vals.length === 1 ? 0 : (i / (vals.length - 1)) * VBW);
  const yAt = v => padT + plotH - ((v - min) / span) * plotH;
  const baseY = padT + plotH;
  const pts  = vals.map((v, i) => `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`);
  const area = `0,${baseY.toFixed(1)} ${pts.join(' ')} ${VBW},${baseY.toFixed(1)}`;
  const uid  = 'gg' + Math.random().toString(36).slice(2, 8);

  const ticks = [0, 0.5, 1];
  // Gridlines live in the stretched SVG (horizontal lines tolerate stretching).
  const gridLines = ticks.map(f => {
    const y = yAt(min + f * span);
    return `<line x1="0" y1="${y.toFixed(1)}" x2="${VBW}" y2="${y.toFixed(1)}" stroke="var(--bg4)" stroke-width="1" vector-effect="non-scaling-stroke" opacity="0.6"/>`;
  }).join('');
  // Y-axis value labels (HTML, in the left gutter) aligned to the gridlines.
  const yLabels = ticks.map(f => {
    const v = Math.round(min + f * span), y = yAt(min + f * span);
    return `<div style="position:absolute;left:0;top:${(y - 7).toFixed(1)}px;width:${axisL - 6}px;text-align:right;font-size:9px;color:var(--fg3)">${v.toLocaleString()}</div>`;
  }).join('');

  // X-axis labels: first / middle / last month (HTML, along the bottom).
  const labelIdx = vals.length <= 3 ? vals.map((_, i) => i)
    : [0, Math.floor((vals.length - 1) / 2), vals.length - 1];
  const xLabels = labelIdx.map(i => {
    const isFirst = i === 0, isLast = i === vals.length - 1;
    const pct = vals.length === 1 ? 0 : i / (vals.length - 1);
    const pos = isFirst ? `left:${axisL}px`
      : isLast ? `right:${axisR}px`
      : `left:calc(${axisL}px + (100% - ${axisL + axisR}px) * ${pct});transform:translateX(-50%)`;
    return `<div style="position:absolute;bottom:5px;${pos};font-size:9px;color:var(--fg3);white-space:nowrap">${escHtml(_fmtMonthLabel(growth[i].month))}</div>`;
  }).join('');

  // Endpoint dot (HTML) at the last point: right edge of the plot area.
  const lastY = yAt(vals[vals.length - 1]);
  const dot = `<div style="position:absolute;right:${axisR - 3}px;top:${(lastY - 3).toFixed(1)}px;width:6px;height:6px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 2px var(--bg3)"></div>`;

  return `<div style="position:relative;width:100%;height:${H}px;margin-top:4px">
    ${yLabels}
    <svg viewBox="0 0 ${VBW} ${H}" preserveAspectRatio="none"
         style="position:absolute;left:${axisL}px;top:0;height:${H}px;width:calc(100% - ${axisL + axisR}px)">
      <defs><linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.28"/>
        <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
      </linearGradient></defs>
      ${gridLines}
      <polygon points="${area}" fill="url(#${uid})"/>
      <polyline points="${pts.join(' ')}" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>
    ${dot}
    ${xLabels}
  </div>`;
}

// Sum of tracks added in the trailing ~90 days (last 3 month buckets)
function _growthRecent(growth) {
  return growth.slice(-3).reduce((s, g) => s + (g.added || 0), 0).toLocaleString();
}


// ═══════════════════════════════════════
//  RETRY FAILED URL
// ═══════════════════════════════════════
function retryEntry(idx) {
  if (running) { appendLog('Cannot retry while a session is running.\n', 'log-warn'); return; }
  const e = entries[idx];
  if (!e) return;

  delete sessionResults[e.url];
  renderQueue();

  const home = document.getElementById('home-input').value.trim();
  if (home) {
    fetch('/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ home_music_folder: home }),
    });
  }

  currentProgress = null;
  appendLog('\n' + '─'.repeat(52) + '\n', 'log-dim');
  appendLog(`Retrying [${idx + 1}]: ${e.url}\n`);
  switchTab('log');
  wsSend({ action: 'start', entries: [e] });
}

// Entries whose last session result was a failure (error or partial).
function _failedEntries() {
  return entries.filter(e => {
    const r = sessionResults[e.url];
    return r && (r.status === 'error' || r.status === 'partial');
  });
}

// Show/enable the "Retry failed" button only when there are failures to retry.
function _updateRetryFailedBtn() {
  const btn = document.getElementById('btn-retry-failed');
  if (!btn) return;
  const n = _failedEntries().length;
  btn.style.display  = n > 0 ? '' : 'none';
  btn.disabled       = running;
  btn.innerHTML      = `<span class="bi" data-icon="refresh"></span>Retry failed (${n})`;
  _applyIcons(btn);
}

// Re-run every failed/partial entry from the last session in one click.
function retryAllFailed() {
  if (running) { appendLog('Cannot retry while a session is running.\n', 'log-warn'); return; }
  const failed = _failedEntries();
  if (!failed.length) return;

  failed.forEach(e => delete sessionResults[e.url]);
  renderQueue();

  const home = document.getElementById('home-input').value.trim();
  if (home) {
    fetch('/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ home_music_folder: home }),
    });
  }

  currentProgress = null;
  appendLog('\n' + '─'.repeat(52) + '\n', 'log-dim');
  appendLog(`Retrying ${failed.length} failed item${failed.length === 1 ? '' : 's'}…\n`);
  switchTab('log');
  wsSend({ action: 'start', entries: failed });
}


// ═══════════════════════════════════════
//  SCHEDULED QUEUE RUN
// ═══════════════════════════════════════
async function _refreshScheduleBanner() {
  const el = document.getElementById('schedule-banner');
  if (!el) return;
  try {
    const d = await (await fetch('/schedule-queue')).json();
    if (d.scheduled) {
      el.style.display = '';
      el.innerHTML = `Scheduled: ${escHtml(d.at_str)} · ${d.entries} URL(s) -
        <a href="#" id="schedule-cancel-link" style="color:var(--red)">cancel</a>`;
      document.getElementById('schedule-cancel-link')?.addEventListener('click', async (ev) => {
        ev.preventDefault();
        await fetch('/schedule-cancel', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: '{}' });
        _toast('Scheduled run cancelled.', 'info');
        _refreshScheduleBanner();
      });
    } else {
      el.style.display = 'none';
      el.innerHTML = '';
    }
  } catch (_) {}
}

async function scheduleQueueRun() {
  const home = document.getElementById('home-input').value.trim();
  if (!home) {
    flash(document.getElementById('home-input'));
    appendLog('ERROR: Set a home music folder first.\n', 'log-error');
    return;
  }
  if (!entries.length) { appendLog('ERROR: Queue is empty.\n', 'log-error'); return; }
  const when = await _prompt('Start time (HH:MM, 24-hour - next occurrence, so past times mean tomorrow)', {
    title: 'Schedule queue run', placeholder: 'e.g. 03:30', confirmLabel: 'Schedule',
  });
  if (!when) return;
  const m = when.trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!m || +m[1] > 23 || +m[2] > 59) { _toast('Use HH:MM (24-hour).', 'error'); return; }
  const at = new Date();
  at.setHours(+m[1], +m[2], 0, 0);
  if (at.getTime() <= Date.now()) at.setDate(at.getDate() + 1);
  // Persist the home folder now, exactly like an immediate run does.
  await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ home_music_folder: home }) });
  try {
    const d = await (await fetch('/schedule-queue', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ at: Math.floor(at.getTime() / 1000), entries, home }),
    })).json();
    if (d.error) { _toast(d.error, 'error'); return; }
    _toast(`Queue scheduled for ${d.at_str}.`, 'success');
    appendLog(`Queue scheduled for ${d.at_str} - ${d.entries} URL(s). Keep the app running.\n`, 'log-success');
  } catch (e) {
    _toast('Could not schedule: ' + e, 'error');
  }
  _refreshScheduleBanner();
}

document.getElementById('btn-schedule')?.addEventListener('click', scheduleQueueRun);
_refreshScheduleBanner();
setInterval(_refreshScheduleBanner, 60_000);   // keep the countdown banner honest

// ═══════════════════════════════════════
//  COPY ALL URLS
// ═══════════════════════════════════════
function copyAllUrls() {
  if (!entries.length) { appendLog('Queue is empty.\n', 'log-warn'); return; }
  const text = entries.map(e => e.url).join('\n');
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('btn-copy-all');
    const orig = btn.innerHTML;
    btn.textContent = 'Copied';
    setTimeout(() => { btn.innerHTML = orig; }, 1500);
  }).catch(() => _toast('Could not copy to clipboard.', 'error'));
}


// ═══════════════════════════════════════
//  EXPORT M3U
// ═══════════════════════════════════════
async function exportM3U() {
  try {
    const cfgResp = await fetch('/config');
    const cfg     = await cfgResp.json();
    const home    = cfg.home_music_folder;
    if (!home) { _toast('Set a home music folder first.', 'error'); return; }

    const histResp = await fetch('/history');
    const hist     = await histResp.json();
    if (!hist.length) { _toast('No download history found.', 'error'); return; }

    const lines = ['#EXTM3U', ''];
    const sep   = home.includes('\\') ? '\\' : '/';

    hist.forEach(item => {
      const artist = sanitizePath(item.artist);
      if (item.albums && item.albums.length) {
        item.albums.forEach(album => {
          const albumSan = sanitizePath(album);
          item.files.forEach(file => {
            lines.push(`#EXTINF:-1,${item.artist} - ${album}`);
            lines.push([home, artist, albumSan, file].join(sep));
          });
        });
      } else {
        item.files.forEach(file => {
          lines.push(`#EXTINF:-1,${item.artist}`);
          lines.push([home, artist, file].join(sep));
        });
      }
    });

    const blob = new Blob([lines.join('\n')], { type: 'audio/x-mpegurl' });
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = 'tgdownloader.m3u';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    _toast('M3U export failed: ' + err, 'error');
  }
}

function sanitizePath(name) {
  return String(name)
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/^[\.\s]+|[\.\s]+$/g, '')
    .trim() || '_unnamed';
}


let _searchFocusIdx = -1;

function _searchResultsNav(dir) {
  const cards = [...document.querySelectorAll('#search-results .album-card')];
  if (!cards.length) return;
  cards.forEach(c => c.classList.remove('kb-focus'));
  _searchFocusIdx = Math.max(0, Math.min(cards.length - 1, _searchFocusIdx + dir));
  cards[_searchFocusIdx].classList.add('kb-focus');
  cards[_searchFocusIdx].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function _searchResultsActivate() {
  const cards = [...document.querySelectorAll('#search-results .album-card')];
  if (_searchFocusIdx >= 0 && cards[_searchFocusIdx]) {
    cards[_searchFocusIdx].click();
    _searchFocusIdx = -1;
  }
}

// Reset focus index when search results change
function _resetSearchFocus() { _searchFocusIdx = -1; }


// ═══════════════════════════════════════
//  DEEZER SEARCH + HISTORY
// ═══════════════════════════════════════
function openSearch() {
  _resetSearchFocus();
  document.getElementById('search-overlay').classList.add('open');
  document.getElementById('search-input').focus();
  renderSearchHistory();
}

function closeSearch() {
  document.getElementById('search-overlay').classList.remove('open');
}

function addToSearchHistory(q) {
  // Only record searches that look intentional: at least 3 chars, not just a single word fragment
  if (!q || q.trim().length < 3) return;
  searchHistory = [q, ...searchHistory.filter(x => x !== q)].slice(0, 10);
  localStorage.setItem('tgd_search_history', JSON.stringify(searchHistory));
}

function removeSearchHistoryItem(q) {
  searchHistory = searchHistory.filter(x => x !== q);
  localStorage.setItem('tgd_search_history', JSON.stringify(searchHistory));
  renderSearchHistory();
}

function clearSearchHistory() {
  searchHistory = [];
  localStorage.removeItem('tgd_search_history');
  renderSearchHistory();
}

function renderSearchHistory() {
  const wrap    = document.getElementById('search-history');
  const section = document.getElementById('search-history-wrap');
  wrap.innerHTML = '';

  if (!searchHistory.length) { section.style.display = 'none'; return; }
  section.style.display = 'block';

  searchHistory.forEach(q => {
    const chipWrap = document.createElement('div');
    chipWrap.className = 'sh-chip-wrap';

    const chip = document.createElement('button');
    chip.className   = 'sh-chip';
    chip.textContent = q;
    chip.addEventListener('click', () => {
      document.getElementById('search-input').value = q;
      doSearch();
    });

    const del = document.createElement('button');
    del.className   = 'sh-chip-del';
    del.innerHTML   = ICON.close;
    del.title       = 'Remove';
    del.addEventListener('click', (ev) => { ev.stopPropagation(); removeSearchHistoryItem(q); });

    chipWrap.appendChild(chip);
    chipWrap.appendChild(del);
    wrap.appendChild(chipWrap);
  });
}

async function doSearch(fromExplicit) {
  const q = document.getElementById('search-input').value.trim();
  if (!q) return;

  // Only record history when user explicitly submitted (not from debounced typing)
  if (fromExplicit) {
    addToSearchHistory(q);
    renderSearchHistory();
  }

  _resetSearchFocus();
  const results = document.getElementById('search-results');
  results.innerHTML = '<div class="search-status">Searching…</div>';
  const provider = document.getElementById('search-provider')?.value || 'deezer';

  try {
    const resp = await fetch(`/search?q=${encodeURIComponent(q)}&provider=${encodeURIComponent(provider)}`);
    const data = await resp.json();

    if (data.error) { results.innerHTML = `<div class="search-status">Error: ${escHtml(data.error)}</div>`; return; }
    if (!data.data || !data.data.length) { results.innerHTML = '<div class="search-status">No results found.</div>'; return; }

    // Spotify search fell back to Deezer (these results add Deezer links). Tell
    // the user rather than silently swapping providers, and if it's just that
    // the API keys aren't set up yet, point them to Settings.
    if (provider === 'spotify' && data.fellback_to_deezer) {
      _toast(data.spotify_unconfigured
        ? 'Add a Spotify Client ID + Secret in Settings → Connection to search Spotify. Showing Deezer results.'
        : `Spotify error: ${data.spotify_error || 'unavailable'} - showing Deezer results.`, 'error');
    }

    const grid = document.createElement('div');
    grid.className = 'album-grid';

    data.data.forEach(album => {
      const card  = document.createElement('div');
      card.className = 'album-card';
      const cover  = album.cover_small || '';
      const tracks = album.nb_tracks != null ? `${album.nb_tracks} track(s)` : '';
      card.innerHTML = `
        ${cover ? `<img class="album-cover" src="${escHtml(cover)}" alt="" loading="lazy">` : `<div class="album-cover-ph">${ICON.note}</div>`}
        <div class="album-info">
          <div class="album-title">${escHtml(album.title)}</div>
          <div class="album-artist">${escHtml(album.artist.name)}</div>
          ${tracks ? `<div class="album-tracks">${tracks}</div>` : ''}
        </div>
      `;
      card.addEventListener('click', async () => {
        // Deezer results have an id; Spotify results carry their own album link
        // (the bot downloads Spotify URLs directly).
        let url = album.id ? `https://www.deezer.com/album/${album.id}` : (album.link || '');
        // Any result still lacking a usable link → resolve it to a Deezer album.
        if (!url && album.artist?.name && album.title) {
          card.style.opacity = '0.5';
          try {
            const rr = await fetch(`/resolve-deezer?artist=${encodeURIComponent(album.artist.name)}&album=${encodeURIComponent(album.title)}`);
            if (rr.ok) { const rd = await rr.json(); url = rd.url || ''; }
          } catch (_) {}
          card.style.opacity = '';
          if (!url) { _toast(`Couldn't find "${album.title}" on Deezer to download.`, 'error'); return; }
        }
        if (!url) return;
        entries.push({
          url,
          artist:     album.artist.name,
          albumTitle: album.title,
          coverUrl:   album.cover_medium || album.cover_small || null,
          nbTracks:   album.nb_tracks || null,
        });
        lastArtist = album.artist.name;
        renderQueue();
        closeSearch();
      });
      grid.appendChild(card);
    });

    results.innerHTML = '';
    results.appendChild(grid);
  } catch (_) {
    results.innerHTML = '<div class="search-status">Network error - is the server running?</div>';
  }
}


// ═══════════════════════════════════════
//  RUN / STOP
// ═══════════════════════════════════════
function runOrStop() {
  if (running) { wsSend({ action: 'stop' }); return; }

  const home = document.getElementById('home-input').value.trim();
  if (!home) {
    flash(document.getElementById('home-input'));
    appendLog('ERROR: Set a home music folder first.\n', 'log-error');
    return;
  }
  if (!entries.length) {
    appendLog('ERROR: Queue is empty.\n', 'log-error');
    return;
  }

  fetch('/config', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ home_music_folder: home }),
  });

  sessionResults   = {};
  currentProgress  = null;
  sessionStartTime = Date.now();

  // Reset smooth progress state
  _stopProgressRaf();
  _prog.raw = null; _prog.smoothSpeed = 0; _prog.displayPct = 0;
  document.getElementById('progress-bar-wrap').classList.remove('visible');
  document.getElementById('progress-fill').style.width = '0%';

  switchTab('log');
  clearLog();
  appendLog('─'.repeat(52) + '\n', 'log-dim');
  appendLog(`Session started - ${entries.length} URL(s)\n`);
  appendLog('─'.repeat(52) + '\n', 'log-dim');
  wsSend({ action: 'start', entries, home });
}


// ═══════════════════════════════════════
