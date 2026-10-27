(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const api = () => window.pywebview.api;
  const call = (name, ...args) => { try { return api()[name](...args); } catch (e) { return Promise.resolve(); } };
  const svg = (inner, vb = '0 0 24 24') => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', vb); s.innerHTML = inner; return s; };
  const ICON_X = '<path d="M6 6l12 12M18 6L6 18"/>';
  const ICON_GRIP = '<circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/>';
  const title = (s) => s.replace(/\b\w/g, (c) => c.toUpperCase());

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
          const b = el('button', it.value === current ? 'on' : '');
          if (it.sub) { const t = el('span', 'item'); t.append(el('span', '', it.label), el('span', 'sub', it.sub)); b.append(t); }
          else b.textContent = it.label;
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
    modelSel.set(S.models.map((m) => ({ value: m.name, label: m.name, sub: m.desc })), st.model);
    qualitySeg.set(st.quality);
    formatSeg.set(st.format);
    const drums = (S.models.find((m) => m.name === st.model) || {}).drums;
    const sw = $('nodrums');
    sw.disabled = !drums;
    sw.classList.toggle('on', !!st.nodrums && !!drums);
    $('out').textContent = '‎' + (st.out || '');
    $('out').title = st.out || '';
    renderPending();
  }

  /* ---------- files ---------- */
  const rows = new Map();
  function renderFiles(files) {
    const list = $('files');
    $('count').textContent = files.length ? `${files.length} file${files.length === 1 ? '' : 's'}` : '';
    const keep = new Set(files.map((f) => f.path));
    for (const [p, r] of rows) if (!keep.has(p)) { r.remove(); rows.delete(p); }
    // empty: the drop zone says it all, there's nothing to clear and nothing to separate
    S.hasFiles = files.length > 0;
    $('clear').style.visibility = S.hasFiles ? '' : 'hidden';
    renderGo();
    files.forEach((f, i) => {
      let r = rows.get(f.path);
      if (!r) {
        r = el('div', 'file');
        const grip = el('span', 'grip'); grip.append(svg(ICON_GRIP));
        const x = el('button', 'x'); x.append(svg(ICON_X)); x.title = 'Remove';
        x.addEventListener('click', () => call('remove_file', f.path));
        x.addEventListener('pointerdown', (e) => e.stopPropagation());
        r.append(grip, el('span', 'name', f.name), el('span', 'tags'), el('span', 'size', f.size), x);
        // the original can go straight into the DAW too, e.g. a YouTube rip you don't want split
        r.title = 'Drag into your DAW';
        dragOut(r, () => [f.path]);
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
    if (booted && tone === 'good' && s.textContent !== text) confetti(S.finishedBy);
    s.textContent = text; s.className = 'status ' + (tone || '');
  }

  /* ---------- logo: the pad mark doubles as the app's status ---------- */
  // ready: one pad lit. working: the light steps around the grid. nogpu: none lit.
  const tones = { gpu: '' };
  function renderLogo() {
    const state = S.running || S.yt.busy ? 'working' : tones.gpu === 'warn' ? 'nogpu' : 'ready';
    const logo = $('logo');
    if (logo.dataset.state !== state) logo.dataset.state = state;
  }

  /* ---------- confetti: one short pop of gold paper strips out of the button whose job just finished ---------- */
  // Thin strips, strong air drag: they stop quickly, then tumble and drift down like real paper.
  const confetti = (() => {
    const cv = document.createElement('canvas'); cv.className = 'confetti';
    document.body.insertBefore(cv, document.querySelector('.shell'));
    const ctx = cv.getContext('2d'), colors = ['#f3c94a', '#f6d261', '#efc23a', '#e2b33a', '#f7da7a', '#d9a92e', '#fbe39a'];
    let parts = [], raf = 0, last = 0;
    function frame(t) {
      const dt = Math.min(0.033, last ? (t - last) / 1000 : 0.016); last = t;
      const dpr = window.devicePixelRatio || 1, W = innerWidth, H = innerHeight;
      if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
      parts = parts.filter((q) => q.life > 0 && q.y < H + 20);
      for (const q of parts) {
        q.vy += 900 * dt; q.vx *= 1 - 2.6 * dt; q.vy *= 1 - 2.6 * dt;  // heavy drag caps the fall speed
        q.x += (q.vx + Math.sin(t / 260 + q.seed) * 22) * dt; q.y += q.vy * dt;
        q.rot += q.spin * dt; q.flip += q.flipV * dt; q.life -= dt;
        ctx.save(); ctx.globalAlpha = Math.min(1, q.life / 0.5);
        ctx.translate(q.x, q.y); ctx.rotate(q.rot); ctx.scale(1, Math.cos(q.flip));
        ctx.fillStyle = q.c;
        ctx.fillRect(-q.w / 2, -q.h / 2, q.w, q.h);
        ctx.restore();
      }
      raf = parts.length ? requestAnimationFrame(frame) : 0;
      if (!raf) { last = 0; ctx.clearRect(0, 0, W, H); }
    }
    return (from) => {
      if (reduceMotion) return;
      const r = (from || $('go')).getBoundingClientRect(), ox = r.left + r.width / 2, oy = r.top + 6;
      for (let i = 0; i < 38; i++) {
        const a = (-90 + (Math.random() - 0.5) * 80) * Math.PI / 180, v = 700 + Math.random() * 500;
        parts.push({ x: ox + (Math.random() - 0.5) * r.width * 0.6, y: oy, vx: Math.cos(a) * v, vy: Math.sin(a) * v,
          w: 3 + Math.random() * 1.5, h: 8 + Math.random() * 4, c: colors[(Math.random() * colors.length) | 0],
          rot: Math.random() * 6, spin: (Math.random() - 0.5) * 8, flip: Math.random() * 6, flipV: 6 + Math.random() * 8,
          seed: Math.random() * 10, life: 1.6 + Math.random() * 0.7 });
      }
      if (!raf) raf = requestAnimationFrame(frame);
    };
  })();
  function setGpu([text, tone]) { const g = $('gpu'); g.className = 'pill gpu ' + (tone || ''); g.querySelector('span').textContent = text; tones.gpu = tone || ''; renderLogo(); }
  const fmt = (s) => { s = Math.max(0, Math.floor(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  function setRun(run) {
    if (S.running && !run.running) S.finishedBy = $('go');  // the "Done!" that follows pops out of Separate
    S.running = run.running;
    if (run.started) S.started = run.started;
    document.body.classList.toggle('locked', S.running);
    $('bar').parentElement.classList.toggle('running', S.running);
    renderGo();
    $('stop').disabled = !S.running;
    $('yt').disabled = S.yt.busy;
    tickElapsed();
    renderLogo(); renderPending();
  }
  // Separate: lit while there's something to split, pressed in while it works, an unlit key with no samples
  function renderGo() {
    const go = $('go'), idle = !S.running && !S.hasFiles;
    go.disabled = S.running || idle;
    go.textContent = S.running ? 'Working' : 'Separate';
    go.classList.toggle('working', S.running);
    go.classList.toggle('idle', idle);
  }
  function tickElapsed() {
    if (!S.started) return;
    const secs = (Date.now() - S.started) / 1000, p = S.progress;
    // a rough time left once there's enough progress to go on
    const left = S.running && p > 0.05 && p < 1 ? ` · ${fmt(secs * (1 - p) / p)} left` : '';
    $('elapsed').textContent = fmt(secs) + left;
  }
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
  // Only touch what changed, so the busy button's flowing fill doesn't restart on every progress tick.
  function setYt(y) {
    if (S.yt.busy && !y.busy) S.finishedBy = $('yt-add');  // and "Added ..." out of Add
    S.yt = y;
    const b = $('yt-add'), lbl = b.querySelector('.lbl'), on = !!y.busy;
    const slow = ['Fetching', 'Converting', 'Analyzing'].includes(y.label);  // no percentage: a full, flowing fill
    b.disabled = on;
    b.classList.toggle('busy', on);
    b.classList.toggle('analyzing', on && slow);
    // a failed download: the key turns red as Retry and the reason shows under the row.
    // Not a link at all: just the reason; the key stays Add, since retrying the same text can't work.
    const err = !on && y.error ? y.error : '', retry = !!err && !!y.retry;
    b.classList.toggle('failed', retry);
    b.closest('.yt').classList.toggle('failed', !!err);
    $('yt-error').hidden = !err; $('yt-error').textContent = err;
    const text = on && y.label ? y.label : retry ? 'Retry' : 'Add';
    if (lbl.textContent !== text) lbl.textContent = text;
    const pct = String(on ? (y.pct != null ? y.pct : slow ? 1 : 0) : 0);
    if (b.style.getPropertyValue('--p') !== pct) b.style.setProperty('--p', pct);
    $('yt').disabled = !!y.busy;
    if (y.clear) $('yt').value = '';
    renderLogo();
  }


  /* ---------- stems + preview ---------- */
  let resultSel;
  function renderResults(res) {
    commitRemoval();  // the pads are about to be rebuilt; a waiting removal goes through now
    S.results = res;
    const shown = res.items[res.selected] && res.items[res.selected].name;
    if (shown !== waves.of) waves = { of: shown, names: [], peaks: [] };
    const box = $('stems');
    box.classList.toggle('empty', !res.items.length);
    if (!res.items.length) { renderPending(); return; }
    resultSel.set(res.items.map((r, i) => ({ value: i, label: r.name })), res.selected);
    const r = res.items[res.selected] || res.items[0];
    fillTags($('result-tags'), { bpm: r.bpm, key: r.key, done: true });
    $('strips').classList.toggle('single', r.files.length === 1);
    $('strips').dataset.sig = '';
    $('strips').replaceChildren(...r.files.map((f, i) => {
      const s = el('div', 'strip drag'); s.dataset.name = f.instr; s.style.animationDelay = (i * 110) + 'ms';
      s.title = 'Drag into your DAW · double-click to show the file';
      const grip = el('span', 'grip'); grip.append(svg(ICON_GRIP));
      const nm = el('div', 'nm'); nm.append(grip, el('span', 'label', title(f.instr)));
      const top = el('div', 'top');
      const row = el('div', 'sm-row');
      if (f.instr === 'no drums' || r.files.length === 1) row.classList.add('blank');
      else for (const which of ['solo', 'mute']) {
        const b = el('button', 'sw ' + which, which === 'solo' ? 'S' : 'M');
        b.addEventListener('click', (e) => { e.stopPropagation(); call('toggle_strip', which, f.instr); });
        b.addEventListener('pointerdown', (e) => e.stopPropagation());
        row.append(b);
      }
      top.append(nm, row);
      // x: the stem empties, with a short Undo before its file goes to the Recycle Bin
      const rm = el('button', 'rm'); rm.append(svg(ICON_X));
      rm.setAttribute('aria-label', `Remove ${title(f.instr)}`);
      rm.title = 'Remove';
      rm.addEventListener('click', (e) => { e.stopPropagation(); removeStem(res.selected, f.instr, s); });
      rm.addEventListener('pointerdown', (e) => e.stopPropagation());
      s.append(top, rm);
      dragOut(s, () => [f.path]);
      s.addEventListener('dblclick', () => call('open_folder_of', f.path));
      return s;
    }));
    renderPlayer();
    drawWaves();
  }

  /* ---------- removing a stem: its pad empties and a short Undo shows; the file goes to the Recycle Bin when
     the bar runs out, or straight away if another stem is removed or the pads change ---------- */
  let removal = null;  // { index, instr, strip, timer }
  function removeStem(index, instr, strip) {
    commitRemoval();
    strip.classList.add('gone');
    $('toast-text').textContent = `${title(instr)} removed`;
    const toast = $('toast');
    toast.hidden = false; toast.classList.remove('run'); void toast.offsetWidth; toast.classList.add('run');
    removal = { index, instr, strip, timer: setTimeout(commitRemoval, 5000) };
  }
  function commitRemoval() {
    if (!removal) return;
    const r = removal; removal = null;
    clearTimeout(r.timer); $('toast').hidden = true;
    call('remove_stem', r.index, r.instr);
  }
  function undoRemoval() {
    if (!removal) return;
    clearTimeout(removal.timer); removal.strip.classList.remove('gone');
    removal = null; $('toast').hidden = true;
  }

  // Before the first result: sunken, empty pads for the stems the chosen model will make, so the
  // window keeps its layout when the real (raised) pads arrive.
  function renderPending() {
    const m = S.models.find((x) => x.name === S.settings.model);
    if (S.results.items.length || !m) return;
    const names = [...m.stems, ...(S.settings.nodrums && m.drums ? ['no drums'] : [])];
    $('stems-hint').textContent = S.running ? `${names.length} on the way` : 'Your stems land here';
    const strips = $('strips'), sig = names.join('|');
    if (strips.dataset.sig === sig) return;
    strips.dataset.sig = sig;
    strips.classList.remove('single');
    strips.replaceChildren(...names.map((n) => {
      const s = el('div', 'strip pending');
      s.append(el('div', 'nm', title(n)), el('div', 'line'));
      return s;
    }));
  }

  /* ---------- stem waveforms: a band under each pad's name row, lit up to the playhead ---------- */
  let waves = { of: null, names: [], peaks: [] };
  function setPeaks(v) { waves = { of: waves.of, names: v.names, peaks: v.peaks }; drawWaves(); }
  function drawWaves() {
    document.querySelectorAll('.strip').forEach((s) => {
      s.querySelector('.wave')?.remove();
      const i = waves.names.indexOf(s.dataset.name);
      if (i < 0) return;
      const wave = el('div', 'wave'), lit = el('div', 'wave-lit');
      const base = document.createElement('canvas'), top = document.createElement('canvas');
      lit.append(top); wave.append(base, lit); s.append(wave);
      const r = wave.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      if (!r.width) { wave.remove(); return; }
      const bars = waves.peaks[i], step = r.width / bars.length, mid = r.height / 2;
      for (const [cv, color] of [[base, 'rgba(237, 237, 234, 0.26)'], [top, '#ededea']]) {
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
    let start = null, sent = false, grab = [0, 0];
    node.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      const r = node.getBoundingClientRect();
      start = [e.clientX, e.clientY]; grab = [e.clientX - r.left, e.clientY - r.top]; sent = false;
    });
    node.addEventListener('pointermove', (e) => {
      if (!start || sent || !(e.buttons & 1)) return;
      if (Math.hypot(e.clientX - start[0], e.clientY - start[1]) > 5) {
        sent = true; start = null;
        const pic = dragPicture(node, grab);
        lift(node, grab, e.clientX, e.clientY);
        // resolves when the drag is dropped
        Promise.resolve(call('drag_out', paths(), pic.png, pic.gx, pic.gy)).finally(land);
      }
    });
    const end = () => { start = null; };
    node.addEventListener('pointerup', end); node.addEventListener('pointercancel', end);
  }

  // A picture of what's dragged, drawn from the element itself: its raised face with a gold edge and a soft
  // shadow (in a transparent margin), the lit grip, the name cut to fit, BPM/key badges, S/M and the waveform.
  // Python puts it in a window that stays under the pointer, outside the app too.
  function dragPicture(node, grab) {
    const r = node.getBoundingClientRect(), dpr = window.devicePixelRatio || 1, m = 20;  // m: room for the shadow
    const radius = parseFloat(getComputedStyle(node).borderTopLeftRadius) || 12;
    const cv = document.createElement('canvas');
    cv.width = Math.round((r.width + m * 2) * dpr); cv.height = Math.round((r.height + m * 2) * dpr);
    const c = cv.getContext('2d'); c.scale(dpr, dpr); c.translate(m, m);
    const box = (el) => { const b = el.getBoundingClientRect(); return [b.left - r.left, b.top - r.top, b.width, b.height]; };
    const round = (x, y, w, h, rad) => { c.beginPath(); c.roundRect(x, y, w, h, rad); };
    const write = (el, color, center) => {
      const [x, y, w, h] = box(el), st = getComputedStyle(el);
      let t = el.textContent.trim();
      c.font = st.font; c.fillStyle = color || st.color; c.textBaseline = 'middle'; c.textAlign = center ? 'center' : 'left';
      if (c.measureText(t).width > w) { while (t.length > 1 && c.measureText(t + '…').width > w) t = t.slice(0, -1); t += '…'; }
      c.fillText(t, center ? x + w / 2 : x, y + h / 2);
    };
    const dots = (cx, cy, color) => {
      c.fillStyle = color;
      for (const dx of [-2.6, 2.6]) for (const dy of [-5, 0, 5]) { c.beginPath(); c.arc(cx + dx, cy + dy, 1.5, 0, Math.PI * 2); c.fill(); }
    };

    c.save();
    c.shadowColor = 'rgba(0, 0, 0, 0.55)'; c.shadowBlur = 18; c.shadowOffsetY = 8;
    const face = c.createLinearGradient(0, 0, 0, r.height);
    face.addColorStop(0, '#363642'); face.addColorStop(1, '#2b2b36');
    c.fillStyle = face; round(0, 0, r.width, r.height, radius); c.fill();
    c.restore();
    c.strokeStyle = '#f3c94a'; c.lineWidth = 1.5; round(0.75, 0.75, r.width - 1.5, r.height - 1.5, radius - 0.75); c.stroke();

    node.querySelectorAll('.grip').forEach((g) => {
      const [x, y, w, h] = box(g);
      c.fillStyle = 'rgba(0, 0, 0, 0.22)'; round(x, y, w, h, 7); c.fill();
      dots(x + w / 2, y + h / 2, '#f3c94a');  // lit: it's in your hand
    });
    node.querySelectorAll('.badge, .sw').forEach((b) => {
      const [x, y, w, h] = box(b), st = getComputedStyle(b), rad = parseFloat(st.borderTopLeftRadius) || 0;
      const lit = b.classList.contains('on') && (b.classList.contains('solo') ? '#efc23a' : b.classList.contains('mute') ? '#e2716c' : '');
      c.fillStyle = lit || st.backgroundColor; round(x, y, w, h, Math.min(rad, h / 2)); c.fill();
      const ring = /(rgba?\([^)]*\)) 0px 0px 0px 1px inset/.exec(st.boxShadow);
      if (ring && !lit) { c.strokeStyle = ring[1]; c.lineWidth = 1; round(x + 0.5, y + 0.5, w - 1, h - 1, Math.min(rad, h / 2)); c.stroke(); }
      write(b, lit ? '#141209' : st.color, true);
    });
    node.querySelectorAll('.label, .name, .size').forEach((t) => write(t));
    node.querySelectorAll('.wave > canvas').forEach((src) => {
      const [x, y, w, h] = box(src);
      try { c.drawImage(src, x, y, w, h); } catch (e) { /* nothing drawn yet */ }
    });
    if (node.classList.contains('chip')) {  // Drag all
      dots(16, r.height / 2, '#f3c94a');
      c.font = getComputedStyle(node).font; c.fillStyle = '#f4f4f0'; c.textBaseline = 'middle'; c.textAlign = 'left';
      c.fillText(node.textContent.trim(), 28, r.height / 2);
    }
    return { png: cv.toDataURL('image/png').split(',')[1],
             gx: Math.round((grab[0] + m) * dpr), gy: Math.round((grab[1] + m) * dpr) };
  }

  // While something is dragged out, the original stays behind as an empty well. Python's picture window
  // follows the pointer; the page's own copy below is only a fallback, shown if that window hasn't
  // appeared (drag_native) shortly after the drag starts, so the two never show at once.
  let ghost = null, grabAt = [0, 0];
  function lift(node, grab, x, y) {
    land();
    const r = node.getBoundingClientRect(), src = node.querySelectorAll('canvas');
    ghost = node.cloneNode(true);
    ghost.classList.add('drag-ghost', 'waiting');  // not 'ghost': that's the text-button style
    const mine = ghost;
    setTimeout(() => { if (ghost === mine && !mine.classList.contains('native')) mine.classList.remove('waiting'); }, 250);
    ghost.style.width = r.width + 'px'; ghost.style.height = r.height + 'px';
    ghost.querySelectorAll('canvas').forEach((cv, i) => {  // cloned canvases come out blank
      try { cv.getContext('2d').drawImage(src[i], 0, 0); } catch (e) { /* an empty canvas has nothing to copy */ }
    });
    ghost.from = node; grabAt = grab;
    node.classList.add('lifted');
    document.body.append(ghost);
    S.draggingOut = true;
    moveGhost(x, y);
  }
  function moveGhost(x, y) {
    if (!ghost) return;
    ghost.classList.remove('away');
    ghost.style.transform = `translate3d(${x - grabAt[0]}px, ${y - grabAt[1]}px, 0) rotate(-2deg) scale(1.03)`;
  }
  function land() {
    S.draggingOut = false;
    if (!ghost) return;
    ghost.from.classList.remove('lifted');
    ghost.remove(); ghost = null;
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

  /* ---------- hover hints: any title shows as a raised bubble in the app's style, not a Windows tooltip ---------- */
  const tip = el('div', 'tip'); tip.setAttribute('role', 'tooltip'); document.body.append(tip);
  let tipFor = null, tipTimer = 0;
  function hideTip() { clearTimeout(tipTimer); tipFor = null; tip.classList.remove('on'); }
  function showTip(t) {
    if (!t.isConnected || !t.dataset.tip || S.draggingOut) return;
    tip.textContent = t.dataset.tip; tip.classList.add('on');
    const r = t.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    const x = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2));
    const y = r.top - h - 8 < 8 ? r.bottom + 8 : r.top - h - 8;
    tip.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  }
  document.addEventListener('pointerover', (e) => {
    const t = e.target.closest && e.target.closest('[title], [data-tip]');
    if (!t || t === tipFor) return;
    if (t.hasAttribute('title')) { t.dataset.tip = t.title; t.removeAttribute('title'); }  // no native tooltip too
    hideTip(); tipFor = t;
    tipTimer = setTimeout(() => showTip(t), 450);
  });
  document.addEventListener('pointerout', (e) => { if (tipFor && !tipFor.contains(e.relatedTarget)) hideTip(); });
  document.addEventListener('pointerdown', hideTip, true);

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
    drag_native() { if (ghost) ghost.classList.add('native'); },
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
    const yt = () => call('add_youtube', $('yt').value);
    $('yt-add').addEventListener('click', yt);
    $('yt').addEventListener('keydown', (e) => { if (e.key === 'Enter') yt(); });
    $('yt').addEventListener('input', () => { if (S.yt.error) setYt({ busy: false }); });  // editing clears the error
    $('go').addEventListener('click', () => call('start'));
    $('stop').addEventListener('click', () => call('stop'));
    $('log-btn').addEventListener('click', () => showLog(!$('log').classList.contains('open')));
    $('toast-undo').addEventListener('click', undoRemoval);
    $('play').addEventListener('click', () => call('toggle_play'));
    const seek = $('seek');
    seek.addEventListener('input', () => { S.seeking = true; seek.style.setProperty('--v', (seek.value / 10) + '%');
      const t = (seek.value / 1000) * S.player.length; $('ptime').textContent = `${fmt(t)} / ${fmt(S.player.length)}`; });
    seek.addEventListener('change', () => { call('seek', seek.value / 1000); S.seeking = false; });
    dragOut($('all'), () => { const r = S.results.items[S.results.selected]; return r ? r.files.map((f) => f.path) : []; });

    // whole-window drop highlight; Python reads the real paths from the same drop event
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
    const armDrag = () => { clearTimeout(dragTimer); dragTimer = setTimeout(endDrag, 300); };
    // a stem dragged out of the app passes over it too: move its copy, but don't offer "Drop to add"
    window.addEventListener('dragenter', (e) => { e.preventDefault(); if (S.draggingOut) return moveGhost(e.clientX, e.clientY);
      if (hasFiles(e)) { dragDepth++; document.body.classList.add('dragging'); armDrag(); } });
    window.addEventListener('dragleave', (e) => { if (S.draggingOut) { if (!e.relatedTarget && ghost) ghost.classList.add('away'); return; }
      dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth || !e.relatedTarget) endDrag(); });
    window.addEventListener('dragover', (e) => { e.preventDefault(); if (S.draggingOut) return moveGhost(e.clientX, e.clientY);
      if (document.body.classList.contains('dragging')) armDrag(); });
    window.addEventListener('drop', (e) => { e.preventDefault(); endDrag(); if (S.draggingOut) land(); }, true);
    window.addEventListener('dragend', endDrag);

    // Space plays or pauses the stems; 1-9 solo a stem. Typing in a field is left alone.
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey || e.altKey || e.metaKey || e.repeat || e.target.closest('input, textarea')) return;
      if (e.code === 'Space' && S.player.ready) { e.preventDefault(); call('toggle_play'); return; }
      const strip = /^[1-9]$/.test(e.key) && document.querySelectorAll('.strip.drag')[+e.key - 1];
      if (strip && strip.querySelector('.solo')) call('toggle_strip', 'solo', strip.dataset.name);
    });
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
