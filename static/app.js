/* Y-finder front end: map, chat (server-sent events), frame graph, data cards, catalog, GeoLibre hand-off. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = (u) => { try { const x = new URL(u, location.href); return /^https?:$/.test(x.protocol) ? x.href : ''; } catch { return ''; } };
  const store = { get(k) { try { return sessionStorage.getItem(k); } catch { return null; } }, set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } } };
  // localStorage keeps conversations across visits (this browser only)
  const local = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); return true; } catch { return false; } } };

  const PALETTE = ['#2563eb', '#db2777', '#059669', '#7c3aed', '#ea580c', '#0891b2', '#65a30d', '#be123c'];
  // Short starters; the demo cases cover the longer stories.
  const EXAMPLES = [
    ['Tree canopy and heat', 'How has tree canopy changed in New Haven neighborhoods since 2012, and is canopy loss linked to summer heat?'],
    ['Polling place access', 'How long do people travel to their polling places in Connecticut, and does it differ by income?'],
    ['Rooftop solar', 'Which Yale campus buildings in New Haven have the best rooftop solar potential?'],
  ];

  const state = {
    sid: store.get('yf_sid') || '', model: '', busy: false, code: store.get('yf_code') || '',
    layers: new Map(), colorIdx: 0, mapReady: false, pending: [],
    graph: null, scope: null, picks: [], methods: [], nextSteps: [], cy: null, config: null,
    gisMode: false, gisFull: true, catKind: 'any', catAccess: 'any', conv: null, restoring: null,
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
    // desktop: the map container already starts right of the panel (app.css), so only a margin is needed
    return mobile ? { top: 70, left: 30, right: 30, bottom: collapsed ? 160 : innerHeight * 0.62 + 20 }
      : { top: 80, right: 50, bottom: 50, left: 50 };
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
    if (p.length && state.scope && state.scope.bbox) fit(state.scope.bbox);
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

  // GeoLibre's own colors for a gallery layer (server-validated, checked again here): graduated stops are class
  // lower bounds (a step), categorized stops exact values (a match), as in GeoLibre's vectorColorExpression.
  const HEXC = /^#[0-9a-f]{3,8}$/i;
  function glColor(P, fallback) {
    if (!P) return fallback;
    const base = HEXC.test(P.fill || '') ? P.fill : fallback;
    const stops = (Array.isArray(P.stops) ? P.stops : []).filter((s) => Array.isArray(s) && HEXC.test(s[1] || ''));
    if (typeof P.property !== 'string' || !P.property) return base;
    if (P.mode === 'graduated') {
      const st = stops.filter((s) => Number.isFinite(s[0])).sort((a, b) => a[0] - b[0]).filter((s, i, a) => !i || s[0] > a[i - 1][0]);
      if (st.length >= 2) return ['step', ['to-number', ['get', P.property], st[0][0]], st[0][1], ...st.slice(1).flatMap((s) => [s[0], s[1]])];
    } else if (P.mode === 'categorized') {
      const seen = new Set(); const st = stops.filter((s) => { const k = String(s[0]); if (seen.has(k)) return false; seen.add(k); return true; });
      if (st.length) return ['match', ['to-string', ['get', P.property]], ...st.flatMap((s) => [String(s[0]), s[1]]), base];
    }
    return base;
  }
  function swatchOf(P, fallback) {
    if (!P) return fallback;
    const st = (P.stops || []).filter((s) => HEXC.test((s || [])[1] || ''));
    return st.length ? st[Math.floor(st.length * 0.7)][1] : HEXC.test(P.fill || '') ? P.fill : fallback;
  }

  async function addLayer(d) {
    if (!state.mapReady) { state.pending.push(d); return null; }
    if (state.layers.has(d.id)) return state.layers.get(d.id);
    // the same data drawn twice (same name, source and feature count) stays one entry
    const twin = d.kind === 'geojson' && d.style !== 'scope' && [...state.layers.values()].find((e) => e.d.kind === 'geojson' && e.d.title === d.title && e.d.source === d.source && e.d.n === d.n);
    if (twin) return twin;
    if (d.style === 'scope') for (const [id, e] of state.layers) if (e.d.style === 'scope') removeLayer(id);
    let color = d.style === 'scope' ? '#2b4f8a' : d.style === 'boundary' ? '#1d2433' : d.style === 'footprint' ? '#d97706' : PALETTE[state.colorIdx++ % PALETTE.length];
    if (d.paint) color = swatchOf(d.paint, color);
    const src = 'src-' + d.id; const entry = { d, color, ids: [], srcs: [], visible: true, fc: null };
    state.layers.set(d.id, entry);
    try {
      if (d.kind === 'raster') { // same-date scenes arrive as parts of one entry
        const parts = [{ tiles: d.tiles, bbox: d.bbox }].concat(Array.isArray(d.parts) ? d.parts : []);
        parts.forEach((p, k) => {
          if (!Array.isArray(p.tiles) || !p.tiles.length || !p.tiles.every((t) => /^https:\/\//.test(t))) return;
          const sid = k ? `${src}-${k}` : src, lid = k ? `L-${d.id}-${k}` : 'L-' + d.id;
          const opts = { type: 'raster', tiles: p.tiles, tileSize: 256, minzoom: d.minzoom || 0, maxzoom: Math.min(d.maxzoom || 18, 20), attribution: d.source || '' };
          if (p.bbox && p.bbox.length === 4) opts.bounds = p.bbox;
          map.addSource(sid, opts); entry.srcs.push(sid);
          map.addLayer({ id: lid, type: 'raster', source: sid, paint: { 'raster-opacity': 0.92 } }, firstVectorId());
          entry.ids.push(lid);
        });
      } else {
        const fc = d.data || await fetch(d.url).then((r) => {
          if (r.status === 404) throw new Error(d.session_only || d.style === 'download' ? 'gone' : 'missing');
          return r.json();
        });
        if (state.layers.get(d.id) !== entry) return null; // removed (or new conversation) while loading
        entry.fc = fc;
        map.addSource(src, { type: 'geojson', data: fc }); entry.srcs.push(src);
        const before = state.firstSymbol;
        if (d.style === 'scope') {
          map.addLayer({ id: 'F-' + d.id, type: 'fill', source: src, paint: { 'fill-color': color, 'fill-opacity': 0.04 } }, before);
          map.addLayer({ id: 'S-' + d.id, type: 'line', source: src, paint: { 'line-color': color, 'line-width': 2, 'line-dasharray': [2, 1.5] } }, before);
          entry.ids.push('F-' + d.id, 'S-' + d.id);
        } else if (d.paint) { // a GeoLibre gallery layer, in GeoLibre's colors
          const P = d.paint; const c = glColor(P, color); const edge = HEXC.test(P.stroke || '') ? P.stroke : '#ffffff';
          const op = Math.min(0.8, Number.isFinite(P.opacity) ? P.opacity : 0.7);
          map.addLayer({ id: 'F-' + d.id, type: 'fill', source: src, filter: IS_POLY, paint: { 'fill-color': c, 'fill-opacity': op } }, before);
          map.addLayer({ id: 'S-' + d.id, type: 'line', source: src, filter: ['any', IS_POLY, IS_LINE],
            paint: { 'line-color': ['case', IS_LINE, c, edge], 'line-width': ['case', IS_LINE, 1.8, Math.max(0.3, Math.min(2, P.width || 0.5))], 'line-opacity': 0.9 } }, before);
          map.addLayer({ id: 'P-' + d.id, type: 'circle', source: src, filter: IS_POINT,
            paint: { 'circle-radius': Math.max(2.5, Math.min(7, (P.radius || 6) * 0.7)), 'circle-color': c, 'circle-opacity': 0.9, 'circle-stroke-color': edge, 'circle-stroke-width': 0.6 } });
          entry.ids.push('F-' + d.id, 'S-' + d.id, 'P-' + d.id);
          ['F-', 'P-'].forEach((p) => { map.on('mouseenter', p + d.id, () => { map.getCanvas().style.cursor = 'pointer'; }); map.on('mouseleave', p + d.id, () => { map.getCanvas().style.cursor = ''; }); });
        } else {
          const fillOp = d.style === 'boundary' ? 0.02 : d.style === 'footprint' ? 0 : 0.33;  // footprints: outline only
          map.addLayer({ id: 'F-' + d.id, type: 'fill', source: src, filter: IS_POLY, paint: { 'fill-color': color, 'fill-opacity': fillOp } }, before);
          map.addLayer({ id: 'S-' + d.id, type: 'line', source: src, filter: ['any', IS_POLY, IS_LINE],
            paint: { 'line-color': color, 'line-width': ['case', IS_LINE, 2.2, d.style === 'boundary' ? 1.2 : 1.1], 'line-opacity': 0.9 } }, before);
          map.addLayer({ id: 'P-' + d.id, type: 'circle', source: src, filter: IS_POINT,
            paint: { 'circle-radius': 4.5, 'circle-color': color, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.3 } });
          entry.ids.push('F-' + d.id, 'S-' + d.id, 'P-' + d.id);
          ['F-', 'P-'].forEach((p) => { map.on('mouseenter', p + d.id, () => { map.getCanvas().style.cursor = 'pointer'; }); map.on('mouseleave', p + d.id, () => { map.getCanvas().style.cursor = ''; }); });
        }
      }
    } catch (err) {
      console.warn('layer failed', d, err); state.layers.delete(d.id);
      if (err.message === 'gone' || err.message === 'missing') {
        state.goneN = (state.goneN || 0) + 1; clearTimeout(state.goneT);
        state.goneT = setTimeout(() => { toast(`${state.goneN} layer${state.goneN > 1 ? 's were' : ' was'} kept only for an earlier session and ${state.goneN > 1 ? 'have' : 'has'} been deleted.`); state.goneN = 0; }, 600);
      } else toast('Could not draw layer: ' + (d.title || d.id));
      return null;
    }
    if (d.hidden) { // hidden at first in the original GeoLibre map; Show in Layers turns it on
      entry.visible = false; entry.ids.forEach((l) => map.getLayer(l) && map.setLayoutProperty(l, 'visibility', 'none'));
    }
    renderLayerList();
    return entry;
  }
  // One popup per click: every Y-finder layer under the pointer, top-most first, with arrows to step through them
  // (one handler per layer used to open a popup each, stacked on top of one another).
  const HIDE_FIELD = /^(objectid|fid|oid|shape.*|mtfcc|funcstat|lsadc|lsad|aland|awater|globalid|intptlat|intptlon|centlat|centlon|basename|stgeometry.*)$/i;
  function fmtVal(v) {
    if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString() : (Math.abs(v) < 1000 ? +v.toFixed(3) : Math.round(v).toLocaleString()).toString();
    return String(v).slice(0, 140);
  }
  function popupHTML(hits, i) {
    const { entry, f } = hits[i]; const props = Object.entries(f.properties || {}).filter(([k, v]) => !HIDE_FIELD.test(k) && v !== null && v !== '');
    const rows = props.slice(0, 16).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td>${esc(fmtVal(v))}</td></tr>`).join('');
    const nav = hits.length > 1 ? `<span class="pp-nav"><button data-pp="-1" aria-label="Previous layer">&#8249;</button>${i + 1} of ${hits.length}<button data-pp="1" aria-label="Next layer">&#8250;</button></span>` : '';
    return `<div class="pp-head"><span class="sw" style="background:${entry.color}"></span><b>${esc(entry.d.title)}</b>${nav}</div>
      <table>${rows || '<tr><td>No attributes</td></tr>'}</table>${props.length > 16 ? `<div class="muted small">${props.length - 16} more fields</div>` : ''}`;
  }
  map.on('click', (e) => {
    const owner = {};
    for (const entry of state.layers.values()) if (entry.visible && entry.d.style !== 'scope') entry.ids.forEach((id) => { if (/^[FPS]-/.test(id) && map.getLayer(id)) owner[id] = entry; });
    const ids = Object.keys(owner); if (!ids.length) return;
    const seen = new Set(); const hits = [];
    for (const f of map.queryRenderedFeatures(e.point, { layers: ids })) { // already ordered top-most first
      const entry = owner[f.layer.id]; if (!entry || seen.has(entry.d.id)) continue;
      seen.add(entry.d.id); hits.push({ entry, f });
    }
    if (!hits.length) return;
    if (state.popup) state.popup.remove();
    let i = 0; const pp = state.popup = new maplibregl.Popup({ maxWidth: '340px', className: 'yf-pop' }).setLngLat(e.lngLat).setHTML(popupHTML(hits, i)).addTo(map);
    const wire = () => $$('[data-pp]', pp.getElement()).forEach((b) => { b.onclick = (ev) => {
      ev.stopPropagation(); i = (i + Number(b.dataset.pp) + hits.length) % hits.length; pp.setHTML(popupHTML(hits, i)); wire();
    }; });
    wire();
  });
  function removeLayer(id) {
    const e = state.layers.get(id); if (!e) return;
    e.ids.forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
    (e.srcs.length ? e.srcs : ['src-' + id]).forEach((sid) => { if (map.getSource(sid)) map.removeSource(sid); });
    state.layers.delete(id); renderLayerList();
  }
  function toggleLayer(id) {
    const e = state.layers.get(id); if (!e) return; e.visible = !e.visible;
    e.ids.forEach((l) => map.getLayer(l) && map.setLayoutProperty(l, 'visibility', e.visible ? 'visible' : 'none'));
    renderLayerList();
  }
  const ICON = {
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>',
    eyeOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M3 3l18 18M10.6 5.1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.9 8.3 2 12 2 12s3.6 7 10 7c1.6 0 3-.4 4.3-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>',
    zoom: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="7"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>',
    dl: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  };
  // ------------------------------------------------------------------ map legend: visible data layers, top-most first
  const fmtNum = (v) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString() : String(+Number(v).toFixed(1)));
  function legendItem(e) {
    const d = e.d, P = d.paint;
    const head = `<div class="ml-t"><span>${esc(d.title)}</span>${P && P.label ? `<small>${esc(P.label)}</small>` : ''}</div>`;
    if (P && P.mode === 'graduated' && Array.isArray(P.stops) && P.stops.length >= 2) {
      const st = P.stops.filter((s) => Array.isArray(s) && HEXC.test(s[1] || '') && Number.isFinite(s[0]));
      const rows = st.map((s, i) => {
        const range = i < st.length - 1 ? `${fmtNum(s[0])} – ${fmtNum(st[i + 1][0])}` : `≥ ${fmtNum(s[0])}`;
        return `<div class="ml-r"><i style="background:${s[1]}"></i>${esc(range)}</div>`;
      }).join('');
      return `<div class="ml-i">${head}${rows}</div>`;
    }
    if (P && P.mode === 'categorized' && Array.isArray(P.stops) && P.stops.length) {
      const rows = P.stops.slice(0, 12).filter((s) => HEXC.test((s || [])[1] || '')).map((s) => `<div class="ml-r"><i style="background:${s[1]}"></i>${esc(String(s[0]))}</div>`).join('');
      return `<div class="ml-i">${head}${rows}${P.stops.length > 12 ? `<div class="ml-r muted">+ ${P.stops.length - 12} more</div>` : ''}</div>`;
    }
    if (d.kind === 'raster') {
      const img = d.legend && safeUrl(d.legend) ? `<img src="${esc(safeUrl(d.legend))}" alt="Legend for ${esc(d.title)}" loading="lazy" onerror="this.remove()">` : '';
      return `<div class="ml-i"><div class="ml-r"><i class="ml-img"></i><span>${esc(d.title)}</span></div>${img}</div>`;
    }
    const geom = (e.fc && e.fc.features && e.fc.features[0] && e.fc.features[0].geometry || {}).type || '';
    const shape = /Point/.test(geom) ? 'dot' : /Line/.test(geom) ? 'line' : '';
    if (P && P.label) return `<div class="ml-i">${head}<div class="ml-r"><i class="${shape}" style="background:${e.color}"></i>one value</div></div>`;
    return `<div class="ml-i"><div class="ml-r"><i class="${shape}" style="background:${e.color}"></i><span>${esc(d.title)}</span></div></div>`;
  }
  function renderLegend() {
    const items = [...state.layers.values()].reverse().filter((e) => e.visible && !['scope', 'boundary', 'footprint'].includes(e.d.style)).slice(0, 8);
    $('#mapLegend').hidden = !items.length || state.gisMode;
    $('#mlBody').innerHTML = items.map(legendItem).join('');
  }
  $('#mlToggle').onclick = () => {
    const open = $('#mapLegend').classList.toggle('closed') === false;
    $('#mlToggle').setAttribute('aria-expanded', String(open)); local.set('yf_legend', open ? '1' : '0');
  };
  if (local.get('yf_legend') === '0') { $('#mapLegend').classList.add('closed'); $('#mlToggle').setAttribute('aria-expanded', 'false'); }

  function renderLayerList() {
    renderLegend();
    const list = $('#layerList'); const n = state.layers.size;
    $('#nLayers').textContent = n ? `${n}` : '';
    $('#nLayersBadge').textContent = n; $('#nLayersBadge').hidden = !n;
    if (!n) { list.innerHTML = '<div class="muted small">Layers that Y-finder adds appear here.</div>'; return; }
    list.innerHTML = [...state.layers.values()].reverse().map((e) => {
      const d = e.d;
      const dl = d.kind === 'geojson' ? `<a href="${esc(d.url)}" download="${esc((d.title || 'layer').replace(/[^\w-]+/g, '_'))}.geojson" title="Download GeoJSON" aria-label="Download GeoJSON">${ICON.dl}</a>` : '';
      const src = String(d.source || '').replace(/\s*\(.*?\)\s*$/, '');
      const sub = [d.date && !String(d.title).includes(d.date) ? d.date : '', d.n ? `${Number(d.n).toLocaleString()} feature${d.n === 1 ? '' : 's'}` : '', src].filter(Boolean).join(' · ');
      return `<div class="lrow${e.visible ? '' : ' off'}" data-id="${esc(d.id)}"><span class="sw" style="background:${e.color}"></span>
        <div class="lmain" title="${esc(d.title)}${d.source ? '\n' + esc(d.source) : ''}"><div class="nm">${esc(d.title)}${d.session_only ? '<span class="tag" title="Downloaded to the server for this session; deleted when it ends">session</span>' : ''}</div>${sub ? `<div class="ls">${esc(sub)}</div>` : ''}</div>
        <div class="lact"><button data-act="vis" title="${e.visible ? 'Hide' : 'Show'}" aria-label="${e.visible ? 'Hide' : 'Show'} layer">${e.visible ? ICON.eye : ICON.eyeOff}</button><button data-act="zoom" title="Zoom to layer" aria-label="Zoom to layer">${ICON.zoom}</button>${dl}<button data-act="rm" title="Remove" aria-label="Remove layer">${ICON.x}</button></div></div>`;
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
  // A bot message is: one collapsible status line, a row of result pills, the answer text, quick replies, sources.
  function botMsg() {
    const el = document.createElement('div'); el.className = 'msg bot'; msgs.appendChild(el);
    const m = { el, chips: {}, raw: '', tools: 0, pills: {}, start: Date.now() };
    el.innerHTML = `<details class="act"><summary><span class="st run"></span><span class="lbl">Thinking</span><span class="cnt"></span></summary><div class="steps"></div></details>
      <div class="strip" hidden></div><div class="md"></div><div class="tail"></div>`;
    m.act = $('.act', el); m.steps = $('.steps', el); m.strip = $('.strip', el); m.text = $('.md', el); m.tail = $('.tail', el);
    m.thinking = { remove() {} };
    m.seg = (cls) => { const s = document.createElement('div'); s.className = cls; m.tail.appendChild(s); return s; };
    m.addText = (delta) => {
      m.raw += delta; const raw = m.raw;
      cancelAnimationFrame(m._raf); m._raf = requestAnimationFrame(() => { m.text.innerHTML = md(raw); scrollChat(); });
    };
    m.chip = (ev) => {
      let c = m.chips[ev.id];
      if (!c) { c = document.createElement('div'); c.className = 'stp'; m.steps.appendChild(c); m.chips[ev.id] = c; m.tools++; }
      c.className = 'stp ' + ev.state;
      c.innerHTML = `<span class="tx">${esc(ev.label)}${ev.summary ? ` <span class="sum">· ${esc(ev.summary)}</span>` : ''}</span>`;
      m.lastChip = c;
      if (ev.state === 'start') $('.lbl', m.act).textContent = ev.label;
      $('.cnt', m.act).textContent = ` · step ${m.tools}`;
      scrollChat();
    };
    // result pills: one per kind, updated in place (graph, scope, map, data, handbook)
    m.pill = (key, html, onClick) => {
      let b = m.pills[key];
      if (!b) { b = document.createElement('button'); b.className = 'rpill'; m.strip.appendChild(b); m.pills[key] = b; m.strip.hidden = false; }
      b.innerHTML = html; b.onclick = onClick; scrollChat(); return b;
    };
    m.card = (html) => { const s = document.createElement('div'); s.innerHTML = html; return s; }; // legacy no-op for replays
    m.layers = 0;
    return m;
  }

  // ------------------------------------------------------------------ progress: the Frame and Data tabs get a check mark
  const STEPS = ['frame', 'scope', 'data', 'map'];
  function step(name, status) {
    const tab = { frame: 'frame', scope: 'frame', data: 'data' }[name]; if (!tab) return;
    $(`.tabs button[data-tab="${tab}"]`).classList.toggle('done', status === 'done');
  }

  async function send(text) {
    text = (text || '').trim(); if (!text || state.busy) return;
    state.busy = true; $('#btnSend').disabled = true; switchTab('chat');
    if (state.restoring) await state.restoring;
    if (!state.graph) step('frame', 'active');
    userMsg(text); $('#q').value = ''; autosize();
    const m = botMsg(); const turn = convTurn(text);
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
          chunk.split('\n').forEach((line) => { if (line.startsWith('data: ')) { try { const ev = JSON.parse(line.slice(6)); convRecord(turn, ev); handle(ev, m); if (ev.type === 'recommendations') m.recs = true; } catch (err) { console.warn(err); } } });
        }
      }
    } catch (err) {
      m.seg('md').innerHTML = `<p style="color:var(--poor)">${esc(err.message || err)}</p>`;
      convRecord(turn, { type: 'error', message: String(err.message || err) });
    } finally {
      m.thinking.remove(); state.busy = false; $('#btnSend').disabled = false; scrollChat(); convSave();
      if (m.recs) watchPreviews(false);
    }
  }

  function handle(ev, m) {
    switch (ev.type) {
      case 'session': state.sid = ev.sid; store.set('yf_sid', ev.sid); break;
      case 'text': m.addText(ev.delta); break;
      case 'tool': m.chip(ev); break;
      case 'results': if (m.lastChip && ev.items && ev.items.length) {
        const ul = document.createElement('ul');
        ul.innerHTML = ev.items.slice(0, 8).map((x) => `<li>${safeUrl(x.url) ? `<a href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}</li>`).join('');
        m.lastChip.appendChild(ul);
      } break;
      case 'choices': m.choices = ev; break;
      case 'graph': state.graph = ev.graph; renderGraph(); step('frame', 'done');
        if (m.pill) m.pill('graph', `<b>Graph</b> ${ev.graph.nodes.length} factors`, () => switchTab('frame')); break;
      case 'scope': state.scope = ev.scope; renderScope(); step('scope', 'done'); if (m.pill) {
        const s = ev.scope; const t0 = String(s.time_start || '').slice(0, 7), t1 = String(s.time_end || '').slice(0, 7);
        m.pill('scope', `<b>Scope</b> ${esc((s.extent_name || 'Study area').split(',')[0])} · ${esc(t0)}${t1 && t1 !== t0 ? '–' + esc(t1) : ''}${s.spatial_resolution_m ? ' · ' + esc(s.spatial_resolution_m) + ' m' : ''}`,
          () => { switchTab('frame'); setTimeout(() => $('#scopeCard').scrollIntoView({ behavior: 'smooth' }), 60); });
      } break;
      case 'layer': addLayer(ev.layer).then((e) => {
        if (!e || ev.layer.style === 'scope' || !m.pill) return;
        m.layers++;
        m.pill('map', `${e.fc ? thumbSVG(e.fc, e.color) : ''}<b>Map</b> ${m.layers} layer${m.layers > 1 ? 's' : ''}`, () => { fit(ev.layer.bbox); $('#layerPanel').hidden = false; });
      }); break;
      case 'zoom': fit(ev.bbox); break;
      case 'recommendations': state.picks = ev.picks || []; state.methods = ev.methods || []; state.nextSteps = ev.next_steps || []; renderData(); step('data', 'done');
        if (state.graph) renderGraph(); // coverage shows once datasets are known (a replay may bring the graph first)
        if (m.pill) {
        const good = state.picks.filter((p) => p.fit.overall === 'good').length;
        m.pill('data', `<b>Data</b> ${good} of ${state.picks.length} fit`, () => switchTab('data'));
      } break;
      case 'handbook': if (m.pill) m.pill('hb', '<b>Handbook</b> open', () => openHandbook(ev.ref, ev.title)); break;
      case 'citations': {
        m.cites = (m.cites || []).concat(ev.items);
        let s = $('.cites', m.tail); if (!s) s = m.seg('cites');
        s.innerHTML = `<details><summary>Sources (${m.cites.length})</summary>${m.cites.slice(0, 10).map((c) => safeUrl(c.url) ? `<a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener">${esc((c.title || c.url).slice(0, 70))}</a>` : '').join('')}</details>`;
      } break;
      case 'error': m.seg('err').innerHTML = `<p style="color:var(--poor)">${esc(ev.message)}</p>`; break;
      case 'done': {
        if (ev.response_id && state.conv) state.conv.rid = ev.response_id;
        if (m.act) {
          const st = $('.st', m.act); st.className = 'st ok';
          $('.lbl', m.act).textContent = `${m.tools} step${m.tools === 1 ? '' : 's'}`;
          $('.cnt', m.act).textContent = ` · ${ev.elapsed}s${String(ev.model || '').startsWith('recorded') ? ' · recorded' : ''}`;
        }
        if (m.choices && m.choices.options && m.choices.options.length) {
          const box = m.seg('choices');
          m.choices.options.forEach((o) => { const b = document.createElement('button'); b.textContent = o; b.onclick = () => { box.classList.add('used'); send(o); }; box.appendChild(b); });
        }
      } break;
      default: break;
    }
  }

  // ------------------------------------------------------------------ frame tab
  // Influence graph in Research Architect's style (app_v2/static/app_v2.js): node colour by graph role
  // (source green, internal sky blue, sink violet), labels below on white chips, springy edges, data
  // coverage as transparency, dagre top-down layout, hover and tap-to-focus, a pulse on the outcome.
  // One change: coverage fades the circle, not the label, so factors without data stay readable.
  try { if (window.cytoscapeDagre && window.dagre) cytoscape.use(window.cytoscapeDagre); } catch { /* already registered */ }
  try { if (window.cytoscapeCola && window.cola) cytoscape.use(window.cytoscapeCola); } catch { /* already registered */ }
  // Layouts offered in the Frame tab, as in Research Architect (applyLayout in app_v2.js), plus left-to-right layers.
  const LAYOUT_NAMES = ['dagre-tb', 'dagre-lr', 'cola', 'cose', 'breadthfirst', 'concentric', 'circle', 'grid'];
  state.layout = LAYOUT_NAMES.includes(local.get('yf_layout')) ? local.get('yf_layout') : 'dagre-tb';
  function layoutOpts(name, animate) {
    const anim = { animate, animationDuration: 300, fit: true, padding: 22 };
    const dagre = (dir) => ({ name: 'dagre', rankDir: dir, ranker: 'tight-tree', nodeSep: dir === 'LR' ? 18 : 28, rankSep: dir === 'LR' ? 90 : 64,
      edgeSep: 14, nodeDimensionsIncludeLabels: true, ...anim });
    return {
      'dagre-tb': dagre('TB'), 'dagre-lr': dagre('LR'),
      // cola with infinite: edges act like springs and the graph follows when a node is dragged
      cola: { name: 'cola', animate: true, refresh: 1, infinite: true, fit: false, nodeSpacing: 34, edgeLength: 120,
        edgeSymDiffLength: 24, maxSimulationTime: 1500, avoidOverlap: true, handleDisconnected: true, nodeDimensionsIncludeLabels: true },
      cose: { name: 'cose', idealEdgeLength: 100, nodeRepulsion: 8000, nodeDimensionsIncludeLabels: true, ...anim },
      breadthfirst: { name: 'breadthfirst', directed: true, spacingFactor: 1.25, nodeDimensionsIncludeLabels: true, ...anim },
      concentric: { name: 'concentric', concentric: (n) => (n.data('target') ? 3 : n.data('core') ? 2 : 1), levelWidth: () => 1,
        minNodeSpacing: 34, avoidOverlap: true, startAngle: -Math.PI / 2, nodeDimensionsIncludeLabels: true, ...anim },
      circle: { name: 'circle', avoidOverlap: true, nodeDimensionsIncludeLabels: true, ...anim },
      grid: { name: 'grid', avoidOverlap: true, nodeDimensionsIncludeLabels: true, ...anim },
    }[name];
  }
  function runLayout(cy, name, animate) {
    if (state.cyLive) { try { state.cyLive.stop(); } catch { /* already stopped */ } state.cyLive = null; }
    let opts = layoutOpts(name, animate) || layoutOpts('dagre-tb', animate);
    const missing = (opts.name === 'dagre' && !window.cytoscapeDagre) || (opts.name === 'cola' && !window.cytoscapeCola);
    if (missing) opts = layoutOpts(opts.name === 'cola' ? 'cose' : 'concentric', animate); // a CDN script did not load
    try {
      const l = cy.layout(opts); l.run();
      if (opts.name === 'cola' && opts.infinite) state.cyLive = l;
      else if (!opts.fit) cy.fit(undefined, 22);
    } catch (err) { console.warn('layout failed', name, err); cy.layout(layoutOpts('concentric', false)).run(); }
  }
  $('#layoutSel').value = state.layout;
  $('#layoutSel').onchange = (e) => {
    state.layout = e.target.value; local.set('yf_layout', state.layout);
    if (state.cy && !state.cy.destroyed()) runLayout(state.cy, state.layout, true);
  };
  $('#btnFitGraph').onclick = () => { if (state.cy && !state.cy.destroyed()) state.cy.animate({ fit: { padding: 22 } }, { duration: 250 }); };
  const RA_STYLE = [
    { selector: 'node', style: {
      'background-color': '#93c5fd', 'background-opacity': 0.95, label: 'data(label)', color: '#0f172a',
      'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 4, 'text-wrap': 'wrap', 'text-max-width': 110,
      'text-background-color': '#ffffff', 'text-background-opacity': 0.78, 'text-background-padding': 3, 'text-background-shape': 'roundrectangle',
      'font-size': 11, 'font-family': 'Inter, system-ui, sans-serif', 'font-weight': 600,
      width: 'mapData(len, 0, 30, 16, 34)', height: 'mapData(len, 0, 30, 16, 34)',
      'border-width': 2, 'border-color': '#fff', 'underlay-shape': 'ellipse', 'overlay-shape': 'ellipse', 'overlay-opacity': 0,
      'transition-property': 'background-color, background-opacity, border-color, border-width, opacity, overlay-opacity, overlay-padding',
      'transition-duration': '180ms', 'transition-timing-function': 'ease-out' } },
    { selector: 'node[core = 1]', style: { 'border-color': '#334155' } },
    { selector: 'node[role = "source"]', style: { 'background-color': '#6ee7b7' } },
    { selector: 'node[role = "sink"]', style: { 'background-color': '#c4b5fd', 'border-color': '#a78bfa', 'border-width': 3, 'overlay-color': '#6d28d9' } },
    { selector: 'node[?target]', style: { 'overlay-color': '#2563eb' } },
    { selector: 'edge', style: {
      'line-color': '#94a3b8', 'target-arrow-color': '#94a3b8', 'target-arrow-shape': 'triangle',
      'curve-style': 'unbundled-bezier', 'control-point-distances': [-12, 12], 'control-point-weights': [0.25, 0.75],
      width: 1.5, opacity: 0.65, 'arrow-scale': 0.95,
      'transition-property': 'line-color, target-arrow-color, width, opacity', 'transition-duration': '140ms', 'transition-timing-function': 'ease-out' } },
    // data coverage (Research Architect's opacities: covered 1, partial 0.5, uncovered 0.18)
    { selector: 'node.cov-partial', style: { 'background-opacity': 0.5, 'border-opacity': 0.6 } },
    { selector: 'node.cov-uncovered', style: { 'background-opacity': 0.18, 'border-opacity': 0.45, color: '#475569' } },
    { selector: 'edge.cov-covered-edge', style: { opacity: 0.95 } },
    { selector: 'edge.cov-partial-edge', style: { opacity: 0.66 } },
    { selector: 'edge.cov-uncovered-edge', style: { opacity: 0.42 } },
    // interaction states follow the analytical symbology, as in Research Architect
    { selector: 'node.dim', style: { opacity: 0.25, 'background-color': '#cccccc' } },
    { selector: 'edge.dim', style: { opacity: 0.15, 'line-color': '#dddddd', 'target-arrow-color': '#dddddd' } },
    { selector: 'node.dim-strong', style: { opacity: 0.15, 'background-color': '#cccccc' } },
    { selector: 'edge.dim-strong', style: { opacity: 0.10, 'line-color': '#dddddd', 'target-arrow-color': '#dddddd' } },
    { selector: 'node.is-hover', style: { 'overlay-color': '#6366f1', 'overlay-opacity': 0.12, 'overlay-padding': 4 } },
    { selector: 'node:selected, node.anchor', style: { 'overlay-color': '#e8590c', 'overlay-opacity': 0.22, 'overlay-padding': 7 } },
    { selector: 'edge.is-hover', style: { 'line-color': '#6366f1', 'target-arrow-color': '#6366f1', width: 2.5, opacity: 1 } },
    { selector: 'edge:selected, edge.anchor', style: { 'line-color': '#e8590c', 'target-arrow-color': '#e8590c', width: 4, opacity: 1 } },
  ];
  function graphElements(g, withCoverage) {
    const indeg = {}, outdeg = {};
    g.edges.forEach((e) => { outdeg[e.source] = (outdeg[e.source] || 0) + 1; indeg[e.target] = (indeg[e.target] || 0) + 1; });
    const cov = Object.fromEntries(g.nodes.map((n) => [n.id, n.data_coverage || 'uncovered']));
    const nodes = g.nodes.map((n) => {
      const i = indeg[n.id] || 0, o = outdeg[n.id] || 0;
      const role = i === 0 && o > 0 ? 'source' : o === 0 && i > 0 ? 'sink' : 'internal'; // Research Architect's tagNodeRoles
      return { data: { id: n.id, label: n.label, len: String(n.label || '').length, role, kind: n.role, core: n.core ? 1 : 0,
        target: n.role === 'outcome' || n.id === g.outcome }, classes: withCoverage ? 'cov-' + cov[n.id] : '' };
    });
    const edges = g.edges.map((e, i) => {
      const a = cov[e.source], b = cov[e.target];
      const cls = !withCoverage ? '' : a === 'uncovered' || b === 'uncovered' ? 'cov-uncovered-edge' : a === 'partial' || b === 'partial' ? 'cov-partial-edge' : 'cov-covered-edge';
      return { data: { id: 'e' + i, source: e.source, target: e.target, note: e.note }, classes: cls };
    });
    return nodes.concat(edges);
  }
  function destroyGraph() {
    clearInterval(state.cyPulse);
    if (state.cyLive) { try { state.cyLive.stop(); } catch { /* already stopped */ } state.cyLive = null; }
    if (state.cy) { state.cy.destroy(); state.cy = null; }
  }
  function renderGraph() {
    const g = state.graph; const has = !!(g && g.nodes && g.nodes.length);
    $('#graphEmpty').hidden = has; $('#graph').hidden = !has; $('#graphLegend').hidden = !has; $('#frameActions').hidden = !has; $('#graphBar').hidden = !has;
    if (!has) return;
    $('#btnGraphml').href = `api/session/${encodeURIComponent(state.sid)}/graph.graphml`;
    const withCov = (state.picks || []).length > 0; // coverage is shown once datasets have been checked
    const els = graphElements(g, withCov);
    $('#covLegend').hidden = !withCov;
    ['covered', 'partial', 'uncovered'].forEach((k) => { $(`#covLegend [data-c="${k}"]`).textContent = g.nodes.filter((n) => (n.data_coverage || 'uncovered') === k).length; });
    const draw = () => {
      destroyGraph();
      const cy = state.cy = cytoscape({ container: $('#graph'), elements: els, style: RA_STYLE, wheelSensitivity: 0.25, minZoom: 0.25, maxZoom: 3, layout: { name: 'preset' } });
      runLayout(cy, state.layout, false);
      const clear = () => cy.elements().removeClass('dim dim-strong anchor');
      cy.on('mouseover', 'node', (e) => { e.target.addClass('is-hover'); e.target.connectedEdges().addClass('is-hover'); });
      cy.on('mouseout', 'node', (e) => { e.target.removeClass('is-hover'); e.target.connectedEdges().removeClass('is-hover'); });
      cy.on('mouseover', 'edge', (e) => e.target.addClass('is-hover'));
      cy.on('mouseout', 'edge', (e) => e.target.removeClass('is-hover'));
      cy.on('tap', 'node', (e) => { // focus the node and its neighbours
        const node = e.target; const keep = new Set(node.neighborhood('node').add(node).map((n) => n.id()));
        cy.batch(() => {
          clear(); node.removeStyle();
          cy.nodes().forEach((n) => { if (!keep.has(n.id())) n.addClass('dim'); });
          cy.edges().forEach((ed) => { if (!keep.has(ed.source().id()) || !keep.has(ed.target().id())) ed.addClass('dim'); });
          node.addClass('anchor');
        });
        showNode(node.id());
      });
      cy.on('tap', 'edge', (e) => {
        const ed = e.target; const ends = new Set([ed.source().id(), ed.target().id()]);
        cy.batch(() => {
          clear();
          cy.nodes().forEach((n) => { if (!ends.has(n.id())) n.addClass('dim-strong'); });
          cy.edges().forEach((x) => { if (x.id() !== ed.id()) x.addClass('dim-strong'); });
          ed.addClass('anchor');
        });
        const lab = (id) => (g.nodes.find((n) => n.id === id) || {}).label || id;
        $('#nodeInfo').hidden = false; $('#nodeInfo').innerHTML = `<h4>${esc(lab(ed.data('source')))} → ${esc(lab(ed.data('target')))}</h4>${esc(ed.data('note') || '')}`;
      });
      cy.on('tap', (e) => { if (e.target === cy) { clear(); cy.elements().unselect(); $('#nodeInfo').hidden = true; } });
      // the gentle pulse on the outcome (the research target), only while the Frame tab is open
      state.cyPulse = setInterval(() => {
        if (cy.destroyed() || !$('#tab-frame').classList.contains('on')) return;
        const t = cy.$('node[?target]').not('.anchor'); if (!t.length) return;
        t.animate({ style: { 'overlay-opacity': 0.16, 'overlay-padding': 7 } }, { duration: 700, easing: 'ease-out',
          complete: () => t.animate({ style: { 'overlay-opacity': 0, 'overlay-padding': 0 } }, { duration: 1100, easing: 'ease-in' }) });
      }, 2500);
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
        watchPreviews(false);
        if (state.conv) {
          const t = convTurn(null);
          [{ type: 'scope', scope: j.scope }, j.graph && { type: 'graph', graph: j.graph }, { type: 'recommendations', picks: j.picks, methods: state.methods, next_steps: state.nextSteps }]
            .filter(Boolean).forEach((e) => convRecord(t, e));
          convSave();
        }
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
    const win = String(state.scope.time_start).slice(0, 4) + (String(state.scope.time_end).slice(0, 4) !== String(state.scope.time_start).slice(0, 4) ? '-' + String(state.scope.time_end).slice(0, 4) : '');
    return `<div class="tb"><svg class="timebar" viewBox="0 0 100 16" preserveAspectRatio="none" aria-label="Data years versus your study window">
      <rect x="${X(d0)}" y="5" width="${Math.max(0.6, X(d1) - X(d0))}" height="6" rx="2" fill="#9db4d6"/>${ongoing ? `<rect x="${X(now)}" y="5" width="${Math.max(0, X(d1) - X(now))}" height="6" fill="#9db4d6" opacity=".35"/>` : ''}
      <rect x="${X(s0)}" y="1.5" width="${Math.max(0.8, X(e1) - X(s0))}" height="13" rx="2" fill="none" stroke="#00356b" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>
      <div class="tb-l"><span>data ${Math.floor(d0)}</span><span class="tb-w">study ${esc(win)}</span><span>${ongoing ? 'ongoing' : Math.floor(d1 - 0.01)}</span></div></div>`;
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
      ps.map((p) => `<tr><td data-ref="${esc(p.ref)}" title="${esc(p.title)}">${esc(p.title)}</td>${cell(p.fit.extent)}${cell(p.fit.time)}${cell(p.fit.resolution)}${cell({ label: p.fit.overall, reason: fitLabel[p.fit.overall] })}<td>${badge(p.fit.access?.label || 'unknown', { open: 'Open', yale: 'Yale', request: 'Reg.', paid: 'Paid' }[p.fit.access?.label] || '?', p.fit.access?.reason)}</td></tr>`).join('')}</tbody></table>`;
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
        // one line that matters most: the first dimension that does not fit, else "fits"
        const worst = ['resolution', 'extent', 'time'].map((k) => p.fit[k]).find((x) => x && (x.label === 'poor' || x.label === 'partial'));
        const dimName = { extent: 'area', time: 'time', resolution: 'detail' };
        const goods = Object.keys(dimName).filter((k) => p.fit[k]?.label === 'good').map((k) => dimName[k]);
        const tableNoDetail = p.data_type === 'table' && p.fit.resolution?.label === 'unknown'; // a table has no cell size to check
        const unknown = Object.keys(dimName).filter((k) => p.fit[k]?.label === 'unknown' && !(k === 'resolution' && tableNoDetail)).map((k) => dimName[k]);
        const list = (xs) => xs.length > 1 ? xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1] : xs[0];
        const one = (worst ? worst.reason : !unknown.length ? (tableNoDetail ? `Fits your ${list(goods) || 'question'} (a table)` : 'Fits your area, time and detail')
          : goods.length ? `Fits your ${list(goods)}; ${list(unknown)} not listed` : 'Details not listed; check the source');
        const reasons = ['extent', 'time', 'resolution'].map((k) => p.fit[k]).filter((x) => x && x.label !== 'unknown').map((x) => `<li>${esc(x.reason)}</li>`).join('');
        const url = safeUrl(p.url); const acc = p.fit.access?.label || 'unknown';
        html += `<div class="dcard ${p.fit.overall === 'poor' ? 'poorfit' : ''}" data-ref="${esc(p.ref)}">
          <div class="t"><span class="fitdot ${esc(p.fit.overall)}"></span><span class="tt">${esc(p.title)}</span>${p.data_type === 'table' ? '<span class="tbl-badge" title="Non-spatial data: a table (statistics, survey, records). It is joined to a map when it has place codes.">Table</span>' : ''}${acc !== 'open' && acc !== 'unknown' ? badge(acc, accessLabel[acc], p.fit.access?.reason) : ''}${p.verified === 'unverified' ? badge('poor', 'Not verified', verifiedText.unverified) : ''}<span class="chev">›</span></div>
          <div class="src">${esc([p.source, p.resolution, p.time].filter(Boolean).join(' · '))}</div>
          <div class="one ${esc(worst ? worst.label : unknown.length ? 'unknown' : p.fit.overall)}">${esc(one)}</div>
          <div class="pv" data-pv="${esc(p.ref)}">${pvLine(p.ref)}</div>
          <div class="more" hidden>
            ${timeBar(p)}
            ${reasons ? `<ul class="reasons">${reasons}</ul>` : ''}
            ${p.why ? `<div class="why">${esc(p.why)}</div>` : ''}
            ${p.limitations ? `<div class="lim"><b>Limits:</b> ${esc(p.limitations)}</div>` : ''}
            ${p.yale_note ? `<div class="yale-note">${esc(p.yale_note)}</div>` : ''}
            ${p.factors && p.factors.length ? `<div class="factors">${p.factors.map((f) => `<span>${esc(nodeLabel(f))}</span>`).join('')}</div>` : ''}
            <div class="row">${url ? `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener">Source</a>` : ''}<button class="btn" data-act="hb">How to access</button><button class="btn" data-act="cov">Coverage</button>${p.stac_collection ? '<button class="btn" data-act="preview">Preview</button>' : ''}${p.map && (state.previews[p.ref] || {}).status !== 'ready' ? '<button class="btn" data-act="map" title="Draw its layers on the map">Map</button>' : ''}<button class="btn" data-act="ask">Ask</button>
              <span class="verified">${esc(verifiedText[p.verified] || '')}</span></div>
          </div></div>`;
      });
    });
    $('#picks').innerHTML = html;
    $$('.dcard').forEach((c) => {
      const p = ps.find((x) => x.ref === c.dataset.ref); if (!p) return;
      c.onclick = (e) => { if (e.target.closest('button, a')) return; const mo = $('.more', c); mo.hidden = !mo.hidden; c.classList.toggle('open', !mo.hidden); };
      c.querySelector('[data-act=hb]').onclick = () => openHandbook(p.ref, p.title);
      c.querySelector('[data-act=cov]').onclick = () => showCoverage(p);
      const pv = c.querySelector('[data-act=preview]'); if (pv) pv.onclick = () => send(`Show a recent preview of ${p.title} (${p.stac_collection}) for my study area on the map.`);
      const mb = c.querySelector('[data-act=map]'); if (mb) mb.onclick = () => mapItem(p.ref, mb);
      c.querySelector('[data-act=ask]').onclick = () => { $('#q').value = `About ${p.title}: `; switchTab('chat'); $('#q').focus(); autosize(); };
    });
    bindPreviewButtons();
    const ms = state.methods || [];
    $('#methods').innerHTML = (ms.length ? '<div class="group-h">Methods</div><ul class="mlist">' + ms.map((x) => `<li title="${esc(x.why)}"><b>${esc(x.name)}</b> <span class="muted">${esc(x.tools)}</span></li>`).join('') + '</ul>' : '') +
      ((state.nextSteps || []).length ? '<div class="group-h">Next steps</div><ol class="mlist small">' + state.nextSteps.slice(0, 3).map((s) => `<li>${esc(s)}</li>`).join('') + '</ol>' : '') +
      (ps.length ? `<div class="data-foot"><button class="btn" id="btnWatch" title="Search again on a schedule and post updates to a private link">Keep looking for me</button>
        <button class="btn" id="btnConsult" title="One-page summary for a YCGS consultant">Ask a YCGS consultant</button></div>` : '');
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

  // ------------------------------------------------------------------ layers collected in GeoLibre, drawn from a card
  async function mapItem(ref, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }
    try {
      if (state.restoring) await state.restoring;
      const r = await fetch('api/map_layer', { method: 'POST', headers: headers(), body: JSON.stringify({ sid: state.sid, ref }) });
      const j = await r.json().catch(() => ({}));
      if (j.sid && j.sid !== state.sid) { state.sid = j.sid; store.set('yf_sid', j.sid); }
      if (!r.ok) throw new Error(j.error || `Server error ${r.status}`);
      const t = state.conv ? convTurn(null) : null;
      (j.events || []).forEach((ev) => { if (t) convRecord(t, ev); handle(ev, {}); });
      if (t) convSave();
      const notes = ((j.result && j.result.layers) || []).map((l) => l.note).filter(Boolean);
      toast(`Added ${j.summary}${notes.length ? ' · ' + notes[0] : ''}`);
      $('#layerPanel').hidden = false;
    } catch (err) { toast(err.message); }
    finally { if (btn) { btn.disabled = false; btn.textContent = 'Map'; } }
  }

  // ------------------------------------------------------------------ map previews of the recommended datasets
  // The server builds them in the background (previews.py): online services are drawn directly; open data
  // without a service is downloaded in a small slice for this session. Core datasets show, the rest wait hidden.
  state.previews = {}; state.pvOpen = new Set();
  // a small trend line for tables with years (World Bank countries, town tables, survey series)
  function sparkSVG(sr) {
    const pts = (sr && Array.isArray(sr.points) ? sr.points : []).filter((p) => Array.isArray(p) && Number.isFinite(+p[0]) && Number.isFinite(+p[1]));
    if (pts.length < 2) return '';
    const W = 240, H = 46, L = 4, R = 4, T = 6, B = 14;
    const xs = pts.map((p) => +p[0]), ys = pts.map((p) => +p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const X = (x) => (L + ((x - x0) / Math.max(1e-9, x1 - x0)) * (W - L - R)).toFixed(1);
    const Y = (y) => (T + (1 - (y - y0) / Math.max(1e-9, y1 - y0)) * (H - T - B)).toFixed(1);
    const d = pts.map((p, i) => `${i ? 'L' : 'M'}${X(+p[0])},${Y(+p[1])}`).join('');
    const last = pts[pts.length - 1];
    return `<div class="pv-spark"><div class="pv-sl">${esc(sr.label || '')} · ${esc(fmtNum(+pts[0][1]))} (${esc(String(pts[0][0]))}) → <b>${esc(fmtNum(+last[1]))}</b> (${esc(String(last[0]))})</div>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="Trend"><path d="${d}" fill="none" stroke="#286dc0" stroke-width="1.8" vector-effect="non-scaling-stroke"/>
      <circle cx="${X(+last[0])}" cy="${Y(+last[1])}" r="2.6" fill="#286dc0"/><text x="${L}" y="${H - 2}" font-size="9" fill="#64748b">${esc(String(x0))}</text>
      <text x="${W - R}" y="${H - 2}" font-size="9" fill="#64748b" text-anchor="end">${esc(String(x1))}</text></svg></div>`;
  }
  function tableHTML(t) {
    if (!t || !Array.isArray(t.columns)) return '';
    const head = t.columns.map((c) => `<th>${esc(c)}</th>`).join('');
    const rows = (t.rows || []).map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('');
    const more = t.n_rows > (t.rows || []).length ? `<div class="muted small">${Number(t.n_rows).toLocaleString()} rows, ${t.n_cols} columns; the CSV has them all</div>` : '';
    return `<div class="pv-tbl"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>${more}`;
  }
  function pvLine(ref) {
    const it = state.previews[ref]; if (!it) return '';
    if (it.status === 'pending') return '<span class="pv-dot run"></span><span class="muted">Preparing a map preview...</span>';
    if (it.status !== 'ready') return `<span class="pv-dot"></span><span class="muted">No map preview: ${esc(it.note || '')}</span>`;
    const csv0 = it.csv ? ` <a class="pv-a" href="api/session/${encodeURIComponent(state.sid)}/sample/${encodeURIComponent(it.csv)}" download>CSV</a>` : '';
    const open = state.pvOpen.has(ref);
    const tblBtn = it.table ? ` <button class="pv-a" data-pvact="table" data-ref="${esc(ref)}">${open ? 'Hide table' : 'View table'}</button>` : '';
    const extra = `${sparkSVG(it.series)}${open ? tableHTML(it.table) : ''}`;
    if (it.kind === 'table') return `<span class="pv-dot ok"></span><span>Table preview</span><span class="muted"> · ${esc(it.note || '')}</span>${tblBtn}${csv0}${extra}`;
    if (!(it.layers || []).length) return `<span class="pv-dot ok"></span><span class="muted">${esc(it.note || 'Already on the map')}</span>`;
    const ids = (it.layers || []).map((l) => l.id); const vis = ids.some((id) => state.layers.get(id)?.visible);
    const csv = it.csv ? ` <a class="pv-a" href="api/session/${encodeURIComponent(state.sid)}/sample/${encodeURIComponent(it.csv)}" download>CSV</a>` : '';
    return `<span class="pv-dot ok"></span><span>${it.kind === 'slice' ? 'Sample on map' : 'On map'}</span><span class="muted"> · ${esc(it.note || '')}</span>
      <button class="pv-a" data-pvact="toggle" data-ref="${esc(ref)}">${vis ? 'Hide' : 'Show'}</button>${tblBtn}${csv}${extra}`;
  }
  function refreshPreviewLines() {
    $$('.pv[data-pv]').forEach((el) => { el.innerHTML = pvLine(el.dataset.pv); });
    bindPreviewButtons();
  }
  function bindPreviewButtons() {
    $$('[data-pvact=table]').forEach((b) => { b.onclick = (e) => {
      e.stopPropagation(); const r = b.dataset.ref; state.pvOpen.has(r) ? state.pvOpen.delete(r) : state.pvOpen.add(r); refreshPreviewLines();
    }; });
    $$('[data-pvact=toggle]').forEach((b) => { b.onclick = (e) => {
      e.stopPropagation(); const it = state.previews[b.dataset.ref]; if (!it) return;
      const ids = (it.layers || []).map((l) => l.id).filter((id) => state.layers.has(id));
      const show = !ids.some((id) => state.layers.get(id).visible);
      ids.forEach((id) => { if (state.layers.get(id).visible !== show) toggleLayer(id); });
      if (show && it.layers[0] && it.layers[0].bbox) fit(state.scope?.bbox || it.layers[0].bbox);
      refreshPreviewLines();
    }; });
  }
  async function watchPreviews(start) {
    if (!state.sid) return;
    clearTimeout(state.pvTimer); const gen = state.pvGen = (state.pvGen || 0) + 1; const t0 = Date.now();
    const applied = new Set([...state.layers.keys()]);
    const tick = async (first) => {
      if (gen !== state.pvGen) return;
      let j;
      try { j = await (await fetch(`api/session/${encodeURIComponent(state.sid)}/previews`, { method: first && start ? 'POST' : 'GET', headers: headers() })).json(); }
      catch { j = null; }
      if (gen !== state.pvGen || !j || !j.items) return;
      const fresh = [];
      Object.entries(j.items).forEach(([ref, it]) => {
        state.previews[ref] = it;
        (it.layers || []).forEach((l) => { if (!applied.has(l.id)) { applied.add(l.id); fresh.push(l); } });
      });
      if (fresh.length) {
        const t = state.conv ? convTurn(null) : null; // kept with the conversation, so reopening shows them
        let shown = [...state.layers.values()].some((e) => e.visible && e.d.paint && e.d.kind === 'geojson' && e.d.paint.mode);
        fresh.forEach((l) => {
          if (l.kind === 'geojson' && l.paint && l.paint.mode && !l.hidden) { if (shown) l.hidden = true; else shown = true; } // choropleths do not stack
          const ev = { type: 'layer', layer: l }; if (t) convRecord(t, ev); addLayer(l).then(refreshPreviewLines);
        });
        if (t) convSave();
      }
      refreshPreviewLines();
      if (j.running && Date.now() - t0 < 150000) state.pvTimer = setTimeout(() => tick(false), 2500);
    };
    tick(true);
  }

  // ------------------------------------------------------------------ data files from a link, kept on the server for this session
  $('#dlForm').onsubmit = async (e) => {
    e.preventDefault(); const url = $('#dlUrl').value.trim(); if (!url) return;
    const b = $('#dlGo'); b.disabled = true; b.textContent = 'Downloading...';
    try {
      if (state.restoring) await state.restoring;
      const r = await fetch('api/download', { method: 'POST', headers: headers(), body: JSON.stringify({ sid: state.sid, url }) });
      const j = await r.json().catch(() => ({}));
      if (j.sid && j.sid !== state.sid) { state.sid = j.sid; store.set('yf_sid', j.sid); }
      if (!r.ok) throw new Error(j.error || `Server error ${r.status}`);
      const t = state.conv ? convTurn(null) : null;
      (j.events || []).forEach((ev) => { if (t) convRecord(t, ev); handle(ev, {}); });
      if (t) convSave();
      const notes = ((j.result && j.result.layers) || []).map((l) => l.note).filter(Boolean);
      toast(`Added ${j.summary}${notes.length ? ' · ' + notes[0] : ''}`); $('#dlUrl').value = '';
    } catch (err) { toast(err.message); }
    finally { b.disabled = false; b.textContent = 'Add'; }
  };

  // ------------------------------------------------------------------ catalog tab
  let catT = null;
  async function catSearch() {
    const q = $('#catQ').value.trim(); const srcF = state.catSource || 'any';
    if (!q && srcF === 'any') { $('#catRes').innerHTML = ''; $('#catMeta').textContent = state.config ? `${state.config.catalog.counts?.total || ''} cards: YCGS curated, Planetary Computer, Earth Engine and its community catalog, and layers collected in GeoLibre. Built ${state.config.catalog.built}.` : ''; return; }
    try {
      const j = await getJSON(`api/catalog/search?q=${encodeURIComponent(q)}&kind=${state.catKind}&access=${state.catAccess}&source=${encodeURIComponent(srcF)}&sid=${encodeURIComponent(state.sid)}&limit=${q ? 30 : 150}`);
      $('#catMeta').textContent = `${j.results.length} results${state.scope ? ' · fit labels use your current scope' : ''}`;
      $('#catRes').innerHTML = j.results.map((c) => `<div class="ccard" data-id="${esc(c.id)}"><div class="t">${safeUrl(c.url) ? `<a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener">${esc(c.title)}</a>` : esc(c.title)}${c.data_type === 'table' ? '<span class="tbl-badge" title="Non-spatial data: a table">Table</span>' : ''}</div>
        <div class="src">${esc([c.kind !== 'dataset' ? c.kind : '', c.source, c.provider, c.resolution, c.time].filter(Boolean).join(' · '))}</div>
        <div class="d">${esc(c.description || '')}</div>
        <div class="row">${c.fit ? fitBadges(c.fit) : badge(c.access_class || 'unknown', accessLabel[c.access_class] || c.access || '', c.access)}${c.map ? '<button class="btn primary" data-act="map" title="Draw its layers on the map">Map</button>' : ''}${safeUrl(c.viewer) ? `<a class="btn" href="${esc(safeUrl(c.viewer))}" target="_blank" rel="noopener" title="The original, styled map in GeoLibre">GeoLibre</a>` : ''}<button class="btn" data-act="ask">Ask Y-finder</button><button class="btn" data-act="hb">How to access</button></div></div>`).join('') || '<div class="empty">No matches. Try other words.</div>';
      $$('.ccard').forEach((el) => {
        const c = j.results.find((x) => x.id === el.dataset.id);
        el.querySelector('[data-act=ask]').onclick = () => send(`Does "${c.title}" (${c.id}) fit my study? If there is no study yet, tell me what it is good for.`);
        el.querySelector('[data-act=hb]').onclick = () => openHandbook(c.id, c.title);
        const mb = el.querySelector('[data-act=map]'); if (mb) mb.onclick = () => mapItem(c.id, mb);
      });
    } catch (err) { $('#catRes').innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
  }
  $('#catQ').oninput = () => { clearTimeout(catT); catT = setTimeout(catSearch, 250); };
  $$('#catThemes button').forEach((b) => { b.onclick = () => { $('#catQ').value = b.dataset.q; catSearch(); }; });
  $$('#catChips button').forEach((b) => { b.onclick = () => { $$('#catChips button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); state.catKind = b.dataset.kind; state.catAccess = b.dataset.access; state.catSource = b.dataset.source || 'any'; catSearch(); }; });

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
    $('#geolibre').hidden = !gis; $('#gisBar').hidden = !gis; $('.layers-box').hidden = gis; renderLegend();
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
  function autosize() { const q = $('#q'); q.style.height = 'auto'; q.style.height = Math.min(q.scrollHeight, 220) + 'px'; }
  $('#q').addEventListener('input', autosize);
  $('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($('#q').value); } });
  $('#ask').onsubmit = (e) => { e.preventDefault(); send($('#q').value); };
  // The map container changes size with the panel; keep the same place in view.
  function mapResized() {
    if (!state.mapReady) return;
    const c = map.getCenter(); map.resize(); map.setCenter(c);
  }
  $('#btnCollapse').onclick = () => { const c = document.body.classList.toggle('collapsed'); $('#btnCollapse').setAttribute('aria-expanded', String(!c)); requestAnimationFrame(mapResized); };
  const isWide = () => document.body.classList.contains('wide');
  function setWide(w) {
    document.body.classList.toggle('wide', w); $('#btnWide').setAttribute('aria-pressed', String(w));
    $('#btnWide').title = w ? 'Smaller chat (more map)' : 'Larger chat';
    setHist(w); // the large chat shows the conversation list beside it
    local.set('yf_wide', w ? '1' : '0');
    requestAnimationFrame(mapResized);
    setTimeout(() => { if (state.cy) { state.cy.resize(); state.cy.fit(undefined, 18); } }, 60);
  }
  $('#btnWide').onclick = () => setWide(!isWide());
  $('#q').addEventListener('focus', () => { if (document.body.classList.contains('collapsed')) { document.body.classList.remove('collapsed'); requestAnimationFrame(mapResized); } });

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
  // A new conversation keeps the old one in the history (and its server session, until it expires).
  function newConversation() {
    clearView();
    state.sid = ''; store.set('yf_sid', ''); store.set('yf_conv', '');
    renderHist();
  }
  function clearView() {
    state.pending.length = 0;
    [...state.layers.keys()].forEach(removeLayer);
    Object.assign(state, { graph: null, scope: null, picks: [], methods: [], nextSteps: [], conv: null, previews: {} });
    clearTimeout(state.pvTimer); state.pvGen = (state.pvGen || 0) + 1;
    destroyGraph();
    msgs.innerHTML = ''; $('#welcome').hidden = false; $('#nodeInfo').hidden = true; $('#attachInfo').hidden = true;
    STEPS.forEach((s) => step(s, null));
    if (state.mapReady && map.getSource('coverage')) map.getSource('coverage').setData({ type: 'FeatureCollection', features: [] });
    if (state.gisMode) setMode(false);
    renderGraph(); renderScope(); renderData(); switchTab('chat');
  }

  // ------------------------------------------------------------------ conversation history (ChatGPT-style list, this browser only)
  // Each conversation keeps its turns as the event stream the UI received, so reopening it replays the same view.
  const HKEY = 'yf_conversations_v1', HMAX = 30, HBYTES = 2.5e6;
  function histLoad() {
    try { const a = JSON.parse(local.get(HKEY) || '[]'); return Array.isArray(a) ? a.filter((x) => x && x.id && Array.isArray(x.turns)) : []; } catch { return []; }
  }
  function histWrite(list) {
    list = list.slice().sort((a, b) => b.updated - a.updated).slice(0, HMAX);
    for (;;) {
      const s = JSON.stringify(list);
      if ((s.length <= HBYTES || list.length <= 1) && local.set(HKEY, s)) return true;
      if (list.length <= 1) return false; // even one conversation does not fit
      list.pop(); // over the budget: drop the oldest
    }
  }
  function histPut(c, bump = true) {
    if (!c || !c.turns.length) return;
    if (bump) c.updated = Date.now();
    const list = histLoad().filter((x) => x.id !== c.id); list.push(c);
    if (!histWrite(list)) toast('This conversation is too large to keep in the browser.');
    renderHist();
  }
  function convStart(title, extra = {}) {
    const now = Date.now();
    state.conv = { id: 'c' + now.toString(36) + Math.random().toString(36).slice(2, 6), title: String(title || 'Conversation').slice(0, 90), created: now, updated: now, sid: state.sid, turns: [], ...extra };
    store.set('yf_conv', state.conv.id);
  }
  function convTurn(q) { if (!state.conv) convStart(q); const t = { q, ev: [] }; state.conv.turns.push(t); return t; }
  const KEEP_LAST = new Set(['graph', 'scope', 'recommendations']); // only the final value matters for these
  function convRecord(t, ev) {
    if (!t || !ev || ev.type === 'status' || ev.type === 'session') return;
    const last = t.ev[t.ev.length - 1];
    if (ev.type === 'text') { if (last && last.type === 'text') last.delta += ev.delta; else t.ev.push({ type: 'text', delta: ev.delta }); return; }
    if (ev.type === 'layer' && ev.layer && ev.layer.data) {
      if (!ev.layer.url) return; // inline-only layer: too big to keep
      const { data, ...rest } = ev.layer; ev = { ...ev, layer: rest };
    }
    if (KEEP_LAST.has(ev.type)) { const i = t.ev.findIndex((x) => x.type === ev.type); if (i >= 0) { t.ev[i] = ev; return; } }
    t.ev.push(ev);
  }
  function convSave() { const c = state.conv; if (!c) return; c.sid = state.sid; c.model = state.model; histPut(c); }

  function replayConv(c) {
    $('#welcome').hidden = true;
    const quiet = { chip() {}, addText() {}, seg() { return document.createElement('div'); } };
    for (const t of c.turns) {
      if (t.q == null) { t.ev.forEach((ev) => handle(ev, quiet)); continue; }
      userMsg(t.q); const m = botMsg();
      t.ev.forEach((ev) => handle(ev, m));
      if (!t.ev.some((e) => e.type === 'done')) { $('.st', m.act).className = 'st ok'; $('.lbl', m.act).textContent = 'Interrupted'; }
    }
    $$('.choices', msgs).slice(0, -1).forEach((b) => b.classList.add('used')); // only the latest quick replies stay active
    scrollChat();
  }
  async function openConv(id) {
    if (state.conv && state.conv.id === id) { if (!isWide()) setHist(false); return; }
    if (state.busy) { toast('Wait for the current answer to finish.'); return; }
    const c = histLoad().find((x) => x.id === id); if (!c) return;
    clearView();
    state.conv = c; store.set('yf_conv', c.id);
    state.sid = c.sid || ''; store.set('yf_sid', state.sid);
    replayConv(c); renderHist();
    if (!isWide()) setHist(false);
    if (state.sid) { const p = ensureSession(c); state.restoring = p; p.finally(() => { if (state.restoring === p) state.restoring = null; }); }
  }
  // Server sessions end after 6 hours or a restart; rebuild one from what this browser kept so follow-ups keep their context.
  async function ensureSession(c) {
    try { await getJSON(`api/session/${encodeURIComponent(c.sid)}`); return; } catch (err) { if (err.status !== 404) return; }
    const said = (t) => t.ev.filter((e) => e.type === 'text').map((e) => e.delta).join('').trim().slice(0, 700);
    const layers = []; const ids = new Set();
    c.turns.forEach((t) => t.ev.forEach((e) => { if (e.type === 'layer' && e.layer && !ids.has(e.layer.id)) { ids.add(e.layer.id); layers.push(e.layer); } }));
    const body = { sid: c.sid, model: c.model || state.model, prev_response_id: c.rid || null, graph: state.graph, scope: state.scope,
      picks: state.picks, methods: state.methods, next_steps: state.nextSteps, layers,
      recap: c.turns.filter((t) => t.q).slice(-4).map((t) => ({ q: t.q.slice(0, 500), a: said(t) })) };
    try {
      const r = await fetch('api/session/restore', { method: 'POST', headers: headers(), body: JSON.stringify(body) });
      const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText);
      if (state.conv === c) { state.sid = j.sid; store.set('yf_sid', j.sid); }
      if (c.sid !== j.sid) { c.sid = j.sid; histPut(c, false); }
    } catch (err) { console.warn('Could not restore the server session', err); }
  }
  function deleteConv(id) {
    const c = histLoad().find((x) => x.id === id); if (!c) return;
    if (!confirm(`Delete "${c.title}"?\nIt is removed from this browser and from the server. This cannot be undone.`)) return;
    histWrite(histLoad().filter((x) => x.id !== id));
    if (c.sid) fetch(`api/session/${encodeURIComponent(c.sid)}/reset`, { method: 'POST', headers: headers() }).catch(() => {});
    if (state.conv && state.conv.id === id) { if (state.busy) state.conv = null; else newConversation(); }
    renderHist();
  }
  function renderHist() {
    const list = histLoad().sort((a, b) => b.updated - a.updated); const cur = state.conv && state.conv.id; const box = $('#histList');
    $('#histClear').hidden = !list.length;
    if (!list.length) { box.innerHTML = '<div class="hist-empty muted small">Your conversations appear here, including the demos you watch.</div>'; return; }
    const t0 = new Date(); t0.setHours(0, 0, 0, 0); const today = t0.getTime(), day = 864e5;
    const grp = (u) => (u >= today ? 'Today' : u >= today - day ? 'Yesterday' : u >= today - 6 * day ? 'Previous 7 days' : 'Older');
    let html = '', last = '';
    list.forEach((c) => {
      const g = grp(c.updated); if (g !== last) { html += `<div class="hist-grp">${g}</div>`; last = g; }
      const n = c.turns.filter((t) => t.q).length;
      const when = new Date(c.updated).toLocaleString([], g === 'Today' || g === 'Yesterday' ? { hour: 'numeric', minute: '2-digit' } : { month: 'short', day: 'numeric' });
      html += `<div class="hitem${c.id === cur ? ' on' : ''}" data-id="${esc(c.id)}"><button class="hopen" title="${esc(c.title)}"><span class="ht">${esc(c.title)}</span>
        <span class="hm">${c.demo ? 'Demo · ' : ''}${n} question${n === 1 ? '' : 's'} · ${esc(when)}</span></button><button class="hdel" title="Delete" aria-label="Delete conversation">&times;</button></div>`;
    });
    box.innerHTML = html;
    $$('.hitem', box).forEach((el) => { $('.hopen', el).onclick = () => openConv(el.dataset.id); $('.hdel', el).onclick = () => deleteConv(el.dataset.id); });
  }
  function setHist(open) {
    document.body.classList.toggle('hist-open', open); $('#btnHist').setAttribute('aria-expanded', String(open));
    if (open) renderHist();
    setTimeout(() => { if (state.cy) state.cy.resize(); }, 60);
  }
  $('#btnHist').onclick = () => { document.body.classList.remove('collapsed'); requestAnimationFrame(mapResized); setHist(!document.body.classList.contains('hist-open')); };
  $('#histNew').onclick = () => { if (state.demo) state.demo.stop = true; if (!state.busy) newConversation(); if (!isWide()) setHist(false); };
  $('#histClear').onclick = () => {
    const list = histLoad(); if (!list.length) return;
    if (!confirm(`Delete all ${list.length} conversations?\nThey are removed from this browser and from the server. This cannot be undone.`)) return;
    list.forEach((c) => { if (c.sid) fetch(`api/session/${encodeURIComponent(c.sid)}/reset`, { method: 'POST', headers: headers() }).catch(() => {}); });
    histWrite([]); if (!state.busy) newConversation(); else state.conv = null; renderHist();
  };
  $('.pmain').addEventListener('pointerdown', () => { if (!isWide() && document.body.classList.contains('hist-open')) setHist(false); });
  // Large chat by default on big screens; the choice is remembered.
  { const pref = local.get('yf_wide'); if (innerWidth > 980 && (pref === '1' || (pref == null && innerWidth >= 1280))) setWide(true); else renderHist(); }

  // ------------------------------------------------------------------ demo cases (recorded runs, replayed, then live)
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  async function loadDemos() {
    try {
      const j = await getJSON('static/demos/index.json?v=' + (window.YF_VERSION || '')); if (!j.cases || !j.cases.length) return;
      const card = (c) => `<button class="demo-card" data-id="${esc(c.id)}" title="${esc(c.who + (c.program ? '\nBased on: ' + c.program : '') + '\nShows: ' + c.shows.join('; '))}">
        <div class="field">${esc(c.program ? c.program : c.field)}</div><div class="title">${esc(c.title)}</div></button>`;
      const yale = j.cases.filter((c) => c.group === 'yale'), rest = j.cases.filter((c) => c.group !== 'yale');
      // Yale research cases first; the earlier general demos stay one click away
      $('#demos').innerHTML = yale.length
        ? `<div class="demo-grid">${yale.map(card).join('')}</div>${rest.length ? `<details class="more-demos"><summary>More demos (${rest.length})</summary><div class="demo-grid">${rest.map(card).join('')}</div></details>` : ''}`
        : `<div class="demo-grid">${rest.map(card).join('')}</div>`;
      if (yale.length) $('#demoWrap .sec-h').textContent = 'Watch a demo: Yale research cases';
      $$('.demo-card').forEach((b) => { b.onclick = () => playDemo(b.dataset.id); });
      $('#demoWrap').hidden = false;
    } catch { /* no recorded demos on this server */ }
  }
  function demoNote(text) {
    if (!text) return;
    const d = document.createElement('div'); d.className = 'demo-note'; d.textContent = text;
    msgs.appendChild(d); scrollChat();
    if (!$('#tab-chat').classList.contains('on')) toast(text, Math.max(2500, 5200 / state.demo.speed));
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
    try { demo = await getJSON(`static/demos/${encodeURIComponent(id)}.json?v=${window.YF_VERSION || ''}`); } catch { toast('Demo not found.'); return; }
    newConversation(); convStart(demo.title, { demo: id });
    state.busy = true; $('#btnSend').disabled = true; $('#welcome').hidden = true;
    state.demo = { stop: false, speed: +(store.get('yf_demo_speed') || 2) };
    const head = document.createElement('div'); head.className = 'demo-head';
    const based = demo.program && safeUrl(demo.program_url) ? `<div class="based">Based on <a href="${esc(safeUrl(demo.program_url))}" target="_blank" rel="noopener">${esc(demo.program)}</a>${demo.based_on ? ': ' + esc(demo.based_on) : ''}</div>` : '';
    head.innerHTML = `<div class="field">Demo · ${esc(demo.field)}</div><h3>${esc(demo.title)}</h3><div class="who">${esc(demo.who)}</div>${based}
      <div class="demo-ctl" title="Recorded ${esc(demo.recorded)} with ${esc(demo.model)}"><span class="seg">${[1, 2, 4, 100].map((v) => `<button data-sp="${v}" class="${v === state.demo.speed ? 'on' : ''}">${v === 100 ? 'Skip' : v + 'x'}</button>`).join('')}</span>
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
          const m = botMsg(); const t = convTurn(st.message);
          for (const ev of st.events) {
            if (state.demo.stop) break; await pace(ev); if (ev.type === 'done') ev.model = 'recorded · ' + (ev.model || '');
            const e2 = sub(ev); convRecord(t, e2); handle(e2, m);
          }
          m.thinking.remove(); $('#attachInfo').hidden = true; await wait(900 / sp());
        } else if (st.type === 'ui') {
          const hold = Math.max(1200, 5600 / sp());
          if (st.action === 'tab') { switchTab(st.tab); demoNote(st.note); await wait(hold); }
          else if (st.action === 'scope_edit') {
            switchTab('frame'); await wait(300); $('#scopeCard').scrollIntoView({ behavior: 'smooth' });
            Object.entries(st.set).forEach(([k, v]) => { const inp = { time_start: '#sc_t0', time_end: '#sc_t1', spatial_resolution_m: '#sc_res', unit_of_analysis: '#sc_unit' }[k]; if (inp && $(inp)) { $(inp).value = v; $(inp).animate([{ background: '#fff4c2' }, { background: '#fff' }], { duration: 1800 }); } });
            demoNote(st.note); await wait(hold / 2);
            const quiet = { graphCard: true, card(html) { const d = document.createElement('div'); d.innerHTML = html; return d; }, chip() {}, addText() {}, seg() { return document.createElement('div'); } };
            const t = convTurn(null);
            for (const ev of st.events) { convRecord(t, ev); handle(ev, quiet); }
            await wait(hold / 2); switchTab('data'); await wait(hold / 2);
          } else if (st.action === 'handbook') { demoNote(st.note); openHandbook(st.ref, st.title); await wait(hold * 1.3); $('#dlg').close(); }
          else if (st.action === 'gis') { demoNote(st.note); setMode(true); await wait(hold * 1.4); setMode(false); }
          else if (st.action === 'map') { demoNote(st.note); document.body.classList.add('collapsed'); requestAnimationFrame(mapResized); await wait(hold); document.body.classList.remove('collapsed'); requestAnimationFrame(mapResized); }
          else if (st.action === 'watch') { demoNote(st.note); watchDialog(); await wait(hold); $('#dlg').close(); }
        }
      }
    } finally {
      const end = document.createElement('div'); end.className = 'demo-end';
      end.textContent = state.demo.stop ? 'Demo stopped. Keep asking from here.' : 'Demo done. Ask a follow-up; it continues live.';
      msgs.appendChild(end); scrollChat();
      state.demo = null; state.busy = false; $('#btnSend').disabled = false; convSave();
      if (!state.demo && state.picks && state.picks.length) watchPreviews(true);
    }
  }
  loadDemos();
  $('#btnAbout').onclick = () => {
    $('#dlgTitle').textContent = 'About Y-finder';
    $('#dlgBody').innerHTML = md(`**Y-finder** is a prototype geospatial data guide, built by Huan Ning as a proposal for the Yale Center for Geospatial Solutions (YCGS). It is not an official Yale or YCGS service.

**How it works (tri-projection).** Y-finder turns a research question into an *influence graph* of factors, agrees a *scope* with you (area, time window, resolution, unit of analysis), then searches for *data* and labels how each source fits that scope. The fit labels come from catalog metadata and simple rules, not from the language model.

**Where the data comes from.** ${state.config ? Number(state.config.catalog.counts?.total || 0).toLocaleString() : '3,000+'} catalog cards: YCGS curated cards with Yale access routes, Microsoft Planetary Computer, Google Earth Engine and its community catalog, the layers collected in [GeoLibre](https://geolibre.app/), and World Bank indicators. Live searches cover Yale Dataverse, Harvard Dataverse, Yale Library GeoBlacklight, ArcGIS Online, Data.gov, US open data portals (including Connecticut Open Data), Planetary Computer imagery and the web (DeepSeek search).

**Maps and tables.** Many factors are measured by tables (statistics, surveys, records) as well as maps; Y-finder looks for both and marks tables with a *Table* badge.

**Previews.** For each recommended dataset Y-finder draws an online map service directly, or downloads a small slice for your study area (up to 100 MB) and shows it on the map or as a table preview with a CSV. Licensed and account-only sources say why there is no preview.

**Map and GIS.** Layers you see can be downloaded as GeoJSON, or opened in [GeoLibre](https://web.geolibre.app/) with *Full GIS*. Influence graphs export to [Research Architect](https://app.researcharchitect.ai/).

**Access handbooks.** Each data source can have a handbook in the style of an Agent Skill: access route, endpoint, a Python example and pitfalls. It extends the handbooks of [LLM-Find](https://github.com/gladcolor/llm-find).

**Privacy.** Do not paste protected or confidential data. Questions are sent to an OpenAI model to generate answers, and web searches go to DeepSeek's search service. Your conversations are saved in this browser only; delete them from the conversation list. Data files you or Y-finder download are kept on the server only for your session (about 6 hours after your last action) and then deleted, along with their map layers.`);
    $('#dlg').showModal();
  };
  function askCode() {
    const c = prompt('This Y-finder server needs an access code:'); if (c) { state.code = c; store.set('yf_code', c); }
  }

  // examples
  $('#examples').innerHTML = EXAMPLES.map(([label, q], i) => `<button class="example" data-i="${i}" title="${esc(q)}">${esc(label)}</button>`).join('');
  $$('.example').forEach((b) => { b.onclick = () => send(EXAMPLES[+b.dataset.i][1]); });

  // config
  getJSON('api/config').then((c) => {
    state.config = c;
    const sel = $('#model');
    sel.innerHTML = c.models.map((m) => `<option value="${esc(m.id)}" title="${esc(m.note)}">${esc(m.name)}</option>`).join('');
    const saved = store.get('yf_model'); state.model = c.models.some((m) => m.id === saved) ? saved : c.default_model; sel.value = state.model;
    sel.onchange = () => { state.model = sel.value; store.set('yf_model', sel.value); };
    $('#catQ').placeholder = `Search ${c.catalog.counts?.total || ''} datasets and tools`;
    if (c.access_code && !state.code) askCode();
  }).catch(() => toast('Could not reach the Y-finder server.'));

  // restore this tab's conversation after a page reload (full history), or an older session without one
  const lastConv = store.get('yf_conv');
  if (lastConv && histLoad().some((x) => x.id === lastConv)) openConv(lastConv);
  else if (state.sid) getJSON(`api/session/${encodeURIComponent(state.sid)}`).then((s) => {
    if (!s.turns) return;
    state.graph = s.graph; state.scope = s.scope && Object.keys(s.scope).length ? s.scope : null; state.picks = s.picks || []; state.methods = s.methods || [];
    renderGraph(); renderScope(); renderData(); (s.layers || []).forEach((l) => addLayer(l));
    $('#welcome').hidden = true; msgs.innerHTML = '<div class="muted small">Restored your last session. Earlier messages are not shown.</div>';
  }).catch(() => { state.sid = ''; store.set('yf_sid', ''); });

  window.YF = { state, send, map }; // for debugging and browser tests
})();
