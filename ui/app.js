(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const api = () => window.pywebview.api;
  const call = (name, ...args) => { try { return api()[name](...args); } catch (e) { return Promise.resolve(); } };
  const svg = (inner, vb = '0 0 24 24') => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', vb); s.innerHTML = inner; return s; };
  const ICON_NOTE = '<path d="M9 17.5a2.5 2.5 0 11-2-2.45V5.5l11-2v11a2.5 2.5 0 11-2-2.45V7.1l-7 1.27z"/>';
  const ICON_X = '<path d="M6 6l12 12M18 6L6 18"/>';
  const ICON_GRIP = '<circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/>';

  const S = { settings: {}, models: [], running: false, started: 0, results: { items: [], selected: 0 },
              player: { ready: false, playing: false, pos: 0, length: 0, solo: [], mute: [] }, playerAt: 0,
              progress: 0, shown: 0, seeking: false, yt: { busy: false } };

  /* ---------- small widgets ---------- */
  function select(root, onPick) {
    const btn = root.querySelector('.select-btn'), menu = root.querySelector('.menu');
    btn.addEventListener('click', (e) => { e.stopPropagation(); if (!btn.disabled) { closeMenus(root); root.classList.toggle('open'); } });
    return {
      set(items, current) {
        menu.replaceChildren(...items.map((it, i) => {
          const b = el('button', it.value === current ? 'on' : '', it.label);
          b.addEventListener('click', (e) => { e.stopPropagation(); root.classList.remove('open'); onPick(it.value, i); });
          return b;
        }));
        const cur = items.find((it) => it.value === current);
        btn.querySelector('span').textContent = cur ? cur.label : '';
      },
    };
  }
  function closeMenus(except) { document.querySelectorAll('.select.open').forEach((s) => s !== except && s.classList.remove('open')); }
  document.addEventListener('click', () => closeMenus());
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenus(); });

  function seg(root, values, onPick) {
    const thumb = el('span', 'thumb');
    const buttons = values.map((v) => { const b = el('button', '', v); b.addEventListener('click', () => onPick(v)); return b; });
    root.replaceChildren(thumb, ...buttons);
    return {
      set(value) {
        const i = Math.max(0, values.indexOf(value));
        buttons.forEach((b, j) => b.classList.toggle('on', j === i));
        const b = buttons[i];
        thumb.style.width = b.offsetWidth + 'px';
        thumb.style.transform = `translateX(${b.offsetLeft - 4}px)`;
      },
    };
  }

  /* ---------- settings ---------- */
  let modelSel, qualitySeg, formatSeg;
  function renderSettings() {
    const st = S.settings;
    if (!qualitySeg || !st.model) return;
    modelSel.set(S.models.map((m) => ({ value: m.name, label: m.name })), st.model);
    qualitySeg.set(st.quality);
    formatSeg.set(st.format);
    const drums = (S.models.find((m) => m.name === st.model) || {}).drums;
    const sw = $('nodrums');
    sw.disabled = !drums;
    sw.classList.toggle('on', !!st.nodrums && !!drums);
    $('out').textContent = '‎' + (st.out || '');
    $('out').title = st.out || '';
  }

  /* ---------- files ---------- */
  const rows = new Map();
  function renderFiles(files) {
    const list = $('files');
    $('count').textContent = files.length ? `${files.length} file${files.length === 1 ? '' : 's'}` : '';
    const keep = new Set(files.map((f) => f.path));
    for (const [p, r] of rows) if (!keep.has(p)) { r.remove(); rows.delete(p); }
    list.querySelector('.empty')?.remove();
    if (!files.length) { list.append(el('div', 'empty', 'No samples yet')); return; }
    files.forEach((f, i) => {
      let r = rows.get(f.path);
      if (!r) {
        r = el('div', 'file');
        const note = svg(ICON_NOTE); note.classList.add('note');
        const x = el('button', 'x'); x.append(svg(ICON_X)); x.title = 'Remove';
        x.addEventListener('click', () => call('remove_file', f.path));
        r.append(note, el('span', 'name', f.name), el('span', 'tags'), el('span', 'size', f.size), x);
        rows.set(f.path, r);
      }
      fillTags(r.querySelector('.tags'), f);
      if (list.children[i] !== r) list.insertBefore(r, list.children[i] || null);
    });
  }

  // BPM and key as two badges; a shimmer while detection runs, a dash if it found nothing
  function fillTags(box, f) {
    const sig = [f.bpm, f.key, f.busy, f.done].join('|');
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig;
    const out = [];
    if (f.bpm) out.push(el('span', 'badge bpm', `${f.bpm} BPM`));
    if (f.key) out.push(el('span', 'badge key', f.key));
    if (!out.length && f.busy) out.push(el('span', 'badge wait', 'BPM'), el('span', 'badge wait', 'Key'));
    else if (!out.length && f.done) out.push(el('span', 'badge none', 'No BPM / key'));
    box.replaceChildren(...out);
  }

  /* ---------- status / run ---------- */
  function setStatus([text, tone]) { const s = $('status'); s.textContent = text; s.className = 'status ' + (tone || ''); }
  function setGpu([text, tone]) { const g = $('gpu'); g.className = 'pill gpu ' + (tone || ''); g.querySelector('span').textContent = text; }
  const fmt = (s) => { s = Math.max(0, Math.floor(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  function setRun(run) {
    S.running = run.running;
    if (run.started) S.started = run.started;
    document.body.classList.toggle('locked', S.running);
    $('bar').parentElement.classList.toggle('running', S.running);
    const go = $('go');
    go.disabled = S.running; go.textContent = S.running ? 'Working' : 'Separate'; go.classList.toggle('working', S.running);
    $('stop').disabled = !S.running;
    $('yt').disabled = S.yt.busy;
    tickElapsed();
  }
  function tickElapsed() { if (S.started) $('elapsed').textContent = fmt((Date.now() - S.started) / 1000); }
  setInterval(() => S.running && tickElapsed(), 500);

  function addLog(line) {
    const pre = $('log-text');
    const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
    pre.textContent += line + '\n';
    if (pre.textContent.length > 200000) pre.textContent = pre.textContent.slice(-150000);
    if (atEnd) pre.scrollTop = pre.scrollHeight;
  }
  function showLog(open) {
    $('log').classList.toggle('open', open);
    $('log-btn').textContent = open ? 'Hide log' : 'Log';
    if (open) { const pre = $('log-text'); pre.scrollTop = pre.scrollHeight; }
  }

  /* ---------- YouTube ---------- */
  function setYt(y) {
    S.yt = y;
    const add = $('yt-add'), dl = $('yt-dl'), active = y.save ? dl : add;
    for (const b of [add, dl]) {
      b.disabled = y.busy; b.classList.remove('busy', 'analyzing'); b.style.setProperty('--p', 0);
      b.querySelector('.lbl').textContent = b === add ? 'Add' : 'Download';
    }
    $('yt').disabled = y.busy;
    if (y.busy) {
      active.classList.add('busy');
      if (y.label) active.querySelector('.lbl').textContent = y.label;
      active.classList.toggle('analyzing', y.label === 'Analyzing' || y.label === 'Converting');
      active.style.setProperty('--p', y.pct != null ? y.pct : (y.label === 'Analyzing' || y.label === 'Converting') ? 1 : 0);
    }
    if (y.clear) $('yt').value = '';
  }

  /* ---------- stems + preview ---------- */
  let resultSel;
  function renderResults(res) {
    S.results = res;
    const box = $('stems');
    if (!res.items.length) { box.hidden = true; return; }
    box.hidden = false;
    resultSel.set(res.items.map((r, i) => ({ value: i, label: r.name })), res.selected);
    const r = res.items[res.selected] || res.items[0];
    fillTags($('result-tags'), { bpm: r.bpm, key: r.key, done: true });
    $('strips').replaceChildren(...r.files.map((f, i) => {
      const s = el('div', 'strip drag'); s.dataset.name = f.instr; s.style.animationDelay = (i * 35) + 'ms';
      s.title = 'Drag into FL Studio · double-click to show the file';
      const nm = el('div', 'nm'); nm.append(svg(ICON_GRIP), document.createTextNode(f.instr.replace(/\b\w/g, (c) => c.toUpperCase())));
      const row = el('div', 'sm-row');
      if (f.instr === 'no drums' || r.files.length === 1) row.classList.add('blank');
      else for (const which of ['solo', 'mute']) {
        const b = el('button', 'sw ' + which, which === 'solo' ? 'S' : 'M');
        b.addEventListener('click', (e) => { e.stopPropagation(); call('toggle_strip', which, f.instr); });
        b.addEventListener('pointerdown', (e) => e.stopPropagation());
        row.append(b);
      }
      s.append(nm, row);
      dragOut(s, () => [f.path]);
      s.addEventListener('dblclick', () => call('open_folder_of', f.path));
      return s;
    }));
    renderPlayer();
  }

  function setPlayer(p) { S.player = p; S.playerAt = performance.now(); renderPlayer(); }
  function renderPlayer() {
    const p = S.player;
    $('play').disabled = !p.ready; $('play').classList.toggle('playing', p.playing);
    $('seek').disabled = !p.ready;
    document.querySelectorAll('.strip').forEach((s) => {
      const n = s.dataset.name;
      s.querySelector('.solo')?.classList.toggle('on', p.solo.includes(n));
      s.querySelector('.mute')?.classList.toggle('on', p.mute.includes(n));
      const audible = !p.ready || !p.names.includes(n) || ((p.solo.length ? p.solo.includes(n) : true) && !p.mute.includes(n));
      s.classList.toggle('quiet', !audible);
    });
  }

  // Drag files out: the page can't start an OS file drag, so once the pointer has moved a few
  // pixels with the button down, Python starts a native one from the window.
  function dragOut(node, paths) {
    let start = null, sent = false;
    node.addEventListener('pointerdown', (e) => { if (e.button === 0) { start = [e.clientX, e.clientY]; sent = false; } });
    node.addEventListener('pointermove', (e) => {
      if (!start || sent || !(e.buttons & 1)) return;
      if (Math.hypot(e.clientX - start[0], e.clientY - start[1]) > 5) { sent = true; start = null; call('drag_out', paths()); }
    });
    const end = () => { start = null; };
    node.addEventListener('pointerup', end); node.addEventListener('pointercancel', end);
  }

  /* ---------- 60fps loop: progress easing, player clock, aura ---------- */
  function frame(now) {
    const bar = $('bar-fill');
    if (S.progress < S.shown - 0.001) S.shown = S.progress; // reset to 0: no backwards animation
    else S.shown += (S.progress - S.shown) * 0.12;
    if (Math.abs(S.progress - S.shown) < 0.0005) S.shown = S.progress;
    bar.style.width = (S.shown * 100).toFixed(3) + '%';

    const p = S.player;
    if (p.ready) {
      let pos = p.pos + (p.playing ? (now - S.playerAt) / 1000 : 0);
      pos = Math.min(pos, p.length);
      if (!S.seeking) {
        const v = p.length ? pos / p.length : 0;
        $('seek').value = Math.round(v * 1000);
        $('seek').style.setProperty('--v', (v * 100) + '%');
      }
      const t = `${fmt(pos)} / ${fmt(p.length)}`;
      if ($('ptime').textContent !== t) $('ptime').textContent = t;
    }
    requestAnimationFrame(frame);
  }

  const aura = $('aura');
  let auraX = 0, auraY = 0, ax = 0, ay = 0, auraOn = false;
  window.addEventListener('pointermove', (e) => { auraX = e.clientX; auraY = e.clientY; if (!auraOn) { auraOn = true; aura.classList.add('on'); ax = auraX; ay = auraY; } });
  document.documentElement.addEventListener('pointerleave', () => { auraOn = false; aura.classList.remove('on'); });
  (function auraLoop() {
    ax += (auraX - ax) * 0.08; ay += (auraY - ay) * 0.08;
    aura.style.transform = `translate3d(${ax}px, ${ay}px, 0) translate(-50%, -50%)`;
    requestAnimationFrame(auraLoop);
  })();

  /* ---------- drop overlay state (native WebView2 drops can swallow the JS drop event) ---------- */
  let dragDepth = 0, dragTimer = 0;
  function endDrag() { dragDepth = 0; clearTimeout(dragTimer); document.body.classList.remove('dragging'); }

  /* ---------- events from Python ---------- */
  const on = {
    settings(v) { S.settings = v; renderSettings(); },
    files(v) { endDrag(); renderFiles(v); },
    status: setStatus,
    gpu: setGpu,
    log: addLog,
    show_log: showLog,
    run: setRun,
    progress(v) { S.progress = v; },
    yt: setYt,
    results: renderResults,
    player: setPlayer,
  };
  window.trill = { batch(list) { for (const [k, v] of list) { try { on[k] && on[k](v); } catch (e) { console.error(k, e); } } } };

  /* ---------- wiring ---------- */
  function wire() {
    modelSel = select($('model'), (v) => call('set_setting', 'model', v));
    resultSel = select($('result'), (v) => call('select_result', v));
    $('nodrums').addEventListener('click', () => call('set_setting', 'nodrums', !S.settings.nodrums));
    $('out-change').addEventListener('click', () => call('pick_output'));
    $('out-open').addEventListener('click', () => call('open_output'));
    $('drop').addEventListener('click', () => call('pick_files'));
    $('clear').addEventListener('click', () => call('clear_files'));
    const yt = (save) => call('add_youtube', $('yt').value, save);
    $('yt-add').addEventListener('click', () => yt(false));
    $('yt-dl').addEventListener('click', () => yt(true));
    $('yt').addEventListener('keydown', (e) => { if (e.key === 'Enter') yt(false); });
    $('go').addEventListener('click', () => call('start'));
    $('stop').addEventListener('click', () => call('stop'));
    $('log-btn').addEventListener('click', () => showLog(!$('log').classList.contains('open')));
    $('play').addEventListener('click', () => call('toggle_play'));
    const seek = $('seek');
    seek.addEventListener('input', () => { S.seeking = true; seek.style.setProperty('--v', (seek.value / 10) + '%');
      const t = (seek.value / 1000) * S.player.length; $('ptime').textContent = `${fmt(t)} / ${fmt(S.player.length)}`; });
    seek.addEventListener('change', () => { call('seek', seek.value / 1000); S.seeking = false; });
    dragOut($('all'), () => { const r = S.results.items[S.results.selected]; return r ? r.files.map((f) => f.path) : []; });

    // whole-window drop highlight; Python reads the real paths from the same drop event
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
    const armDrag = () => { clearTimeout(dragTimer); dragTimer = setTimeout(endDrag, 300); };
    window.addEventListener('dragenter', (e) => { if (hasFiles(e)) { dragDepth++; document.body.classList.add('dragging'); armDrag(); } e.preventDefault(); });
    window.addEventListener('dragleave', (e) => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth || !e.relatedTarget) endDrag(); });
    window.addEventListener('dragover', (e) => { e.preventDefault(); if (document.body.classList.contains('dragging')) armDrag(); });
    window.addEventListener('drop', (e) => { e.preventDefault(); endDrag(); }, true);
    window.addEventListener('dragend', endDrag);
    window.addEventListener('blur', endDrag);
    window.addEventListener('resize', () => renderSettings());
  }

  async function boot() {
    const s = await api().ready();
    S.models = s.models;
    qualitySeg = seg($('quality'), s.qualities, (v) => call('set_setting', 'quality', v));
    formatSeg = seg($('format'), s.formats, (v) => call('set_setting', 'format', v));
    on.settings(s.settings);
    renderFiles(s.files);
    setStatus(s.status); setGpu(s.gpu);
    s.log.forEach(addLog);
    setYt(s.yt); setRun(s.run);
    renderResults(s.results); setPlayer(s.player);
    requestAnimationFrame(frame);
    // fonts can change widths after first paint: re-place the segmented thumbs
    document.fonts && document.fonts.ready.then(renderSettings);
  }

  wire();
  if (window.pywebview && window.pywebview.api && window.pywebview.api.ready) boot();
  else window.addEventListener('pywebviewready', boot, { once: true });
})();
