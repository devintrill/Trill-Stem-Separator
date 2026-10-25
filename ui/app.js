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

  // Segmented control with an Apple-style liquid thumb, driven by springs each frame. Press and hold stretches
  // it toward the option under the pointer; on release it compresses as it travels, then stretches back out
  // to its normal size as it arrives. Springs are near critical damping, so there's barely any wobble.
  const reduceMotion = false;  // Windows with Animation effects off reports reduced motion; Devin wants motion on
  function seg(root, values, onPick) {
    const thumb = el('span', 'thumb');
    let api;
    const buttons = values.map((v) => { const b = el('button', '', v); b.addEventListener('click', () => { api.set(v); onPick(v); }); return b; });
    root.replaceChildren(thumb, ...buttons);
    let x = null, w = 0, tx = 0, tw = 0, vx = 0, vw = 0, p = 0, pt = 0, vp = 0, c = 0, vc = 0, lean = 0, raf = 0, last = 0;
    const draw = () => {
      const extra = Math.max(0, p) * 30, full = w + extra, comp = Math.max(0, c);
      thumb.style.width = (full * (1 - comp)) + 'px';
      thumb.style.transform = `translateX(${x - extra * (1 - lean) / 2 + full * comp / 2}px) scaleY(${1 - comp * 0.55})`;
    };
    function step(t) {
      const dt = Math.min(0.034, last ? (t - last) / 1000 : 0.016); last = t;
      vx += (-(x - tx) * 250 - vx * 30) * dt; x += vx * dt;
      vw += (-(w - tw) * 250 - vw * 30) * dt; w += vw * dt;
      vp += (-(p - pt) * 320 - vp * 32) * dt; p += vp * dt;
      // compression follows the travel speed and relaxes as the thumb slows, which is the stretch back out
      const want = Math.min(0.28, Math.abs(vx) / 1500);
      vc += ((want - c) * 380 - vc * 36) * dt; c += vc * dt;
      draw();
      const moving = Math.abs(x - tx) + Math.abs(vx) + Math.abs(w - tw) + Math.abs(vw) + Math.abs(p - pt) + Math.abs(vp) + Math.abs(c) + Math.abs(vc);
      if (moving > 0.01) raf = requestAnimationFrame(step);
      else { x = tx; w = tw; p = pt; c = vx = vw = vp = vc = 0; raf = 0; last = 0; draw(); }
    }
    const kick = () => { if (!raf) raf = requestAnimationFrame(step); };
    root.addEventListener('pointerdown', (e) => {
      const b = e.target.closest('button'), to = b ? b.offsetLeft - 4 : tx;
      lean = Math.abs(to - tx) < 1 ? 0 : Math.sign(to - tx);
      pt = 1; kick();
    });
    for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) root.addEventListener(ev, () => { pt = 0; kick(); });
    api = {
      set(value) {
        const i = Math.max(0, values.indexOf(value));
        buttons.forEach((b, j) => b.classList.toggle('on', j === i));
        const b = buttons[i];
        tx = b.offsetLeft - 4; tw = b.offsetWidth;
        if (x === null || !w) { x = tx; w = tw; draw(); return; }
        kick();
      },
    };
    return api;
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
  let booted = false;
  function setStatus([text, tone]) {
    const s = $('status');
    if (booted && tone === 'good' && s.textContent !== text) confetti();
    s.textContent = text; s.className = 'status ' + (tone || '');
  }

  /* ---------- confetti: a soft burst behind the panels when something finishes ---------- */
  const confetti = (() => {
    const cv = document.createElement('canvas'); cv.className = 'confetti';
    document.body.insertBefore(cv, document.querySelector('.shell'));
    const ctx = cv.getContext('2d'), colors = ['#ffffff', '#f3dfa6', '#eab6ff', '#9fe7f5', '#c9c3ff'];
    let parts = [], raf = 0, last = 0;
    function frame(t) {
      const dt = Math.min(0.033, last ? (t - last) / 1000 : 0.016); last = t;
      const dpr = window.devicePixelRatio || 1, W = innerWidth, H = innerHeight;
      if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
      parts = parts.filter((q) => q.life > 0 && q.y < H + 40);
      for (const q of parts) {
        q.vy += 1100 * dt; q.vx *= 1 - 1.1 * dt; q.vy *= 1 - 1.1 * dt;
        q.x += (q.vx + Math.sin(t / 300 + q.seed) * 30) * dt; q.y += q.vy * dt;
        q.rot += q.spin * dt; q.flip += q.flipV * dt; q.life -= dt;
        ctx.save(); ctx.globalAlpha = Math.min(1, q.life / 0.8);
        ctx.translate(q.x, q.y); ctx.rotate(q.rot); ctx.scale(1, Math.cos(q.flip));
        ctx.fillStyle = q.c;
        if (q.round) { ctx.beginPath(); ctx.arc(0, 0, q.s * 0.45, 0, Math.PI * 2); ctx.fill(); }
        else { ctx.beginPath(); ctx.roundRect(-q.s / 2, -q.s / 4, q.s, q.s / 2, 1.5); ctx.fill(); }
        ctx.restore();
      }
      raf = parts.length ? requestAnimationFrame(frame) : 0;
      if (!raf) { last = 0; ctx.clearRect(0, 0, W, H); }
    }
    return () => {
      if (reduceMotion) return;
      const W = innerWidth, H = innerHeight;
      for (const [ox, dir] of [[W * 0.18, 1], [W * 0.82, -1]]) {
        for (let i = 0; i < 45; i++) {
          const a = (-90 + dir * (8 + Math.random() * 30)) * Math.PI / 180, v = 1500 + Math.random() * 900;
          parts.push({ x: ox, y: H + 10, vx: Math.cos(a) * v, vy: Math.sin(a) * v, s: 12 + Math.random() * 16,
            c: colors[(Math.random() * colors.length) | 0], round: Math.random() < 0.3, rot: Math.random() * 6,
            spin: (Math.random() - 0.5) * 10, flip: Math.random() * 6, flipV: 4 + Math.random() * 8,
            seed: Math.random() * 10, life: 2.4 + Math.random() * 1.2 });
        }
      }
      if (!raf) raf = requestAnimationFrame(frame);
    };
  })();
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
    const shown = res.items[res.selected] && res.items[res.selected].name;
    if (shown !== waves.of) waves = { of: shown, names: [], peaks: [] };
    const box = $('stems');
    if (!res.items.length) { box.hidden = true; return; }
    box.hidden = false;
    resultSel.set(res.items.map((r, i) => ({ value: i, label: r.name })), res.selected);
    const r = res.items[res.selected] || res.items[0];
    fillTags($('result-tags'), { bpm: r.bpm, key: r.key, done: true });
    $('strips').classList.toggle('single', r.files.length === 1);
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
    drawWaves();
  }

  /* ---------- stem waveforms: a slim band along the bottom of each strip, lit up to the playhead ---------- */
  let waves = { of: null, names: [], peaks: [] };
  function setPeaks(v) { waves = { of: waves.of, names: v.names, peaks: v.peaks }; drawWaves(); }
  function drawWaves() {
    document.querySelectorAll('.strip').forEach((s) => {
      s.querySelector('.wave')?.remove();
      const i = waves.names.indexOf(s.dataset.name);
      if (i < 0) return;
      const wave = el('div', 'wave'), lit = el('div', 'wave-lit');
      const base = document.createElement('canvas'), top = document.createElement('canvas');
      lit.append(top); wave.append(base, lit); s.prepend(wave);
      const r = wave.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      if (!r.width) { wave.remove(); return; }
      const bars = waves.peaks[i], step = r.width / bars.length, mid = r.height / 2;
      for (const [cv, color] of [[base, 'rgba(255, 255, 255, 0.14)'], [top, 'rgba(159, 231, 245, 0.6)']]) {
        cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
        cv.style.width = r.width + 'px'; cv.style.height = r.height + 'px';
        const ctx = cv.getContext('2d'); ctx.scale(dpr, dpr); ctx.fillStyle = color;
        bars.forEach((v, k) => {
          const h = Math.max(1, v * r.height);
          ctx.beginPath(); ctx.roundRect(k * step + step * 0.22, mid - h / 2, Math.max(1, step * 0.56), h, 1); ctx.fill();
        });
      }
    });
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
        $('strips').style.setProperty('--pp', (v * 100) + '%');
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
    peaks: setPeaks,
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
    window.addEventListener('resize', () => { renderSettings(); drawWaves(); });
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
    booted = true;
    // fonts can change widths after first paint: re-place the segmented thumbs
    document.fonts && document.fonts.ready.then(renderSettings);
  }

  wire();
  if (window.pywebview && window.pywebview.api && window.pywebview.api.ready) boot();
  else window.addEventListener('pywebviewready', boot, { once: true });
})();
