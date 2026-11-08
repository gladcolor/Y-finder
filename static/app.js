/* Y-finder front end: map, chat (server-sent events), frame graph, data cards, catalog, GeoLibre hand-off. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = (u) => { try { const x = new URL(u, location.href); return /^https?:$/.test(x.protocol) ? x.href : ''; } catch { return ''; } };
  const store = { get(k) { try { return sessionStorage.getItem(k); } catch { return null; } }, set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } } };

  const PALETTE = ['#2563eb', '#db2777', '#059669', '#7c3aed', '#ea580c', '#0891b2', '#65a30d', '#be123c'];
  const EXAMPLES = [
    ['Public health (MPH student)', 'Which standing water sites in New Haven are most likely to breed Culex mosquitoes this summer, to guide larval control?'],
    ['Environment (forest ecology)', 'How has tree canopy changed in New Haven neighborhoods since 2012, and is canopy loss linked to summer heat?'],
    ['Environmental health', 'Estimate daily PM2.5 exposure by census tract in Connecticut during the June 2023 wildfire smoke, for an asthma study.'],
    ['Economics (housing)', 'Did Connecticut towns that eased multifamily zoning after 2021 see more new housing permits?'],
    ['History (digital humanities)', "Find historical maps of New Haven's waterfront from 1850 to 1950 to trace how the shoreline changed."],
    ['Global health', 'Map land cover change and malaria risk around villages in western Kenya from 2015 to 2025.'],
  ];

  const state = {
    sid: store.get('yf_sid') || '', model: '', busy: false, code: store.get('yf_code') || '',
    layers: new Map(), colorIdx: 0, mapReady: false, pending: [],
    graph: null, scope: null, picks: [], methods: [], nextSteps: [], cy: null, config: null,
    gisMode: false, gisFull: false, catKind: 'any', catAccess: 'any',
  };

  // ------------------------------------------------------------------ helpers
  function toast(msg, ms = 3200) {
    const t = $('#toast'); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
  }
  function headers(json = true) {
    const h = {}; if (json) h['Content-Type'] = 'application/json'; if (state.code) h['X-Access-Code'] = state.code; return h;
  }
  async function getJSON(url) { const r = await fetch(url, { headers: headers(false) }); const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status, body: j }); return j; }
  function md(text) {
    const html = DOMPurify.sanitize(marked.parse(text || '', { breaks: false, gfm: true }));
    const d = document.createElement('div'); d.innerHTML = html;
    $$('a', d).forEach((a) => { a.target = '_blank'; a.rel = 'noopener'; });
    return d.innerHTML;
  }
  const fitLabel = { good: 'Fits', partial: 'Partly fits', poor: 'Poor fit', unknown: 'Not checked' };
  const accessLabel = { open: 'Open', yale: 'Yale access', request: 'Registration', paid: 'Paid', unknown: 'Check access' };
  function badge(kind, label, title) { return `<span class="badge ${esc(kind)}" title="${esc(title || '')}">${esc(label)}</span>`; }
  function fitBadges(f) {
    if (!f) return '';
    const part = (k, name) => f[k] ? badge(f[k].label, `${name}: ${f[k].label === 'good' ? 'yes' : f[k].label}`, f[k].reason) : '';
    return badge(f.overall, fitLabel[f.overall] || f.overall, 'Overall fit to your scope') + part('extent', 'Area') + part('time', 'Time') +
      part('resolution', 'Detail') + (f.access ? badge(f.access.label, accessLabel[f.access.label] || f.access.label, f.access.reason) : '');
  }
  function padding() {
    const mobile = innerWidth <= 720; const collapsed = document.body.classList.contains('collapsed');
    return mobile ? { top: 70, left: 30, right: 30, bottom: collapsed ? 160 : innerHeight * 0.62 + 20 }
      : { top: 70, right: 60, bottom: 60, left: collapsed ? 60 : $('#panel').offsetWidth + 50 };
  }
  function fit(bbox) {
    if (!bbox || bbox.length !== 4 || !state.mapReady) return;
    try { map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: padding(), maxZoom: 16, duration: 900 }); } catch { /* invalid bounds */ }
  }

  // ------------------------------------------------------------------ map
  const map = new maplibregl.Map({
    container: 'map', style: 'https://tiles.openfreemap.org/styles/positron',
    center: [-72.925, 41.31], zoom: 11.6, attributionControl: { compact: true }, hash: false,
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'bottom-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');
  map.on('load', () => {
    const firstSymbol = (map.getStyle().layers.find((l) => l.type === 'symbol') || {}).id;
    map.addSource('esri-imagery', {
      type: 'raster', tileSize: 256, maxzoom: 19,
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      attribution: 'Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    });
    map.addLayer({ id: 'esri-imagery', type: 'raster', source: 'esri-imagery', layout: { visibility: 'none' } }, firstSymbol);
    state.firstSymbol = firstSymbol;
    state.mapReady = true;
    const p = state.pending.splice(0); p.forEach((d) => addLayer(d));
  });
  map.on('error', (e) => { if (e && e.error && /tiles|style/i.test(String(e.error.message))) console.warn(e.error); });

  function setBase(sat) {
    if (!state.mapReady) return;
    map.setLayoutProperty('esri-imagery', 'visibility', sat ? 'visible' : 'none');
    $('#baseSat').classList.toggle('on', sat); $('#baseStreets').classList.toggle('on', !sat);
    $('#btnLayers').classList.toggle('sat', sat);
  }
  $('#baseSat').onclick = () => setBase(true);
  $('#baseStreets').onclick = () => setBase(false);
  $('#btnLayers').onclick = () => { $('#layerPanel').hidden = !$('#layerPanel').hidden; };

  function firstVectorId() {
    for (const e of state.layers.values()) if (e.d.kind !== 'raster' && e.ids.length) return e.ids[0];
    return state.firstSymbol;
  }
  const IS_POLY = ['in', ['geometry-type'], ['literal', ['Polygon', 'MultiPolygon']]];
  const IS_LINE = ['in', ['geometry-type'], ['literal', ['LineString', 'MultiLineString']]];
  const IS_POINT = ['in', ['geometry-type'], ['literal', ['Point', 'MultiPoint']]];

  async function addLayer(d) {
    if (!state.mapReady) { state.pending.push(d); return null; }
    if (state.layers.has(d.id)) return state.layers.get(d.id);
    if (d.style === 'scope') for (const [id, e] of state.layers) if (e.d.style === 'scope') removeLayer(id);
    const color = d.style === 'scope' ? '#2b4f8a' : d.style === 'boundary' ? '#1d2433' : d.style === 'footprint' ? '#d97706' : PALETTE[state.colorIdx++ % PALETTE.length];
    const src = 'src-' + d.id; const entry = { d, color, ids: [], visible: true, fc: null };
    state.layers.set(d.id, entry);
    try {
      if (d.kind === 'raster') {
        const opts = { type: 'raster', tiles: d.tiles, tileSize: 256, minzoom: d.minzoom || 0, maxzoom: Math.min(d.maxzoom || 18, 20), attribution: d.source || '' };
        if (d.bbox && d.bbox.length === 4) opts.bounds = d.bbox;
        map.addSource(src, opts);
        map.addLayer({ id: 'L-' + d.id, type: 'raster', source: src, paint: { 'raster-opacity': 0.92 } }, firstVectorId());
        entry.ids.push('L-' + d.id);
      } else {
        const fc = d.data || await fetch(d.url).then((r) => r.json());
        if (state.layers.get(d.id) !== entry) return null; // removed (or new conversation) while loading
        entry.fc = fc;
        map.addSource(src, { type: 'geojson', data: fc });
        const before = state.firstSymbol;
        if (d.style === 'scope') {
          map.addLayer({ id: 'F-' + d.id, type: 'fill', source: src, paint: { 'fill-color': color, 'fill-opacity': 0.04 } }, before);
          map.addLayer({ id: 'S-' + d.id, type: 'line', source: src, paint: { 'line-color': color, 'line-width': 2, 'line-dasharray': [2, 1.5] } }, before);
          entry.ids.push('F-' + d.id, 'S-' + d.id);
        } else {
          const fillOp = d.style === 'boundary' ? 0.02 : d.style === 'footprint' ? 0.05 : 0.33;
          map.addLayer({ id: 'F-' + d.id, type: 'fill', source: src, filter: IS_POLY, paint: { 'fill-color': color, 'fill-opacity': fillOp } }, before);
          map.addLayer({ id: 'S-' + d.id, type: 'line', source: src, filter: ['any', IS_POLY, IS_LINE],
            paint: { 'line-color': color, 'line-width': ['case', IS_LINE, 2.2, d.style === 'boundary' ? 1.2 : 1.1], 'line-opacity': 0.9 } }, before);
          map.addLayer({ id: 'P-' + d.id, type: 'circle', source: src, filter: IS_POINT,
            paint: { 'circle-radius': 4.5, 'circle-color': color, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.3 } });
          entry.ids.push('F-' + d.id, 'S-' + d.id, 'P-' + d.id);
          ['F-', 'P-', 'S-'].forEach((p) => map.on('click', p + d.id, (e) => popup(e, d)));
          ['F-', 'P-'].forEach((p) => { map.on('mouseenter', p + d.id, () => { map.getCanvas().style.cursor = 'pointer'; }); map.on('mouseleave', p + d.id, () => { map.getCanvas().style.cursor = ''; }); });
        }
      }
    } catch (err) {
      console.warn('layer failed', d, err); state.layers.delete(d.id); toast('Could not draw layer: ' + (d.title || d.id)); return null;
    }
    renderLayerList();
    return entry;
  }
  function popup(e, d) {
    const f = e.features && e.features[0]; if (!f) return;
    const rows = Object.entries(f.properties || {}).slice(0, 14).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(String(v).slice(0, 120))}</td></tr>`).join('');
    new maplibregl.Popup({ maxWidth: '320px' }).setLngLat(e.lngLat).setHTML(`<b>${esc(d.title)}</b><table>${rows || '<tr><td>No attributes</td></tr>'}</table>`).addTo(map);
  }
  function removeLayer(id) {
    const e = state.layers.get(id); if (!e) return;
    e.ids.forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
    if (map.getSource('src-' + id)) map.removeSource('src-' + id);
    state.layers.delete(id); renderLayerList();
  }
  function toggleLayer(id) {
    const e = state.layers.get(id); if (!e) return; e.visible = !e.visible;
    e.ids.forEach((l) => map.getLayer(l) && map.setLayoutProperty(l, 'visibility', e.visible ? 'visible' : 'none'));
    renderLayerList();
  }
  function renderLayerList() {
    const list = $('#layerList'); const n = state.layers.size;
    $('#nLayers').textContent = n ? `${n}` : '';
    if (!n) { list.innerHTML = '<div class="muted small">Layers that Y-finder adds appear here.</div>'; return; }
    list.innerHTML = [...state.layers.values()].reverse().map((e) => {
      const d = e.d; const dl = d.kind === 'geojson' ? `<a class="btn" style="padding:0 6px" href="${esc(d.url)}" download="${esc((d.title || 'layer').replace(/[^\w-]+/g, '_'))}.geojson" title="Download GeoJSON">&#8595;</a>` : '';
      return `<div class="lrow" data-id="${esc(d.id)}"><span class="sw" style="background:${e.color};opacity:${e.visible ? 1 : .3}"></span>
        <span class="nm" title="${esc(d.title)} · ${esc(d.source || '')}">${esc(d.title)}${d.n ? ` <span class="muted">(${d.n})</span>` : ''}</span>
        <button data-act="zoom" title="Zoom to">Zoom</button><button data-act="vis">${e.visible ? 'Hide' : 'Show'}</button>${dl}<button data-act="rm" title="Remove">&times;</button></div>`;
    }).join('');
    $$('.lrow', list).forEach((row) => {
      const id = row.dataset.id;
      row.querySelector('[data-act=zoom]').onclick = () => fit(state.layers.get(id)?.d.bbox);
      row.querySelector('[data-act=vis]').onclick = () => toggleLayer(id);
      row.querySelector('[data-act=rm]').onclick = () => removeLayer(id);
    });
    if (state.gisMode) loadGeoLibre();
  }
  function thumbSVG(fc, color) {
    const feats = (fc && fc.features) || []; if (!feats.length) return '';
    let xs = [], ys = [];
    const walk = (c, fn) => { if (typeof c[0] === 'number') fn(c); else c.forEach((x) => walk(x, fn)); };
    feats.slice(0, 400).forEach((f) => f.geometry && walk(f.geometry.coordinates, (p) => { xs.push(p[0]); ys.push(p[1]); }));
    if (!xs.length) return '';
    const w0 = Math.min(...xs), w1 = Math.max(...xs), s0 = Math.min(...ys), s1 = Math.max(...ys);
    const kx = Math.cos(((s0 + s1) / 2) * Math.PI / 180); const W = 64, H = 48, pad = 4;
    const sc = Math.min((W - 2 * pad) / Math.max((w1 - w0) * kx, 1e-9), (H - 2 * pad) / Math.max(s1 - s0, 1e-9));
    const X = (x) => (pad + (x - w0) * kx * sc + ((W - 2 * pad) - (w1 - w0) * kx * sc) / 2).toFixed(1);
    const Y = (y) => (H - pad - (y - s0) * sc - ((H - 2 * pad) - (s1 - s0) * sc) / 2).toFixed(1);
    let path = '', dots = '';
    const ring = (r) => { const step = Math.max(1, Math.floor(r.length / 60)); path += 'M' + r.filter((_, i) => i % step === 0).map((p) => X(p[0]) + ',' + Y(p[1])).join('L'); };
    feats.slice(0, 400).forEach((f) => {
      const g = f.geometry; if (!g) return;
      if (g.type === 'Polygon') ring(g.coordinates[0]); else if (g.type === 'MultiPolygon') g.coordinates.forEach((p) => ring(p[0]));
      else if (g.type === 'LineString') ring(g.coordinates); else if (g.type === 'MultiLineString') g.coordinates.forEach(ring);
      else if (g.type === 'Point') dots += `<circle cx="${X(g.coordinates[0])}" cy="${Y(g.coordinates[1])}" r="1.4" fill="${color}"/>`;
    });
    return `<svg class="thumb-svg" viewBox="0 0 ${W} ${H}"><path d="${path}" fill="${color}" fill-opacity=".18" stroke="${color}" stroke-width="1"/>${dots}</svg>`;
  }

  // ------------------------------------------------------------------ chat
  const msgs = $('#msgs');
  function scrollChat() { const t = $('#tab-chat'); t.scrollTop = t.scrollHeight; }
  function userMsg(text) {
    $('#welcome').hidden = true;
    const d = document.createElement('div'); d.className = 'msg user'; d.textContent = text; msgs.appendChild(d); scrollChat();
  }
  function botMsg() {
    const el = document.createElement('div'); el.className = 'msg bot'; msgs.appendChild(el);
    const m = { el, last: null, chips: {}, text: null, raw: '', tools: 0, start: Date.now() };
    m.thinking = document.createElement('div'); m.thinking.className = 'thinking'; m.thinking.innerHTML = '<i></i><i></i><i></i>'; el.appendChild(m.thinking);
    m.seg = (cls) => { const s = document.createElement('div'); s.className = cls; el.insertBefore(s, m.thinking); m.last = s; return s; };
    m.addText = (delta) => {
      if (!m.last || !m.last.classList.contains('md')) { m.text = m.seg('md'); m.raw = ''; }
      m.raw += delta; const target = m.text; const raw = m.raw;
      cancelAnimationFrame(m._raf); m._raf = requestAnimationFrame(() => { target.innerHTML = md(raw); scrollChat(); });
    };
    m.chip = (ev) => {
      let c = m.chips[ev.id];
      if (!c) {
        let act = m.last && m.last.classList.contains('activity') ? m.last : null;
        if (!act) { act = m.seg('activity'); }
        c = document.createElement('div'); c.className = 'chip'; act.appendChild(c); m.chips[ev.id] = c; m.tools++;
      }
      c.className = 'chip ' + ev.state;
      c.innerHTML = `<span class="st"></span><span class="tx">${esc(ev.label)}${ev.summary ? ` <span class="sum">· ${esc(ev.summary)}</span>` : ''}</span>`;
      m.lastChip = c; scrollChat();
    };
    m.card = (html) => { const s = m.seg('inline'); s.innerHTML = html; scrollChat(); return s; };
    return m;
  }

  // ------------------------------------------------------------------ progress stepper (tri-projection)
  const STEPS = ['frame', 'scope', 'data', 'map'];
  function step(name, status) {
    const li = $(`#stepper li[data-step="${name}"]`); if (!li) return;
    li.classList.remove('active', 'done'); if (status) li.classList.add(status);
    if (status === 'done') { const nxt = STEPS[STEPS.indexOf(name) + 1]; const n = nxt && $(`#stepper li[data-step="${nxt}"]`); if (n && !n.classList.contains('done')) n.classList.add('active'); }
  }
  $$('#stepper li').forEach((li) => { li.onclick = () => {
    if (li.dataset.tab === 'map') { document.body.classList.add('collapsed'); return; }
    switchTab(li.dataset.tab); if (li.dataset.step === 'scope') setTimeout(() => $('#scopeCard').scrollIntoView({ behavior: 'smooth' }), 50);
  }; });

  async function send(text) {
    text = (text || '').trim(); if (!text || state.busy) return;
    state.busy = true; $('#btnSend').disabled = true; switchTab('chat');
    if (!state.graph) step('frame', 'active');
    userMsg(text); $('#q').value = ''; autosize();
    const m = botMsg();
    try {
      const r = await fetch('api/chat', { method: 'POST', headers: headers(), body: JSON.stringify({ sid: state.sid, message: text, model: state.model }) });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        if (r.status === 401) { askCode(); }
        throw new Error(j.error || `Server error ${r.status}`);
      }
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          chunk.split('\n').forEach((line) => { if (line.startsWith('data: ')) { try { handle(JSON.parse(line.slice(6)), m); } catch (err) { console.warn(err); } } });
        }
      }
    } catch (err) {
      m.seg('md').innerHTML = `<p style="color:var(--poor)">${esc(err.message || err)}</p>`;
    } finally {
      m.thinking.remove(); state.busy = false; $('#btnSend').disabled = false; scrollChat();
    }
  }

  function handle(ev, m) {
    switch (ev.type) {
      case 'session': state.sid = ev.sid; store.set('yf_sid', ev.sid); break;
      case 'text': m.addText(ev.delta); break;
      case 'tool': m.chip(ev); break;
      case 'results': if (m.lastChip && ev.items && ev.items.length) {
        const det = document.createElement('details');
        det.innerHTML = `<summary>${ev.items.length} found</summary><ul>${ev.items.slice(0, 10).map((x) => `<li>${safeUrl(x.url) ? `<a href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)} <span class="muted">${esc(x.meta || '')}</span></li>`).join('')}</ul>`;
        m.lastChip.querySelector('.tx').appendChild(det);
      } break;
      case 'choices': m.choices = ev; break;
      case 'graph': state.graph = ev.graph; renderGraph(); step('frame', 'done'); if (!m.graphCard) {
        const core = ev.graph.nodes.filter((n) => n.core).length;
        m.graphCard = m.card(`<div class="inline-card"><div class="ic-body"><div class="ic-title">Influence graph · ${ev.graph.nodes.length} factors (${core} core)</div>
          <div class="ic-sub">${esc(ev.graph.question || '')}</div></div><button data-go="frame">Open</button></div>`);
        m.graphCard.querySelector('[data-go]').onclick = () => switchTab('frame');
      } break;
      case 'scope': state.scope = ev.scope; renderScope(); step('scope', 'done'); {
        const s = ev.scope; const a = new Set(s.assumed || []);
        const tag = (k) => a.has(k) ? '<span class="assumed">default</span>' : '';
        const c = m.card(`<div class="inline-card"><div class="ic-body"><div class="ic-title">Scope · ${esc(s.extent_name || 'Study area')}</div>
          <div class="ic-sub">${esc(s.time_start)} to ${esc(s.time_end)}${tag('time')} · ${s.spatial_resolution_m ? esc(s.spatial_resolution_m) + ' m detail' : 'reporting units'}${tag('resolution')} · ${esc(s.unit_of_analysis || '')}${tag('unit')}</div></div><button data-go="frame">Edit</button></div>`);
        c.querySelector('[data-go]').onclick = () => { switchTab('frame'); $('#scopeCard').scrollIntoView({ behavior: 'smooth' }); };
      } break;
      case 'layer': addLayer(ev.layer).then((e) => {
        if (!e || ev.layer.style === 'scope') return;
        if (state.picks.length || ev.layer.style !== 'boundary') step('map', 'done');
        const c = m.card(`<div class="inline-card">${e.fc ? thumbSVG(e.fc, e.color) : ''}<div class="ic-body"><div class="ic-title">On the map: ${esc(ev.layer.title)}</div>
          <div class="ic-sub">${ev.layer.n ? esc(ev.layer.n) + ' features · ' : ''}${esc(ev.layer.source || '')}${ev.layer.date ? ' · ' + esc(ev.layer.date) : ''}</div></div><button>Zoom</button></div>`);
        c.querySelector('button').onclick = () => fit(ev.layer.bbox);
      }); break;
      case 'zoom': fit(ev.bbox); break;
      case 'recommendations': state.picks = ev.picks || []; state.methods = ev.methods || []; state.nextSteps = ev.next_steps || []; renderData(); step('data', 'done'); {
        const top = state.picks.slice(-8);
        const c = m.card(`<div class="inline-card"><div class="ic-body"><div class="ic-title">${top.length} datasets checked against your scope</div>
          <ul class="mini-picks">${top.map((p) => `<li><span class="fitdot ${esc(p.fit.overall)}"></span>${esc(p.title)} <span class="muted">· ${esc(fitLabel[p.fit.overall])}</span></li>`).join('')}</ul></div><button data-go="data">Details</button></div>`);
        c.querySelector('[data-go]').onclick = () => switchTab('data');
      } break;
      case 'handbook': { const c = m.card(`<div class="inline-card"><div class="ic-body"><div class="ic-title">Access handbook</div><div class="ic-sub">${esc(ev.title)}</div></div><button>Open</button></div>`); c.querySelector('button').onclick = () => openHandbook(ev.ref, ev.title); } break;
      case 'citations': {
        const s = m.seg('cites'); s.innerHTML = 'Sources: ' + ev.items.slice(0, 8).map((c) => safeUrl(c.url) ? `<a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener">${esc((c.title || c.url).slice(0, 60))}</a>` : '').join('');
      } break;
      case 'error': m.seg('md').innerHTML = `<p style="color:var(--poor)">${esc(ev.message)}</p>`; break;
      case 'done': {
        $$('.activity', m.el).forEach((a) => {
          const n = a.children.length; if (n < 3) return;
          const det = document.createElement('details'); det.innerHTML = `<summary class="muted small">${n} steps</summary>`;
          a.parentNode.insertBefore(det, a); det.appendChild(a);
        });
        if (m.choices && m.choices.options && m.choices.options.length) {
          const box = m.seg('choices');
          m.choices.options.forEach((o) => { const b = document.createElement('button'); b.textContent = o; b.onclick = () => { box.classList.add('used'); send(o); }; box.appendChild(b); });
          const other = document.createElement('button'); other.textContent = 'Something else...'; other.onclick = () => { $('#q').focus(); }; box.appendChild(other);
        }
        const u = ev.usage || {}; const s = m.seg('meta-line');
        s.textContent = `${ev.model} · ${ev.elapsed}s · ${u.tools || 0} tool calls`;
      } break;
      default: break;
    }
  }

  // ------------------------------------------------------------------ frame tab
  function renderGraph() {
    const g = state.graph; const has = !!(g && g.nodes && g.nodes.length);
    $('#graphEmpty').hidden = has; $('#graph').hidden = !has; $('#graphLegend').hidden = !has; $('#frameActions').hidden = !has;
    if (!has) return;
    $('#btnGraphml').href = `api/session/${encodeURIComponent(state.sid)}/graph.graphml`;
    const els = g.nodes.map((n) => ({ data: { id: n.id, label: n.label, cov: n.data_coverage || 'uncovered', role: n.role, core: n.core ? 1 : 0 } }))
      .concat(g.edges.map((e, i) => ({ data: { id: 'e' + i, source: e.source, target: e.target, note: e.note } })));
    const draw = () => {
      if (state.cy) state.cy.destroy();
      state.cy = cytoscape({
        container: $('#graph'), elements: els, wheelSensitivity: 0.25, minZoom: 0.3, maxZoom: 2.5,
        style: [
          { selector: 'node', style: { label: 'data(label)', 'font-size': 10, 'text-wrap': 'wrap', 'text-max-width': 92, 'text-valign': 'bottom', 'text-margin-y': 4, color: '#1d2433', width: 20, height: 20, 'background-color': '#cfd5df', 'border-width': 0 } },
          { selector: 'node[cov = "covered"]', style: { 'background-color': '#2e9e57' } },
          { selector: 'node[cov = "partial"]', style: { 'background-color': '#e0a030' } },
          { selector: 'node[core = 1]', style: { 'border-width': 3, 'border-color': '#1d2433' } },
          { selector: 'node[role = "outcome"]', style: { shape: 'diamond', width: 30, height: 30, 'background-color': '#1f3a68', 'font-weight': 700 } },
          { selector: 'node:selected', style: { 'overlay-color': '#2b4f8a', 'overlay-opacity': 0.18 } },
          { selector: 'edge', style: { width: 1.2, 'line-color': '#aab2c0', 'target-arrow-color': '#aab2c0', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.8 } },
        ],
        // Outcome in the center, core factors on the inner ring, the rest outside: readable in a narrow panel.
        layout: { name: 'concentric', concentric: (n) => (n.data('role') === 'outcome' ? 3 : n.data('core') ? 2 : 1),
          levelWidth: () => 1, minNodeSpacing: 34, padding: 18, avoidOverlap: true, startAngle: -Math.PI / 2 },
      });
      state.cy.on('tap', 'node', (e) => showNode(e.target.id()));
      state.cy.on('tap', 'edge', (e) => { const d = e.target.data(); $('#nodeInfo').hidden = false; $('#nodeInfo').innerHTML = `<h4>${esc(d.source)} → ${esc(d.target)}</h4>${esc(d.note || '')}`; });
    };
    if ($('#tab-frame').classList.contains('on')) draw(); else state.drawGraph = draw;
  }
  function showNode(id) {
    const n = state.graph.nodes.find((x) => x.id === id); if (!n) return;
    const ps = state.picks.filter((p) => (p.factors || []).includes(id));
    $('#nodeInfo').hidden = false;
    $('#nodeInfo').innerHTML = `<h4>${esc(n.label)} <span class="muted small">${esc(n.role)}${n.core ? ' · core' : ''}</span></h4>
      <div><span class="muted">Observe:</span> ${esc(n.observable || '')}</div>
      <div style="margin-top:4px">${ps.length ? ps.map((p) => `<div><span class="fitdot ${esc(p.fit.overall)}" style="display:inline-block"></span> ${esc(p.title)}</div>`).join('') : '<span class="muted">No dataset linked yet. Ask Y-finder to search for it.</span>'}</div>`;
  }
  function renderScope() {
    const s = state.scope; const el = $('#scopeCard'); el.hidden = !s; if (!s) return;
    const a = new Set(s.assumed || []); const tag = (k) => a.has(k) ? '<span class="assumed" title="Y-finder chose this default; edit it if it is wrong">default</span>' : '';
    el.innerHTML = `<h4>Scope <button class="btn primary" id="scopeSave" type="button">Update fit</button></h4>
      <div class="scope-grid">
        <label>Area${tag('extent')}</label><div>${esc(s.extent_name || '')} <a href="#" id="scopeZoom" class="small">zoom</a></div>
        <label for="sc_t0">From${tag('time')}</label><input id="sc_t0" value="${esc(s.time_start || '')}" placeholder="YYYY or YYYY-MM-DD">
        <label for="sc_t1">To</label><input id="sc_t1" value="${esc(s.time_end || '')}" placeholder="YYYY or YYYY-MM-DD">
        <label for="sc_res">Detail (m)${tag('resolution')}</label><input id="sc_res" type="number" min="0" step="any" value="${esc(s.spatial_resolution_m ?? '')}" placeholder="blank = reporting units">
        <label for="sc_unit">Unit${tag('unit')}</label><input id="sc_unit" value="${esc(s.unit_of_analysis || '')}">
        <label>Frequency${tag('frequency')}</label><div>${esc(s.temporal_frequency || '')}</div>
      </div>${s.rationale ? `<div class="muted small" style="margin-top:6px">${esc(s.rationale)}</div>` : ''}`;
    $('#scopeZoom').onclick = (e) => { e.preventDefault(); fit(s.bbox); };
    $('#scopeSave').onclick = async () => {
      try {
        const r = await fetch(`api/session/${encodeURIComponent(state.sid)}/scope`, { method: 'POST', headers: headers(), body: JSON.stringify({
          time_start: $('#sc_t0').value, time_end: $('#sc_t1').value, spatial_resolution_m: $('#sc_res').value, unit_of_analysis: $('#sc_unit').value }) });
        const j = await r.json(); if (!r.ok) throw new Error(j.error);
        state.scope = j.scope; state.picks = j.picks; state.graph = j.graph; renderScope(); renderData(); renderGraph(); toast('Scope updated. Fit labels re-checked.');
      } catch (err) { toast(err.message); }
    };
  }

  // ------------------------------------------------------------------ data tab
  const verifiedText = { catalog: 'From the YCGS catalog', search: 'From a live search', web: 'From a web source', unverified: 'Not verified: not found by a search' };
  const yearOf = (s) => { const m = String(s ?? '').match(/(\d{4})(?:-(\d{2}))?/); return m ? +m[1] + ((+m[2] || 1) - 1) / 12 : null; };
  function timeBar(p) {
    const s0 = yearOf(state.scope?.time_start), s1 = yearOf(state.scope?.time_end); const d0 = yearOf(p.t0);
    if (s0 == null || d0 == null) return '';
    const now = new Date().getFullYear() + 0.99; const d1 = p.t1 != null ? yearOf(p.t1) + 0.99 : Math.max(now, (s1 ?? s0) + 0.99);
    const e1 = (s1 ?? s0) + (String(state.scope.time_end || '').length === 4 ? 0.99 : 0.08);
    const lo = Math.min(d0, s0) - 0.5, hi = Math.max(d1, e1) + 0.5; const X = (v) => (((v - lo) / (hi - lo)) * 96 + 2).toFixed(2);
    const ongoing = p.t1 == null;
    return `<svg class="timebar" viewBox="0 0 100 22" preserveAspectRatio="none" aria-label="Data years versus your study window">
      <rect x="${X(d0)}" y="6" width="${Math.max(0.6, X(d1) - X(d0))}" height="6" rx="2" fill="#9db4d6"/>${ongoing ? `<rect x="${X(now)}" y="6" width="${Math.max(0, X(d1) - X(now))}" height="6" fill="#9db4d6" opacity=".35"/>` : ''}
      <rect x="${X(s0)}" y="2.5" width="${Math.max(0.8, X(e1) - X(s0))}" height="13" rx="2" fill="none" stroke="#00356b" stroke-width="1.2" vector-effect="non-scaling-stroke"/>
      <text x="${X(d0)}" y="21" font-size="5.5" fill="#6b7486">${Math.floor(d0)}</text><text x="${X(d1)}" y="21" font-size="5.5" fill="#6b7486" text-anchor="end">${ongoing ? 'ongoing' : Math.floor(d1)}</text></svg>`;
  }
  function showCoverage(p) {
    if (!state.mapReady || !p.bbox) { toast('Coverage for this source is not listed.'); return; }
    const [w, s, e, n] = p.bbox; const global = e - w >= 300;
    const fc = { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } }] };
    if (map.getSource('coverage')) map.getSource('coverage').setData(fc);
    else { map.addSource('coverage', { type: 'geojson', data: fc }); map.addLayer({ id: 'coverage', type: 'line', source: 'coverage', paint: { 'line-color': '#db2777', 'line-width': 2.5, 'line-dasharray': [1, 1] } }); }
    if (global) { toast(`${p.title}: global coverage`); return; }
    const sb = state.scope?.bbox; const b = sb ? [Math.min(w, sb[0]), Math.min(s, sb[1]), Math.max(e, sb[2]), Math.max(n, sb[3])] : p.bbox;
    fit(b); toast(`Pink box: area covered by ${p.title}`);
  }
  function renderMatrix(ps) {
    if (!ps.length) { $('#matrix').innerHTML = ''; return; }
    const cell = (x) => `<td title="${esc(x?.reason || '')}"><span class="fitdot ${esc(x?.label || 'unknown')}"></span></td>`;
    $('#matrix').innerHTML = `<table class="matrix"><thead><tr><th>Dataset</th><th>Area</th><th>Time</th><th>Detail</th><th>Overall</th><th>Access</th></tr></thead><tbody>${
      ps.map((p) => `<tr><td data-ref="${esc(p.ref)}" title="${esc(p.title)}">${esc(p.title)}</td>${cell(p.fit.extent)}${cell(p.fit.time)}${cell(p.fit.resolution)}${cell({ label: p.fit.overall, reason: fitLabel[p.fit.overall] })}<td>${badge(p.fit.access?.label || 'unknown', accessLabel[p.fit.access?.label] || '', p.fit.access?.reason)}</td></tr>`).join('')}</tbody></table>`;
    $$('#matrix td[data-ref]').forEach((td) => { td.onclick = () => {
      const c = $$('.dcard').find((x) => x.dataset.ref === td.dataset.ref);
      if (c) { c.scrollIntoView({ behavior: 'smooth', block: 'center' }); c.animate([{ boxShadow: '0 0 0 3px #286dc0' }, { boxShadow: 'none' }], { duration: 1400 }); }
    }; });
  }
  function renderData() {
    const ps = state.picks || []; const hide = $('#hidePoor').checked;
    const order = { core: 0, supporting: 1, alternative: 2 };
    renderMatrix(ps.slice().sort((a, b) => (order[a.role] ?? 1) - (order[b.role] ?? 1)));
    $('#dataEmpty').hidden = ps.length > 0; $('#hidePoorWrap').hidden = !ps.length;
    $('#nData').hidden = !ps.length; $('#nData').textContent = ps.length;
    const groups = [['core', 'Core data'], ['supporting', 'Supporting data'], ['alternative', 'Alternatives']];
    const nodeLabel = (id) => (state.graph?.nodes.find((n) => n.id === id) || {}).label || id;
    let html = '';
    groups.forEach(([role, name]) => {
      const g = ps.filter((p) => (p.role || 'supporting') === role && !(hide && p.fit.overall === 'poor')); if (!g.length) return;
      html += `<div class="group-h">${esc(name)}</div>`;
      g.forEach((p) => {
        const reasons = ['extent', 'time', 'resolution'].map((k) => p.fit[k]).filter((x) => x && x.label !== 'unknown').map((x) => `<li>${esc(x.reason)}</li>`).join('');
        const url = safeUrl(p.url);
        html += `<div class="dcard ${p.fit.overall === 'poor' ? 'poorfit' : ''}" data-ref="${esc(p.ref)}">
          <div class="t"><span class="fitdot ${esc(p.fit.overall)}"></span>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}</div>
          <div class="src">${esc([p.source, p.provider].filter(Boolean).join(' · '))}${p.resolution ? ' · ' + esc(p.resolution) : ''}${p.time ? ' · ' + esc(p.time) : ''}</div>
          <div class="badges">${fitBadges(p.fit)}</div>
          ${timeBar(p)}
          ${reasons ? `<ul class="reasons">${reasons}</ul>` : ''}
          ${p.why ? `<div class="why">${esc(p.why)}</div>` : ''}
          ${p.limitations ? `<div class="lim"><b>Limits:</b> ${esc(p.limitations)}</div>` : ''}
          ${p.yale_note ? `<div class="yale-note">${esc(p.yale_note)}</div>` : ''}
          ${p.factors && p.factors.length ? `<div class="factors">${p.factors.map((f) => `<span>${esc(nodeLabel(f))}</span>`).join('')}</div>` : ''}
          <div class="row"><button class="btn" data-act="hb">How to access</button><button class="btn" data-act="cov">Coverage</button>${p.stac_collection ? '<button class="btn" data-act="preview">Preview imagery</button>' : ''}<button class="btn" data-act="ask">Ask</button>
            <span class="verified ${p.verified === 'unverified' ? 'warn' : ''}">${esc(verifiedText[p.verified] || '')}${p.metadata_from_model ? ' · some details from the model' : ''}</span></div></div>`;
      });
    });
    $('#picks').innerHTML = html;
    $$('.dcard').forEach((c) => {
      const p = ps.find((x) => x.ref === c.dataset.ref); if (!p) return;
      c.querySelector('[data-act=hb]').onclick = () => openHandbook(p.ref, p.title);
      c.querySelector('[data-act=cov]').onclick = () => showCoverage(p);
      const pv = c.querySelector('[data-act=preview]'); if (pv) pv.onclick = () => send(`Show a recent preview of ${p.title} (${p.stac_collection}) for my study area on the map.`);
      c.querySelector('[data-act=ask]').onclick = () => { $('#q').value = `About ${p.title}: `; switchTab('chat'); $('#q').focus(); autosize(); };
    });
    const ms = state.methods || [];
    $('#methods').innerHTML = (ms.length ? '<div class="group-h">Methods and tools</div>' + ms.map((x) => `<div class="mcard"><b>${esc(x.name)}</b>${esc(x.why)}<div class="tools">${esc(x.tools)}</div></div>`).join('') : '') +
      ((state.nextSteps || []).length ? '<div class="group-h">Next steps</div><ol class="small">' + state.nextSteps.map((s) => `<li>${esc(s)}</li>`).join('') + '</ol>' : '') +
      (ps.length ? `<div class="mcard" style="border-left-color:var(--good)"><b>Keep looking for me</b>Y-finder can search again on a schedule for new or better-fitting sources and post short updates to a private link.
        <div style="margin-top:6px"><button class="btn primary" id="btnWatch">Set up follow-up searches</button></div></div>
        <div class="mcard" style="border-left-color:#d4a017"><b>Talk to a YCGS consultant</b>Hand this conversation to a person: Y-finder writes a one-page summary of the question, scope, data found and open issues.
        <div style="margin-top:6px"><button class="btn" id="btnConsult">Prepare consultation summary</button></div></div>` : '');
    const bw = $('#btnWatch'); if (bw) bw.onclick = watchDialog;
    const bc = $('#btnConsult'); if (bc) bc.onclick = consultDialog;
  }
  $('#hidePoor').onchange = renderData;

  // ------------------------------------------------------------------ hand-off to a YCGS consultant (human in the loop)
  function consultSummary() {
    const g = state.graph || {}; const s = state.scope || {}; const ps = state.picks || [];
    const lab = (p) => fitLabel[p.fit.overall] || p.fit.overall;
    const gaps = (g.nodes || []).filter((n) => n.core && n.data_coverage !== 'covered').map((n) => n.label);
    const assumed = (s.assumed || []).join(', ');
    return [`Consultation request (prepared with Y-finder)`, ``,
      `Question: ${g.question || '(see conversation)'}`,
      `Scope: ${s.extent_name || '?'}; ${s.time_start || '?'} to ${s.time_end || '?'}; detail ${s.spatial_resolution_m ? s.spatial_resolution_m + ' m' : 'reporting units'}; unit ${s.unit_of_analysis || '?'}${assumed ? ` (defaults not yet confirmed: ${assumed})` : ''}`,
      ``, `Data found so far:`, ...ps.map((p) => `- ${p.title} [${lab(p)}; ${p.fit.access?.reason || p.access || ''}] ${p.url || ''}`),
      ``, `Core factors still without good data: ${gaps.join(', ') || 'none'}`,
      `Methods suggested: ${(state.methods || []).map((m) => m.name).join('; ') || 'none yet'}`,
      ``, `What I need help with: (write one or two sentences)`].join('\n');
  }
  function consultDialog() {
    $('#dlgTitle').textContent = 'Talk to a YCGS consultant';
    $('#dlgBody').innerHTML = `<p>Copy this summary into the YCGS service request form, or bring it to office hours. Edit it first; nothing is sent automatically.</p>
      <textarea id="consultText" style="width:100%;height:280px;font:12.5px/1.45 ui-monospace,Consolas,monospace;border:1px solid var(--line);border-radius:8px;padding:8px"></textarea>
      <p><button class="btn primary" id="consultCopy">Copy summary</button> <a class="btn" href="https://geospatial.yale.edu/request-services" target="_blank" rel="noopener">Open the YCGS request form</a></p>`;
    $('#consultText').value = consultSummary();
    $('#consultCopy').onclick = async () => { try { await navigator.clipboard.writeText($('#consultText').value); toast('Copied.'); } catch { $('#consultText').select(); toast('Press Ctrl+C to copy.'); } };
    $('#dlg').showModal();
  }

  // ------------------------------------------------------------------ follow-up watches (opt-in)
  function watchDialog() {
    const c = state.config || {};
    $('#dlgTitle').textContent = 'Keep looking for me';
    $('#dlgBody').innerHTML = `<p>Y-finder will re-run the search for <b>${esc(state.graph?.question || 'your question')}</b> within your current scope, look for new releases and better-fitting sources, and write a short note each round.</p>
      <div class="scope-grid" style="grid-template-columns:120px 1fr;margin:10px 0">
        <label for="w_cad">How often</label><select id="w_cad"><option value="7">Every week</option><option value="14">Every two weeks</option></select>
        <label for="w_rounds">For</label><select id="w_rounds"><option value="2">2 rounds</option><option value="4" selected>4 rounds</option><option value="8">8 rounds</option></select>
        ${c.watch_email ? '<label for="w_email">Email (optional)</label><input id="w_email" type="email" placeholder="you@yale.edu">' : ''}
      </div>
      <label class="toggle" style="align-items:flex-start"><input type="checkbox" id="w_ok"> <span>${esc(c.watch_consent || 'I agree that Y-finder stores my question and scope and searches again on this schedule.')}</span></label>
      <p class="small muted">${c.watch_email ? '' : 'Updates appear on a private link and an Atom feed. '}You can stop or delete the watch at any time from that link.</p>
      <button class="btn primary" id="w_go" disabled>Start</button> <span id="w_msg" class="small"></span>`;
    $('#w_ok').onchange = () => { $('#w_go').disabled = !$('#w_ok').checked; };
    $('#w_go').onclick = async () => {
      try {
        const r = await fetch(`api/session/${encodeURIComponent(state.sid)}/watch`, { method: 'POST', headers: headers(), body: JSON.stringify({
          consent: $('#w_ok').checked, cadence_days: +$('#w_cad').value, rounds: +$('#w_rounds').value, email: $('#w_email') ? $('#w_email').value : null }) });
        const j = await r.json(); if (!r.ok) throw new Error(j.error);
        const link = new URL(j.link, location.href).href; const feed = new URL(j.feed, location.href).href;
        $('#dlgBody').innerHTML = `<p>Done. The first follow-up runs on <b>${esc(j.watch.next_run.slice(0, 10))}</b>.</p>
          <p>Your private link (keep it; anyone with it can see the updates):<br><a href="${esc(link)}" target="_blank" rel="noopener">${esc(link)}</a></p>
          <p class="small">Feed for a reader app: <a href="${esc(feed)}" target="_blank" rel="noopener">Atom feed</a></p>`;
      } catch (err) { $('#w_msg').textContent = err.message; $('#w_msg').style.color = 'var(--poor)'; }
    };
    $('#dlg').showModal();
  }
  async function watchView(token) {
    $('#dlgTitle').textContent = 'Follow-up searches';
    const render = (w) => {
      const ups = (w.updates || []).slice().reverse();
      $('#dlgBody').innerHTML = `<p><b>${esc(w.question)}</b></p>
        <p class="small muted">Status: ${esc(w.status)} · ${esc(w.rounds_left)} round(s) left · next run ${esc((w.next_run || '').slice(0, 10))} · consent given ${esc((w.consent_at || '').slice(0, 10))}</p>
        <div class="row" style="display:flex;gap:6px;margin-bottom:10px">${w.status === 'active' ? '<button class="btn primary" id="wv_run">Run a round now</button><button class="btn" id="wv_stop">Stop</button>' : ''}<button class="btn" id="wv_del">Delete my data</button></div>
        ${ups.length ? ups.map((u) => `<div class="mcard"><b>${esc(u.at.slice(0, 10))}</b>${md(u.summary)}${u.new.length ? '<div class="small"><b>New sources:</b><ul>' + u.new.map((n) => `<li>${safeUrl(n.url) ? `<a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)} <span class="badge ${esc(n.fit?.overall || 'unknown')}">${esc(fitLabel[n.fit?.overall] || '')}</span></li>`).join('') + '</ul></div>' : ''}</div>`).join('') : '<p class="muted">No rounds have run yet.</p>'}`;
      const run = $('#wv_run'); if (run) run.onclick = async () => { run.disabled = true; run.textContent = 'Searching (about a minute)...'; try { const r = await fetch(`api/watch/${token}/run`, { method: 'POST', headers: headers() }); const j = await r.json(); if (!r.ok) throw new Error(j.error); render(j.watch); } catch (e) { toast(e.message); run.disabled = false; run.textContent = 'Run a round now'; } };
      const stop = $('#wv_stop'); if (stop) stop.onclick = async () => { const r = await fetch(`api/watch/${token}/stop`, { method: 'POST', headers: headers() }); render((await r.json()).watch); };
      $('#wv_del').onclick = async () => { if (!confirm('Delete this watch and all its updates? This cannot be undone.')) return; await fetch(`api/watch/${token}/delete`, { method: 'POST', headers: headers() }); $('#dlgBody').innerHTML = '<p>Deleted.</p>'; };
    };
    try { const j = await getJSON(`api/watch/${encodeURIComponent(token)}`); render(j.watch); $('#dlg').showModal(); }
    catch (err) { toast('Follow-up link not found or deleted.'); }
  }
  const wtok = new URLSearchParams(location.search).get('watch'); if (wtok) watchView(wtok);

  // ------------------------------------------------------------------ handbooks
  async function openHandbook(ref, title) {
    const dlg = $('#dlg'); $('#dlgTitle').textContent = 'How to access: ' + (title || ref); $('#dlgBody').innerHTML = '<p class="muted">Loading...</p>'; dlg.showModal();
    try {
      const j = await getJSON('api/handbook?ref=' + encodeURIComponent(ref));
      showHandbook(j.handbook);
    } catch (err) {
      const can = err.body && err.body.can_generate;
      const p = state.picks.find((x) => x.ref === ref);
      $('#dlgBody').innerHTML = `<p>No access handbook for this source yet.</p>${p && safeUrl(p.url) ? `<p>Start at the source: <a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">${esc(p.url)}</a></p>` : ''}
        ${can ? '<p><button class="btn primary" id="hbGen">Write a handbook now</button> <span class="muted small">Uses AI with web search, about 30 to 60 seconds. It is saved for everyone as a draft.</span></p>' : '<p class="muted small">Handbooks are written in batches when compute is available (scripts/write_handbooks.py).</p>'}`;
      const b = $('#hbGen'); if (b) b.onclick = async () => {
        b.disabled = true; b.textContent = 'Writing...';
        try { const j = await getJSON(`api/handbook?generate=1&ref=${encodeURIComponent(ref)}&sid=${encodeURIComponent(state.sid)}`); showHandbook(j.handbook); }
        catch (e2) { $('#dlgBody').insertAdjacentHTML('beforeend', `<p style="color:var(--poor)">${esc(e2.message)}</p>`); b.disabled = false; b.textContent = 'Try again'; }
      };
    }
  }
  function showHandbook(hb) {
    const st = hb.meta.status || 'draft';
    $('#dlgBody').innerHTML = `<p class="small muted">${esc(st === 'reviewed' ? 'Reviewed handbook' : 'Draft handbook written by ' + (hb.meta.generated_by || 'AI') + ' on ' + (hb.meta.generated_on || '') + '. Test the example before relying on it.')}
      · <a href="api/handbook.md?ref=${encodeURIComponent(hb.ref)}" download>SKILL.md</a> · <a href="api/handbook.toml?ref=${encodeURIComponent(hb.ref)}" download>LLM-Find TOML</a></p>${md(hb.markdown)}`;
  }
  $('#dlgClose').onclick = () => $('#dlg').close();

  // ------------------------------------------------------------------ catalog tab
  let catT = null;
  async function catSearch() {
    const q = $('#catQ').value.trim();
    if (!q) { $('#catRes').innerHTML = ''; $('#catMeta').textContent = state.config ? `${state.config.catalog.counts?.total || ''} cards: YCGS curated, Planetary Computer and Earth Engine. Built ${state.config.catalog.built}.` : ''; return; }
    try {
      const j = await getJSON(`api/catalog/search?q=${encodeURIComponent(q)}&kind=${state.catKind}&access=${state.catAccess}&sid=${encodeURIComponent(state.sid)}&limit=30`);
      $('#catMeta').textContent = `${j.results.length} results${state.scope ? ' · fit labels use your current scope' : ''}`;
      $('#catRes').innerHTML = j.results.map((c) => `<div class="ccard" data-id="${esc(c.id)}"><div class="t">${safeUrl(c.url) ? `<a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener">${esc(c.title)}</a>` : esc(c.title)}</div>
        <div class="src">${esc([c.kind !== 'dataset' ? c.kind : '', c.source, c.provider, c.resolution, c.time].filter(Boolean).join(' · '))}</div>
        <div class="d">${esc(c.description || '')}</div>
        <div class="row">${c.fit ? fitBadges(c.fit) : badge(c.access_class || 'unknown', accessLabel[c.access_class] || c.access || '', c.access)}<button class="btn" data-act="ask">Ask Y-finder</button><button class="btn" data-act="hb">How to access</button></div></div>`).join('') || '<div class="empty">No matches. Try other words.</div>';
      $$('.ccard').forEach((el) => {
        const c = j.results.find((x) => x.id === el.dataset.id);
        el.querySelector('[data-act=ask]').onclick = () => send(`Does "${c.title}" (${c.id}) fit my study? If there is no study yet, tell me what it is good for.`);
        el.querySelector('[data-act=hb]').onclick = () => openHandbook(c.id, c.title);
      });
    } catch (err) { $('#catRes').innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
  }
  $('#catQ').oninput = () => { clearTimeout(catT); catT = setTimeout(catSearch, 250); };
  $$('#catThemes button').forEach((b) => { b.onclick = () => { $('#catQ').value = b.dataset.q; catSearch(); }; });
  $$('#catChips button').forEach((b) => { b.onclick = () => { $$('#catChips button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); state.catKind = b.dataset.kind; state.catAccess = b.dataset.access; catSearch(); }; });

  // ------------------------------------------------------------------ GeoLibre (full GIS)
  function geolibreURL() {
    const p = new URLSearchParams(); let skipped = 0;
    for (const e of state.layers.values()) {
      if (!e.visible) continue;
      if (e.d.kind === 'geojson') p.append('data', new URL(e.d.url, location.href).href); else skipped++;
    }
    if (!state.gisFull) { p.set('layout', 'compact'); p.set('panels', 'collapsed'); }
    p.set('welcome', '0');
    return { url: 'https://web.geolibre.app/?' + p.toString(), skipped };
  }
  function loadGeoLibre() {
    const { url, skipped } = geolibreURL(); const f = $('#geolibre');
    if (f.dataset.src !== url) { f.src = url; f.dataset.src = url; }
    const local = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
    $('#gisNote').textContent = local ? 'Running locally: GeoLibre cannot fetch layers from this computer. Download them from Layers and drop them in.'
      : skipped ? `${skipped} imagery preview(s) stay in Map view.` : (state.layers.size ? 'Your layers are loaded.' : 'Ask Y-finder for data first, or explore GeoLibre.');
  }
  function setMode(gis) {
    state.gisMode = gis; $('#modeGIS').classList.toggle('on', gis); $('#modeMap').classList.toggle('on', !gis);
    $('#geolibre').hidden = !gis; $('#gisBar').hidden = !gis; $('.layers-box').hidden = gis;
    if (gis) loadGeoLibre();
  }
  $('#modeMap').onclick = () => setMode(false);
  $('#modeGIS').onclick = () => setMode(true);
  $('#gisSimple').onclick = () => { state.gisFull = false; $('#gisSimple').classList.add('on'); $('#gisFull').classList.remove('on'); loadGeoLibre(); };
  $('#gisFull').onclick = () => { state.gisFull = true; $('#gisFull').classList.add('on'); $('#gisSimple').classList.remove('on'); loadGeoLibre(); };

  // ------------------------------------------------------------------ misc UI
  function switchTab(name) {
    $$('.tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
    $$('.tab').forEach((t) => t.classList.toggle('on', t.id === 'tab-' + name));
    if (name === 'frame' && state.drawGraph) { const d = state.drawGraph; state.drawGraph = null; d(); }
    if (name === 'frame' && state.cy) state.cy.resize();
    if (name === 'catalog') catSearch();
  }
  $$('.tabs button').forEach((b) => { b.onclick = () => switchTab(b.dataset.tab); });
  function autosize() { const q = $('#q'); q.style.height = 'auto'; q.style.height = Math.min(q.scrollHeight, 140) + 'px'; }
  $('#q').addEventListener('input', autosize);
  $('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($('#q').value); } });
  $('#ask').onsubmit = (e) => { e.preventDefault(); send($('#q').value); };
  $('#btnCollapse').onclick = () => { const c = document.body.classList.toggle('collapsed'); $('#btnCollapse').setAttribute('aria-expanded', String(!c)); };
  $('#btnWide').onclick = () => {
    const w = document.body.classList.toggle('wide'); $('#btnWide').setAttribute('aria-pressed', String(w));
    setTimeout(() => { if (state.cy) { state.cy.resize(); state.cy.fit(undefined, 18); } }, 60);
  };
  $('#q').addEventListener('focus', () => { if (document.body.classList.contains('collapsed')) document.body.classList.remove('collapsed'); });

  $('#btnAttach').onclick = () => $('#file').click();
  $('#file').onchange = async () => {
    const f = $('#file').files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f); fd.append('sid', state.sid);
    $('#attachInfo').hidden = false; $('#attachInfo').textContent = 'Reading ' + f.name + '...';
    try {
      const r = await fetch('api/upload', { method: 'POST', body: fd, headers: state.code ? { 'X-Access-Code': state.code } : {} });
      const j = await r.json(); if (!r.ok) throw new Error(j.error);
      state.sid = j.sid; store.set('yf_sid', j.sid);
      $('#attachInfo').innerHTML = `Attached <b>${esc(j.name)}</b> (${j.words.toLocaleString()} words). It is read with your next message. <button type="button" id="attachX">remove</button>`;
      $('#attachX').onclick = () => {
        fetch(`api/session/${encodeURIComponent(state.sid)}/proposal/clear`, { method: 'POST', headers: headers() }).catch(() => {});
        $('#attachInfo').hidden = true;
      };
      if (!$('#q').value.trim()) { $('#q').value = 'Here is my proposal. Help me find data for it.'; autosize(); }
    } catch (err) { $('#attachInfo').textContent = err.message; }
    $('#file').value = '';
  };

  $('#btnNew').onclick = () => { if (state.demo) state.demo.stop = true; if (!state.busy) newConversation(); };
  function newConversation() {
    if (state.sid) fetch(`api/session/${encodeURIComponent(state.sid)}/reset`, { method: 'POST', headers: headers() }).catch(() => {});
    state.sid = ''; store.set('yf_sid', '');
    [...state.layers.keys()].forEach(removeLayer);
    Object.assign(state, { graph: null, scope: null, picks: [], methods: [], nextSteps: [] });
    if (state.cy) { state.cy.destroy(); state.cy = null; }
    msgs.innerHTML = ''; $('#welcome').hidden = false; $('#nodeInfo').hidden = true; $('#attachInfo').hidden = true;
    STEPS.forEach((s) => step(s, null));
    if (map.getSource('coverage')) map.getSource('coverage').setData({ type: 'FeatureCollection', features: [] });
    if (state.gisMode) setMode(false);
    renderGraph(); renderScope(); renderData(); switchTab('chat');
  }

  // ------------------------------------------------------------------ demo cases (recorded runs, replayed, then live)
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  async function loadDemos() {
    try {
      const j = await getJSON('static/demos/index.json'); if (!j.cases || !j.cases.length) return;
      $('#demos').innerHTML = j.cases.map((c) => `<button class="demo-card" data-id="${esc(c.id)}"><div class="field">${esc(c.field)}</div>
        <div class="title">${esc(c.title)}</div><div class="who">${esc(c.who)}</div><div class="shows">${c.shows.map((x) => `<span>${esc(x)}</span>`).join('')}</div></button>`).join('');
      $$('.demo-card').forEach((b) => { b.onclick = () => playDemo(b.dataset.id); });
      $('#demoWrap').hidden = false;
    } catch { /* no recorded demos on this server */ }
  }
  function demoNote(text) {
    if (!text) return;
    const d = document.createElement('div'); d.className = 'demo-note'; d.innerHTML = `<b>Demo note.</b> ${esc(text)}`;
    msgs.appendChild(d); scrollChat(); toast(text, Math.max(2500, 5200 / state.demo.speed));
  }
  async function pace(ev) {
    const sp = state.demo.speed;
    const ms = ev.type === 'text' ? Math.min(ev.delta.length * 7, 160) : ev.type === 'tool' ? (ev.state === 'start' ? 420 : 220)
      : ['graph', 'scope', 'recommendations', 'layer'].includes(ev.type) ? 650 : 60;
    if (sp < 50) await wait(ms / sp);
  }
  async function playDemo(id) {
    if (state.busy) return;
    let demo;
    try { demo = await getJSON(`static/demos/${encodeURIComponent(id)}.json`); } catch { toast('Demo not found.'); return; }
    newConversation();
    state.busy = true; $('#btnSend').disabled = true; $('#welcome').hidden = true;
    state.demo = { stop: false, speed: +(store.get('yf_demo_speed') || 2) };
    const head = document.createElement('div'); head.className = 'demo-head';
    head.innerHTML = `<div class="field">Demo case · ${esc(demo.field)}</div><h3>${esc(demo.title)}</h3><div class="who">${esc(demo.who)}</div>
      <ul>${demo.shows.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      <div class="demo-ctl">Recorded ${esc(demo.recorded)} with ${esc(demo.model)} · Speed
        <span class="seg">${[1, 2, 4, 100].map((v) => `<button data-sp="${v}" class="${v === state.demo.speed ? 'on' : ''}">${v === 100 ? 'Skip' : v + 'x'}</button>`).join('')}</span>
        <button class="btn" id="demoStop">Stop</button></div>`;
    msgs.appendChild(head);
    $$('[data-sp]', head).forEach((b) => { b.onclick = () => { if (!state.demo) return; state.demo.speed = +b.dataset.sp; store.set('yf_demo_speed', b.dataset.sp); $$('[data-sp]', head).forEach((x) => x.classList.toggle('on', x === b)); }; });
    $('#demoStop', head).onclick = () => { if (state.demo) state.demo.stop = true; };
    let load = { layers: {} };
    try {
      const r = await fetch(`api/demo/${encodeURIComponent(id)}/load`, { method: 'POST', headers: headers() });
      if (r.ok) { load = await r.json(); state.sid = load.sid; store.set('yf_sid', load.sid); }
    } catch { /* replay still works with inline layers */ }
    const sub = (ev) => {
      if (ev.type !== 'layer' || ev.layer.kind !== 'geojson') return ev;
      const nd = load.layers && load.layers[ev.layer.id];
      return { ...ev, layer: nd ? { ...nd } : { ...ev.layer, data: demo.layers[ev.layer.id] } };
    };
    try {
      for (const st of demo.steps) {
        if (state.demo.stop) break;
        const sp = () => state.demo.speed;
        if (st.type === 'upload') {
          $('#attachInfo').hidden = false; $('#attachInfo').innerHTML = `Attached <b>${esc(st.name)}</b> (${esc(st.words)} words).`;
          demoNote(`${demo.who.split(' (')[0]} attaches a proposal (${st.words} words). Y-finder reads it with the next message.`); await wait(2200 / sp());
        } else if (st.type === 'turn') {
          switchTab('chat'); userMsg(st.message); await wait(500 / sp());
          const m = botMsg();
          for (const ev of st.events) { if (state.demo.stop) break; await pace(ev); if (ev.type === 'done') ev.model = 'recorded · ' + (ev.model || ''); handle(sub(ev), m); }
          m.thinking.remove(); $('#attachInfo').hidden = true; await wait(900 / sp());
        } else if (st.type === 'ui') {
          const hold = Math.max(1200, 5600 / sp());
          if (st.action === 'tab') { switchTab(st.tab); demoNote(st.note); await wait(hold); }
          else if (st.action === 'scope_edit') {
            switchTab('frame'); await wait(300); $('#scopeCard').scrollIntoView({ behavior: 'smooth' });
            Object.entries(st.set).forEach(([k, v]) => { const inp = { time_start: '#sc_t0', time_end: '#sc_t1', spatial_resolution_m: '#sc_res', unit_of_analysis: '#sc_unit' }[k]; if (inp && $(inp)) { $(inp).value = v; $(inp).animate([{ background: '#fff4c2' }, { background: '#fff' }], { duration: 1800 }); } });
            demoNote(st.note); await wait(hold / 2);
            const quiet = { graphCard: true, card(html) { const d = document.createElement('div'); d.innerHTML = html; return d; }, chip() {}, addText() {}, seg() { return document.createElement('div'); } };
            for (const ev of st.events) handle(ev, quiet);
            await wait(hold / 2); switchTab('data'); await wait(hold / 2);
          } else if (st.action === 'handbook') { demoNote(st.note); openHandbook(st.ref, st.title); await wait(hold * 1.3); $('#dlg').close(); }
          else if (st.action === 'gis') { demoNote(st.note); setMode(true); await wait(hold * 1.4); setMode(false); }
          else if (st.action === 'map') { demoNote(st.note); document.body.classList.add('collapsed'); await wait(hold); document.body.classList.remove('collapsed'); }
          else if (st.action === 'watch') { demoNote(st.note); watchDialog(); await wait(hold); $('#dlg').close(); }
        }
      }
    } finally {
      const end = document.createElement('div'); end.className = 'demo-end';
      end.textContent = state.demo.stop ? 'Demo stopped. You can keep asking from here.' : 'Demo finished. This conversation is now live: ask a follow-up, click a quick reply, or edit the scope in the Frame tab.';
      msgs.appendChild(end); scrollChat();
      state.demo = null; state.busy = false; $('#btnSend').disabled = false;
    }
  }
  loadDemos();
  $('#btnAbout').onclick = () => {
    $('#dlgTitle').textContent = 'About Y-finder';
    $('#dlgBody').innerHTML = md(`**Y-finder** is a prototype geospatial data guide, built by Huan Ning as a proposal for the Yale Center for Geospatial Solutions (YCGS). It is not an official Yale or YCGS service.

**How it works (tri-projection).** Y-finder turns a research question into an *influence graph* of factors, agrees a *scope* with you (area, time window, resolution, unit of analysis), then searches for *data* and labels how each source fits that scope. The fit labels come from catalog metadata and simple rules, not from the language model.

**Where the data comes from.** ${state.config ? state.config.catalog.counts?.total : '1,100+'} catalog cards (YCGS curated cards with Yale access routes, Microsoft Planetary Computer and Google Earth Engine), plus live searches of Yale Dataverse, Yale Library GeoBlacklight, ArcGIS Online, Data.gov, Planetary Computer STAC and the web.

**Map and GIS.** Layers you see can be downloaded as GeoJSON, or opened in [GeoLibre](https://web.geolibre.app/) with *Full GIS*. Influence graphs export to [Research Architect](https://app.researcharchitect.ai/).

**Access handbooks.** Each data source can have a handbook in the style of an Agent Skill: access route, endpoint, a Python example and pitfalls. It extends the handbooks of [LLM-Find](https://github.com/gladcolor/llm-find).

**Privacy.** Do not paste protected or confidential data. Questions are sent to an OpenAI model to generate answers.`);
    $('#dlg').showModal();
  };
  function askCode() {
    const c = prompt('This Y-finder server needs an access code:'); if (c) { state.code = c; store.set('yf_code', c); }
  }

  // examples
  $('#examples').innerHTML = EXAMPLES.map(([who, q], i) => `<button class="example" data-i="${i}"><b>${esc(who)}</b>${esc(q)}</button>`).join('');
  $$('.example').forEach((b) => { b.onclick = () => send(EXAMPLES[+b.dataset.i][1]); });

  // config
  getJSON('api/config').then((c) => {
    state.config = c;
    const sel = $('#model');
    sel.innerHTML = c.models.map((m) => `<option value="${esc(m.id)}" title="${esc(m.note)}">${esc(m.name)}</option>`).join('');
    const saved = store.get('yf_model'); state.model = c.models.some((m) => m.id === saved) ? saved : c.default_model; sel.value = state.model;
    sel.onchange = () => { state.model = sel.value; store.set('yf_model', sel.value); };
    $('#catQ').placeholder = `Search ${c.catalog.counts?.total || ''} datasets, tools and methods`;
    if (c.access_code && !state.code) askCode();
  }).catch(() => toast('Could not reach the Y-finder server.'));

  // restore an open session after a page reload
  if (state.sid) getJSON(`api/session/${encodeURIComponent(state.sid)}`).then((s) => {
    if (!s.turns) return;
    state.graph = s.graph; state.scope = s.scope && Object.keys(s.scope).length ? s.scope : null; state.picks = s.picks || []; state.methods = s.methods || [];
    renderGraph(); renderScope(); renderData(); (s.layers || []).forEach((l) => addLayer(l));
    $('#welcome').hidden = true; msgs.innerHTML = '<div class="muted small">Restored your last session. Earlier messages are not shown.</div>';
  }).catch(() => { state.sid = ''; store.set('yf_sid', ''); });

  window.YF = { state, send, map }; // for debugging and browser tests
})();
