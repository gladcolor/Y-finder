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
    ['Permits and income', 'How did new housing permits change in Connecticut towns after the 2021 zoning reform, and do they track median household income?'],
  ];

  const state = {
    sid: store.get('yf_sid') || '', model: '', busy: false, code: store.get('yf_code') || '',
    layers: new Map(), colorIdx: 0, mapReady: false, pending: [],
    graph: null, scope: null, picks: [], methods: [], nextSteps: [], cy: null, config: null,
    gisMode: false, gisFull: true, catKind: 'any', catAccess: 'any', conv: null, restoring: null,
  };

  // ------------------------------------------------------------------ helpers
  // estimated cost of a turn at the model's list prices (api/config gives US dollars per million tokens)
  function turnCost(usage, model) {
    // the page inlines the prices (window.YF_PRICES), so turns restored before api/config answers still get a cost
    const p = ((state.config || {}).prices || window.YF_PRICES || {})[String(model || '').replace(/^recorded\s*·\s*/, '')]; if (!p || !usage) return null;
    const cached = usage.cached || 0; const fresh = Math.max(0, (usage.input || 0) - cached);
    return (fresh * p.input + cached * p.cached + (usage.output || 0) * p.output) / 1e6;
  }
  const fmtCost = (c) => c == null ? '' : c < 0.01 ? 'under 1¢' : c < 1 ? `${Math.round(c * 100)}¢` : `$${c.toFixed(2)}`;
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
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left'); // beside the Layers button; the legend sits bottom right
  // The attribution box grows with every source on the map. When it would reach the scale bar (a narrow map beside a
  // large chat) it folds to its "i" button, MapLibre's compact mode; a click opens it again and it then stays as the user left it.
  function fitAttribution() {
    const a = document.querySelector('.maplibregl-ctrl-attrib'); const sc = document.querySelector('.maplibregl-ctrl-scale');
    if (!a || !sc || a.dataset.user || !a.classList.contains('maplibregl-compact-show')) return;
    if (!a.dataset.bound) { a.dataset.bound = '1'; a.addEventListener('click', () => { a.dataset.user = '1'; }); }
    if (a.getBoundingClientRect().left < sc.getBoundingClientRect().right + 12) { a.classList.remove('maplibregl-compact-show'); a.removeAttribute('open'); }
  }
  // throttled, not debounced: tiles keep arriving, and a timer that restarts on every one would never fire
  let attribTimer = 0; const attribSoon = () => { if (!attribTimer) attribTimer = setTimeout(() => { attribTimer = 0; fitAttribution(); }, 300); };
  map.on('load', attribSoon); map.on('resize', attribSoon); map.on('sourcedata', attribSoon);
  map.on('load', () => {
    map.resize(); // the panel may have changed size while the style loaded
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
  // ------------------------------------------------------------------ what a layer is: image tiles, areas, lines, points
  const GEOM_TIP = { image: 'Image tiles (raster)', polygon: 'Areas (polygons)', line: 'Lines', point: 'Points', footprint: 'Scene footprints: outlines of the images found',
    scope: 'Your study area', boundary: 'A boundary (outline only)', mixed: 'Points, lines and areas' };
  const GEOM_NOUN = { polygon: ['area', 'areas'], line: ['line', 'lines'], point: ['point', 'points'], footprint: ['scene', 'scenes'], boundary: ['area', 'areas'], mixed: ['feature', 'features'] };
  function geomKind(e) {
    const d = e.d;
    if (d.kind === 'raster') return 'image';
    if (d.style === 'scope') return 'scope';
    if (d.style === 'footprint') return 'footprint';
    const seen = new Set(); const fs = (e.fc && e.fc.features) || [];
    for (let i = 0; i < fs.length && i < 400; i++) {
      const t = ((fs[i] || {}).geometry || {}).type || '';
      if (/Point/.test(t)) seen.add('point'); else if (/Line/.test(t)) seen.add('line'); else if (/Polygon/.test(t)) seen.add('polygon');
    }
    const k = seen.size === 1 ? [...seen][0] : seen.size ? 'mixed' : 'polygon';
    return d.style === 'boundary' && k === 'polygon' ? 'boundary' : k;
  }
  // a small picture of the layer: a landscape for image tiles, a filled shape for areas (with the colour ramp when the
  // layer is graduated or categorized), a stroke for lines, dots for points, dashed outlines for footprints and the study box
  function layerGlyph(e) {
    const k = geomKind(e); const c = HEXC.test(e.color || '') ? e.color : '#2563eb';
    const P = e.d.paint || {};
    let ramp = P.mode === 'graduated' || P.mode === 'categorized' ? (P.stops || []).map((s) => (s || [])[1]).filter((x) => HEXC.test(x || '')) : [];
    if (ramp.length > 4) ramp = [0, 1, 2, 3].map((i) => ramp[Math.round((i * (ramp.length - 1)) / 3)]);
    const gid = 'lg-' + String(e.d.id).replace(/[^\w-]/g, '') + '-' + (state.glyphN = (state.glyphN || 0) + 1);
    const grad = ramp.length > 1 ? `<defs><linearGradient id="${gid}" x1="0" x2="1">${ramp.map((x, i) => (P.mode === 'categorized'
      ? `<stop offset="${i / ramp.length}" stop-color="${x}"/><stop offset="${(i + 1) / ramp.length}" stop-color="${x}"/>`
      : `<stop offset="${i / (ramp.length - 1)}" stop-color="${x}"/>`)).join('')}</linearGradient></defs>` : '';
    const fill = grad ? `url(#${gid})` : c; const edge = 'rgba(15,23,42,.32)';
    const AREA = 'M3 4.6 9.6 1.8 17.2 3.9 18.2 11.4 11 14.4 2.6 12.4Z';
    let body;
    if (k === 'image') body = '<rect x="1" y="1" width="18" height="14" rx="2.4" fill="#eef3ea" stroke="#64748b" stroke-width="1.1"/><path d="M1.6 14.4 7.4 8.2l3.7 3.6 2.6-2.5 4.7 5.1Z" fill="#76a067"/><circle cx="14.6" cy="5" r="1.8" fill="#e0ad3e"/>';
    else if (k === 'scope') body = `<rect x="2" y="2.5" width="16" height="11" rx="1" fill="${c}" fill-opacity=".1" stroke="${c}" stroke-width="1.4" stroke-dasharray="2.6 1.8"/>`;
    else if (k === 'footprint') body = `<rect x="1.5" y="1.5" width="11" height="8.5" fill="none" stroke="${c}" stroke-width="1.3" stroke-dasharray="2.2 1.4"/><rect x="7.5" y="6" width="11" height="8.5" fill="none" stroke="${c}" stroke-width="1.3" stroke-dasharray="2.2 1.4"/>`;
    else if (k === 'boundary') body = `<path d="${AREA}" fill="none" stroke="${c}" stroke-width="1.7" stroke-linejoin="round"/>`;
    else if (k === 'line') body = `<path d="M1.6 12.6c3.2-.4 3.6-8.6 7.6-8.6s3.4 7.4 9.2 6.2" fill="none" stroke="${fill}" stroke-width="2.6" stroke-linecap="round"/>`;
    else if (k === 'point') {
      const cs = ramp.length > 1 ? [ramp[0], ramp[Math.floor(ramp.length / 2)], ramp[ramp.length - 1]] : [c, c, c];
      body = [[4.6, 10.8], [10.2, 4.6], [15.6, 11.2]].map(([x, y], i) => `<circle cx="${x}" cy="${y}" r="3" fill="${cs[i]}" stroke="${edge}" stroke-width=".9"/>`).join('');
    } else if (k === 'mixed') body = `<path d="${AREA}" fill="${fill}" fill-opacity=".4" stroke="${c}" stroke-width="1.2" stroke-linejoin="round"/><circle cx="14.4" cy="5.6" r="2.8" fill="${c}" stroke="#fff" stroke-width="1.1"/>`;
    else body = `<path d="${AREA}" fill="${fill}" fill-opacity="${grad ? 1 : 0.45}" stroke="${grad ? edge : c}" stroke-width="${grad ? 0.9 : 1.4}" stroke-linejoin="round"/>`;
    return `<svg class="gl" viewBox="0 0 20 16" role="img" aria-label="${esc(GEOM_TIP[k])}"><title>${esc(GEOM_TIP[k])}</title>${grad}${body}</svg>`;
  }
  // "naip footprints" (a collection id) reads "NAIP scene footprints"
  const ACRO = new Set(['naip', 'hls', 'modis', 'esa', 'jrc', 'io', 'lulc', 'dem', 'dsm', 'dtm', 'hag', 'cop', 'glo', 'alos', 'nlcd', 'cdl', 'mtbs', 'gnatsgo',
    'nasadem', 'usgs', 'noaa', 'goes', 'aster', 'gpm', 'era5', 'l2a', 'l1c', 'c2', 'l2', 'l1', 's30', 'l30', 'sar', 'grd', 'rtc', '3dep', 'v02', 'fia', 'gbif']);
  function prettyColl(id) {
    const out = [];
    String(id).split('-').forEach((w) => {
      if (/^\d+$/.test(w) && out.length) out[out.length - 1] += '-' + w;
      else out.push(ACRO.has(w.toLowerCase()) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1));
    });
    return out.join(' ');
  }
  function layerName(d) {
    const m = d.style === 'footprint' && /^([a-z0-9][a-z0-9-]*) footprints$/.exec(String(d.title || ''));
    return m ? `${prettyColl(m[1])} scene footprints` : String(d.title || 'Layer');
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
    const twin = d.kind === 'geojson' && d.style !== 'scope' ? [...state.layers.values()].find((e) => e.d.kind === 'geojson' && e.d.title === d.title && e.d.source === d.source && e.d.n === d.n)
      // the same map service drawn by the answer and again by its card's preview
      : d.kind === 'raster' && Array.isArray(d.tiles) ? [...state.layers.values()].find((e) => e.d.kind === 'raster' && Array.isArray(e.d.tiles) && e.d.tiles[0] === d.tiles[0]) : null;
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
          const fp = d.style === 'footprint'; // scene outlines stay in the background: thin, dashed, half transparent
          map.addLayer({ id: 'F-' + d.id, type: 'fill', source: src, filter: IS_POLY, paint: { 'fill-color': color, 'fill-opacity': fillOp } }, before);
          map.addLayer({ id: 'S-' + d.id, type: 'line', source: src, filter: ['any', IS_POLY, IS_LINE],
            paint: { 'line-color': color, 'line-width': ['case', IS_LINE, 2.2, d.style === 'boundary' ? 1.2 : fp ? 0.9 : 1.1], 'line-opacity': fp ? 0.6 : 0.9,
              ...(fp ? { 'line-dasharray': [3, 2] } : {}) } }, before);
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
    return `<div class="pp-head">${layerGlyph(entry)}<b>${esc(layerName(entry.d))}</b>${nav}</div>
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
    const sub = P && P.label && !String(d.title).toLowerCase().includes(String(P.label).toLowerCase()) ? P.label : ''; // no repeats
    const head = `<div class="ml-t"><span>${esc(layerName(d))}</span>${sub ? `<small>${esc(sub)}</small>` : ''}</div>`;
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
      return `<div class="ml-i"><div class="ml-r">${layerGlyph(e)}<span>${esc(layerName(d))}</span></div>${img}</div>`;
    }
    if (P && P.label) return `<div class="ml-i">${head}<div class="ml-r">${layerGlyph(e)}one value</div></div>`;
    return `<div class="ml-i"><div class="ml-r">${layerGlyph(e)}<span>${esc(layerName(d))}</span></div></div>`;
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
  // phones start with the legend folded (the map is small there); a choice the user made is kept
  if (local.get('yf_legend') === '0' || (local.get('yf_legend') == null && innerWidth <= 720)) { $('#mapLegend').classList.add('closed'); $('#mlToggle').setAttribute('aria-expanded', 'false'); }

  function renderLayerList() {
    renderLegend();
    const list = $('#layerList'); const n = state.layers.size;
    $('#nLayers').textContent = n ? `${n}` : '';
    $('#nLayersBadge').textContent = n; $('#nLayersBadge').hidden = !n;
    if (!n) { list.innerHTML = '<div class="muted small">Layers that Y-finder adds appear here.</div>'; return; }
    list.innerHTML = [...state.layers.values()].reverse().map((e) => {
      const d = e.d;
      // image layers have nothing to download: an empty slot keeps the buttons in columns
      const dl = d.kind === 'geojson' ? `<a href="${esc(d.url)}" download="${esc((d.title || 'layer').replace(/[^\w-]+/g, '_'))}.geojson" title="Download GeoJSON" aria-label="Download GeoJSON">${ICON.dl}</a>` : '<span class="lgap" aria-hidden="true"></span>';
      const src = String(d.source || '').replace(/\s*\(.*?\)\s*$/, '');
      const k = geomKind(e); const noun = GEOM_NOUN[k];
      const what = k === 'image' ? 'Image tiles' : k === 'scope' ? '' : k === 'boundary' && d.n === 1 ? 'Boundary' : d.n && noun ? `${Number(d.n).toLocaleString()} ${noun[d.n === 1 ? 0 : 1]}` : '';
      const sub = [d.date && !String(d.title).includes(d.date) ? d.date : '', what, src].filter(Boolean).join(' · ');
      const name = layerName(d);
      return `<div class="lrow${e.visible ? '' : ' off'}" data-id="${esc(d.id)}">${layerGlyph(e)}
        <div class="lmain" title="${esc(name)}${d.source ? '\n' + esc(d.source) : ''}"><div class="nm">${esc(name)}${d.session_only ? '<span class="tag" title="Downloaded to the server for this session; deleted when it ends">session</span>' : ''}</div>${sub ? `<div class="ls">${esc(sub)}</div>` : ''}</div>
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
    const m = botMsg(); const turn = convTurn(text); m.turn = state.conv.turns.length - 1;
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
      case 'layer': {
        const pill = (e) => {
          if (ev.layer.style === 'scope' || !m.pill) return;
          m.layers++;
          m.pill('map', `${e && e.fc ? thumbSVG(e.fc, e.color) : ''}<b>Map</b> ${m.layers} layer${m.layers > 1 ? 's' : ''}`, () => { fit(ev.layer.bbox); $('#layerPanel').hidden = false; });
        };
        if (!state.mapReady) { addLayer(ev.layer); pill(null); } // queued until the map loads (a replay right after a reload)
        else addLayer(ev.layer).then((e) => { if (e) pill(e); });
      } break;
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
      case 'error': m.failed = true; m.seg('err').innerHTML = `<p style="color:var(--poor)">${esc(ev.message)}</p>`; break;
      case 'done': {
        if (ev.response_id && state.conv) state.conv.rid = ev.response_id;
        if (m.act) {
          const st = $('.st', m.act); st.className = 'st ok';
          $('.lbl', m.act).textContent = `${m.tools} step${m.tools === 1 ? '' : 's'}`;
          const u = ev.usage || {}; const tok = (u.input || 0) + (u.output || 0);
          const cost = ev.cost_usd != null ? ev.cost_usd : turnCost(u, ev.model);
          const modelId = String(ev.model || '').replace(/^recorded\s*·\s*/, '');
          const c = $('.cnt', m.act);
          c.textContent = ` · ${ev.elapsed}s${tok ? ` · ${tok >= 1000 ? Math.round(tok / 1000) + 'k' : tok} tokens` : ''}${cost != null && tok ? ` · ${cost < 0.01 ? '' : 'about '}${fmtCost(cost)}` : ''}${String(ev.model || '').startsWith('recorded') ? ' · recorded' : ''}`;
          c.title = tok ? `${(u.input || 0).toLocaleString()} input tokens${u.cached ? ` (${u.cached.toLocaleString()} from cache)` : ''} and ${(u.output || 0).toLocaleString()} output tokens over ${u.steps || '?'} model calls${cost != null ? `; about $${cost.toFixed(3)} at ${modelId} list prices` : ''}` : '';
        }
        if (m.choices && m.choices.options && m.choices.options.length) {
          const box = m.seg('choices');
          m.choices.options.forEach((o) => { const b = document.createElement('button'); b.textContent = o; b.onclick = () => { box.classList.add('used'); send(o); }; box.appendChild(b); });
        }
        m.model = String(ev.model || '').replace(/^recorded\s*·\s*/, '') || undefined; // the model that answered, for the feedback row
        if (!m.fb && state.conv && !m.failed) feedbackRow(m); // a failed turn is not an answer to rate
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
    // factors without data: a pale fill and a dashed outline (at 0.18 alone they were nearly invisible)
    { selector: 'node.cov-partial', style: { 'background-opacity': 0.55, 'border-opacity': 0.7 } },
    { selector: 'node.cov-uncovered', style: { 'background-opacity': 0.3, 'border-style': 'dashed', 'border-opacity': 0.9, color: '#475569' } },
    { selector: 'node.cov-uncovered[core != 1][role != "sink"]', style: { 'border-color': '#94a3b8', 'border-width': 1.5 } },
    { selector: 'edge.cov-covered-edge', style: { opacity: 0.95 } },
    { selector: 'edge.cov-partial-edge', style: { opacity: 0.66 } },
    { selector: 'edge.cov-uncovered-edge', style: { opacity: 0.42 } },
    // links Y-finder compared on the preview data, labelled with the statistic
    { selector: 'edge.tested', style: { label: 'data(rl)', 'font-size': 10, 'font-weight': 700, color: '#00356b', 'text-background-color': '#ffffff',
      'text-background-opacity': 0.95, 'text-background-padding': 2, 'text-background-shape': 'roundrectangle', 'text-border-width': 1,
      'text-border-color': '#c3cfdf', 'text-border-opacity': 1, 'line-color': '#286dc0', 'target-arrow-color': '#286dc0', width: 2.6, opacity: 1 } },
    { selector: 'edge.indirect', style: { 'line-style': 'dashed', 'line-dash-pattern': [6, 4], 'curve-style': 'bezier' } },
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
    const tested = {};
    ((state.explore || {}).findings || []).forEach((f) => { if (f.edge && f.rho != null) tested[f.edge.join('>')] = f; });
    const lab = (f) => `${f.chart && f.chart.type === 'groups' ? 'effect' : 'ρ'} ${f.rho > 0 ? '+' : ''}${f.rho}`;
    const edges = g.edges.map((e, i) => {
      const a = cov[e.source], b = cov[e.target];
      const cls = !withCoverage ? '' : a === 'uncovered' || b === 'uncovered' ? 'cov-uncovered-edge' : a === 'partial' || b === 'partial' ? 'cov-partial-edge' : 'cov-covered-edge';
      const t = tested[e.source + '>' + e.target]; if (t) delete tested[e.source + '>' + e.target];
      return { data: { id: 'e' + i, source: e.source, target: e.target, note: e.note, rl: t ? lab(t) : '' }, classes: cls + (t ? ' tested' : '') };
    });
    const ids = new Set(g.nodes.map((n) => n.id));
    Object.values(tested).forEach((f, i) => { if (ids.has(f.edge[0]) && ids.has(f.edge[1])) edges.push({ data: { id: 'x' + i, source: f.edge[0], target: f.edge[1], note: `Compared on the previews through other factors: ${f.headline}`, rl: lab(f) }, classes: 'tested indirect' }); });
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
    $('#testedLegend').hidden = !els.some((e) => e.classes && String(e.classes).includes('tested'));
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
      const calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches; // no pulse for reduced motion
      state.cyPulse = calm ? null : setInterval(() => {
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
    // each label sits where its years are: the study window above the bar, the data's first and last year below it
    const wMid = (+X(s0) + +X(e1)) / 2; const wPos = wMid > 82 ? `right:${(100 - +X(e1)).toFixed(1)}%` : wMid < 18 ? `left:${X(s0)}%` : `left:${wMid.toFixed(1)}%;transform:translateX(-50%)`;
    const dEnd = ongoing ? 'ongoing' : String(Math.floor(d1 - 0.01)); const close = +X(d1) - +X(d0) < 22;
    const dLabels = close ? `<span style="left:${X(d0)}%">${Math.floor(d0)}${dEnd !== String(Math.floor(d0)) ? '–' + dEnd : ''}</span>`
      : `<span style="left:${X(d0)}%">${Math.floor(d0)}</span><span style="right:${(100 - +X(d1)).toFixed(1)}%">${dEnd}</span>`;
    return `<div class="tb"><div class="tb-w" style="${wPos}">Your study: ${esc(win)}</div><svg class="timebar" viewBox="0 0 100 16" preserveAspectRatio="none" aria-label="Data years versus your study window">
      <rect x="${X(d0)}" y="5" width="${Math.max(0.6, X(d1) - X(d0))}" height="6" rx="2" fill="#9db4d6"/>${ongoing ? `<rect x="${X(now)}" y="5" width="${Math.max(0, X(d1) - X(now))}" height="6" fill="#9db4d6" opacity=".35"/>` : ''}
      <rect x="${X(s0)}" y="1.5" width="${Math.max(1.4, X(e1) - X(s0))}" height="13" rx="2" fill="#00356b" fill-opacity=".1" stroke="#00356b" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>
      <div class="tb-l">${dLabels}</div></div>`;
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
    $('#matrix').innerHTML = `<table class="matrix"><thead><tr><th>Dataset</th><th>Area</th><th>Time</th><th>Detail</th><th title="Overall fit: the weakest of area, time and detail">Fit</th><th>Access</th></tr></thead><tbody>${
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
      html += `<div class="group-h">${esc(name)}<span class="gcount">${g.length}</span></div>`;
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
          <div class="more" hidden><div class="well">
            ${timeBar(p)}
            ${reasons ? `<ul class="reasons">${reasons}</ul>` : ''}
            ${p.why ? `<div class="why">${esc(p.why)}</div>` : ''}
            ${p.limitations ? `<div class="lim"><b>Limits:</b> ${esc(p.limitations)}</div>` : ''}
            ${p.yale_note ? `<div class="yale-note">${esc(p.yale_note)}</div>` : ''}
            ${p.factors && p.factors.length ? `<div class="factors">${p.factors.map((f) => `<span>${esc(nodeLabel(f))}</span>`).join('')}</div>` : ''}
            </div><div class="row">${url ? `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener">Source</a>` : ''}<button class="btn" data-act="hb">How to access</button><button class="btn" data-act="cov">Coverage</button>${p.stac_collection ? '<button class="btn" data-act="preview">Preview</button>' : ''}${p.map && (state.previews[p.ref] || {}).status !== 'ready' ? '<button class="btn" data-act="map" title="Draw its layers on the map">Map</button>' : ''}<button class="btn" data-act="ask">Ask</button>
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
      (ps.length ? `<div class="data-foot"><button class="btn primary" id="btnGuide" title="One document with the scope, every dataset and its fit, methods, next steps and what the previews showed: print it, save it as a PDF or copy it as Markdown">Download guide</button><button class="btn" id="btnWatch" title="Search again on a schedule and post updates to a private link">Keep looking for me</button>
        <button class="btn" id="btnConsult" title="One-page summary for a YCGS consultant">Ask a YCGS consultant</button></div>` : '');
    const bg = $('#btnGuide'); if (bg) bg.onclick = guideOpen;
    const bw = $('#btnWatch'); if (bw) bw.onclick = watchDialog;
    const bc = $('#btnConsult'); if (bc) bc.onclick = consultDialog;
  }
  $('#hidePoor').onchange = renderData;

  // ------------------------------------------------------------------ feedback: a Yes or No under each answer
  // Stored on the server without the conversation (the question's first 300 characters only), to build the
  // evaluation set from real use.
  function feedbackRow(m) {
    // which turn this row sits under is fixed now: a restored conversation has a row under every answer
    const c = state.conv || {}; const turns = c.turns || [];
    const turn = m.turn != null ? m.turn : Math.max(0, turns.length - 1);
    // one id per vote: a No and the comment that follows it are two rows with the same id
    m.fbInfo = { id: Math.random().toString(36).slice(2, 12) + Date.now().toString(36), turn,
      question: String((turns[turn] || {}).q || c.title || '').slice(0, 300), model: m.model || state.model, demo: c.demo || null };
    m.fb = m.seg('fb');
    m.fb.innerHTML = `<span>Was this useful?</span><button type="button" data-v="up">Yes</button><button type="button" data-v="down">No</button>`;
    $$('button', m.fb).forEach((b) => { b.onclick = () => sendFeedback(m, b.dataset.v); });
  }
  async function sendFeedback(m, vote, comment) {
    const body = { vote, comment: comment || '', ...m.fbInfo, sid: state.sid };
    try {
      const r = await fetch('api/feedback', { method: 'POST', headers: headers(), body: JSON.stringify(body) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `Server error ${r.status}`);
      if (vote === 'down' && comment == null) {
        m.fb.innerHTML = `<span>Thanks. What was missing?</span><input type="text" maxlength="500" placeholder="One line is enough" aria-label="What was missing"><button type="button">Send</button>`;
        const inp = $('input', m.fb); const go = $('button', m.fb);
        go.onclick = () => { if (inp.value.trim()) sendFeedback(m, 'down', inp.value.trim()); else m.fb.innerHTML = '<span>Thanks.</span>'; };
        inp.onkeydown = (e) => { if (e.key === 'Enter') go.click(); };
        inp.focus();
      } else m.fb.innerHTML = '<span>Thanks.</span>';
    } catch (err) { m.fb.innerHTML = `<span>${esc(err.message || err)}</span>`; }
  }

  // ------------------------------------------------------------------ the data guide: one document to keep, print or send
  function guideMarkdown() {
    const g = state.graph || {}; const s = state.scope || {}; const ps = (state.picks || []).slice();
    const order = { core: 0, supporting: 1, alternative: 2 }; ps.sort((a, b) => (order[a.role] ?? 1) - (order[b.role] ?? 1));
    const x = state.explore || {}; const fs = x.findings || []; const sy = x.synthesis;
    const cites = [];
    ((state.conv || {}).turns || []).forEach((t) => (t.ev || []).forEach((e) => { if (e.type === 'citations') (e.items || []).forEach((k) => { if (safeUrl(k.url) && !cites.some((o) => o.url === k.url)) cites.push(k); }); }));
    const core = (g.nodes || []).filter((n) => n.core);
    const cov = { covered: 'data found', partial: 'partly covered', none: 'no data yet' };
    const dim = (p, k) => p.fit && p.fit[k] && p.fit[k].label !== 'unknown' ? p.fit[k].reason : '';
    const today = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
    const L = [];
    L.push(`# Data guide: ${g.question || (state.conv || {}).title || 'your question'}`, '');
    L.push(`Prepared with Y-finder on ${today}. Y-finder is a prototype: confirm access routes with YCGS before relying on them.`, '');
    L.push('## Scope', '');
    L.push(`- Area: ${s.extent_name || 'not set'}`);
    L.push(`- Years: ${s.time_start || '?'} to ${s.time_end || '?'}`);
    L.push(`- Detail: ${s.spatial_resolution_m ? s.spatial_resolution_m + ' m' : 'reporting units'}; unit of analysis: ${s.unit_of_analysis || 'not set'}`);
    if ((s.assumed || []).length) L.push(`- Defaults Y-finder chose, not yet confirmed: ${s.assumed.join(', ')}`);
    L.push('');
    if (core.length) { L.push('## Factors behind the question', ''); core.forEach((n) => L.push(`- ${n.label}${n.observable ? ` (measured by ${n.observable})` : ''}: ${cov[n.data_coverage] || 'no data yet'}`)); L.push(''); }
    if (ps.length) {
      L.push('## Data', '');
      ps.forEach((p) => {
        const reasons = ['extent', 'time', 'resolution'].map((k) => dim(p, k)).filter(Boolean);
        const url = safeUrl(p.url);
        L.push(`### ${p.title}${p.data_type === 'table' ? ' (table)' : ''}`);
        L.push(`${[p.source, p.resolution, p.time].filter(Boolean).join(' · ')}${url ? `  \n<${url}>` : ''}`);
        const fitWord = { good: 'fits your scope', partial: 'partly fits your scope', poor: 'a poor fit for your scope' }[(p.fit || {}).overall] || 'not checked';
        const lc = (t) => t.charAt(0).toLowerCase() + t.slice(1);
        L.push(`- Fit: ${fitWord}${p.verified === 'unverified' ? '; not verified (no search returned this source)' : ''}${reasons.length ? ' (' + reasons.map(lc).join('; ') + ')' : ''}`);
        if (p.fit && p.fit.access && p.fit.access.reason) L.push(`- Access: ${p.fit.access.reason}`);
        if (p.why) L.push(`- Why: ${p.why}`);
        if (p.limitations) L.push(`- Limits: ${p.limitations}`);
        if (p.yale_note) L.push(`- At Yale: ${p.yale_note}`);
        const pv = (state.previews || {})[p.ref]; if (pv && pv.note) L.push(`- Preview: ${pv.note}`);
        L.push('');
      });
    }
    const ms = state.methods || [];
    if (ms.length) { L.push('## Methods', ''); ms.forEach((m) => L.push(`- ${m.name}${m.tools ? ` (${m.tools})` : ''}${m.why ? `: ${m.why}` : ''}`)); L.push(''); }
    if ((state.nextSteps || []).length) { L.push('## Next steps', ''); state.nextSteps.forEach((t, i) => L.push(`${i + 1}. ${t}`)); L.push(''); }
    if (fs.length) {
      L.push('## What the previews showed', '');
      if (sy && sy.summary) L.push(sy.summary.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, '(finding $1)'), '');
      fs.forEach((f, i) => L.push(`${i + 1}. ${f.headline}${f.detail ? ` ${f.detail}` : ''}`));
      if (sy && sy.caveat) L.push('', `Careful: ${sy.caveat}`);
      L.push('', 'These findings come from the map previews, which are samples, not from the full datasets. A correlation across places is a lead to check, not a test of the link.', '');
    }
    if (cites.length) { L.push('## Sources read', ''); cites.slice(0, 15).forEach((k) => L.push(`- ${k.title || k.url}: <${safeUrl(k.url)}>`)); L.push(''); }
    return L.join('\n');
  }
  function guideOpen() {
    const text = guideMarkdown(); const q = (state.graph || {}).question || '';
    const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(q ? 'Data guide: ' + q.slice(0, 60) : 'Data guide')}</title>
<style>
  body { margin: 0; background: #e9edf2; font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #1d2433; font-size: 14px; line-height: 1.5; }
  .bar { max-width: 820px; margin: 16px auto 0; padding: 0 16px; display: flex; gap: 8px; justify-content: flex-end; }
  .bar button { font: inherit; padding: 7px 14px; border-radius: 8px; border: 1px solid #c3cfdf; background: #fff; cursor: pointer; }
  .bar button.primary { background: #00356b; color: #fff; border-color: #00356b; }
  .page { max-width: 820px; margin: 12px auto 32px; background: #fff; padding: 36px 44px; border-radius: 12px; box-shadow: 0 2px 6px rgba(20,30,50,.12), 0 8px 24px rgba(20,30,50,.10); }
  h1 { font-size: 22px; line-height: 1.25; color: #00356b; margin: 0 0 6px; }
  h2 { font-size: 15px; color: #00356b; margin: 22px 0 6px; padding-bottom: 4px; border-bottom: 1px solid #e3e7ee; }
  h3 { font-size: 14px; margin: 14px 0 2px; }
  p, ul, ol { margin: 4px 0 8px; } li { margin: 2px 0; } a { color: #286dc0; word-break: break-all; }
  @media (max-width: 600px) { .page { padding: 20px 16px; margin: 8px; } }
  @media print { body { background: #fff; } .bar { display: none; } .page { box-shadow: none; margin: 0; padding: 0; max-width: none; border-radius: 0; } }
</style></head><body>
<div class="bar"><button type="button" id="copy">Copy as Markdown</button><button type="button" class="primary" onclick="print()">Print or save as PDF</button></div>
<div class="page">${md(text)}</div><pre id="src" hidden>${esc(text)}</pre>
<script>document.getElementById('copy').onclick = function () { var t = document.getElementById('src').textContent; var b = this; navigator.clipboard.writeText(t).then(function () { b.textContent = 'Copied'; }, function () { b.textContent = 'Select the text and copy it'; }); };</script>
</body></html>`;
    // a Blob URL: the tab gets a real address, so the browser's Save, Print and Reload work on it
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
    const w = window.open(url, '_blank'); if (!w) { toast('Allow pop-ups for this site to open the guide.'); return; }
  }

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
    if (it.status === 'pending') {
      // Y-finder's own browser reports each step: waiting, opening the site, clicking Download, downloading, reading
      const globe = it.browser ? '<svg class="pv-globe" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M1.8 8h12.4M8 1.8c2 2.2 2 10.2 0 12.4M8 1.8c-2 2.2-2 10.2 0 12.4" fill="none" stroke="currentColor" stroke-width="1.1"/></svg>' : '';
      return `<span class="pv-dot run"></span>${globe}<span class="muted pv-step">${esc(it.step || 'Preparing a map preview...')}</span>`;
    }
    if (it.status !== 'ready') return `<span class="pv-dot"></span><span class="muted">No map preview: ${esc(it.note || '')}</span>`;
    const csv0 = it.csv ? ` <a class="pv-a" href="api/session/${encodeURIComponent(state.sid)}/sample/${encodeURIComponent(it.csv)}" download>CSV</a>` : '';
    const open = state.pvOpen.has(ref);
    const tblBtn = it.table ? ` <button class="pv-a" data-pvact="table" data-ref="${esc(ref)}">${open ? 'Hide table' : 'View table'}</button>` : '';
    const extra = `${sparkSVG(it.series)}${open ? tableHTML(it.table) : ''}`;
    if (it.kind === 'table') return `<span class="pv-dot ok"></span><span>Table preview</span><span class="muted"> · ${esc(it.note || '')}</span>${tblBtn}${csv0}${extra}`;
    const mine = pvLayers(it);
    if (!mine.length) return `<span class="pv-dot ok"></span><span class="muted">${esc(it.note || 'Already on the map')}</span>`;
    const ids = mine.map((l) => l.id); const vis = ids.some((id) => state.layers.get(id)?.visible);
    if (!(it.layers || []).length) { // the data is a layer drawn earlier (by Y-finder or another card): name it, show or hide it
      const name = mine[0].title || 'a layer on the map';
      return `<span class="pv-dot ok"></span><span>On map</span><span class="muted"> · as “${esc(name)}” in Layers</span>
        <button class="pv-a" data-pvact="toggle" data-ref="${esc(ref)}">${vis ? 'Hide' : 'Show'}</button>`;
    }
    const csv = it.csv ? ` <a class="pv-a" href="api/session/${encodeURIComponent(state.sid)}/sample/${encodeURIComponent(it.csv)}" download>CSV</a>` : '';
    return `<span class="pv-dot ok"></span><span>${it.kind === 'slice' ? 'Sample on map' : 'On map'}</span><span class="muted"> · ${esc(it.note || '')}</span>
      <button class="pv-a" data-pvact="toggle" data-ref="${esc(ref)}">${vis ? 'Hide' : 'Show'}</button>${tblBtn}${csv}${extra}`;
  }
  // a card's layers: its own preview, or the layer drawn earlier that already holds its data ("existing")
  const pvLayers = (it) => ((it.layers || []).length ? it.layers : (it.existing || []));
  function refreshPreviewLines() {
    $$('.pv[data-pv]').forEach((el) => { el.innerHTML = pvLine(el.dataset.pv); });
    if (!$('#insEmpty').hidden) renderInsights(); // the empty state says why nothing is explored, which depends on the previews
    bindPreviewButtons();
  }
  function bindPreviewButtons() {
    $$('[data-pvact=table]').forEach((b) => { b.onclick = (e) => {
      e.stopPropagation(); const r = b.dataset.ref; state.pvOpen.has(r) ? state.pvOpen.delete(r) : state.pvOpen.add(r); refreshPreviewLines();
    }; });
    $$('[data-pvact=toggle]').forEach((b) => { b.onclick = (e) => {
      e.stopPropagation(); const it = state.previews[b.dataset.ref]; if (!it) return;
      const mine = pvLayers(it);
      const ids = mine.map((l) => l.id).filter((id) => state.layers.has(id));
      const show = !ids.some((id) => state.layers.get(id).visible);
      ids.forEach((id) => { if (state.layers.get(id).visible !== show) toggleLayer(id); });
      if (show && mine[0] && mine[0].bbox) fit(state.scope?.bbox || mine[0].bbox);
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
        // a layer drawn earlier that the page no longer has (a failed load, a restart) is drawn again from the server's copy
        (it.layers || []).concat(it.existing || []).forEach((l) => { if (!applied.has(l.id)) { applied.add(l.id); fresh.push(l); } });
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
      maybeExplore(); // explore what is ready while the user looks at it
      if (j.running && Date.now() - t0 < 600000) state.pvTimer = setTimeout(() => tick(false), 2000); // the browser can take minutes
    };
    tick(true);
  }

  // ------------------------------------------------------------------ Y-finder explores the previews (Insights tab and map pill)
  // The server reads the preview layers, guided by the question and graph, one step at a time; the user can pause or stop it.
  const xLive = (st) => st === 'running' || st === 'waiting' || st === 'paused';
  const xAuto = () => local.get('yf_explore_auto') !== '0';
  const INS_EMPTY = 'When map previews of your data are ready, Y-finder explores them here, guided by your question and graph: where values are high, which factors vary together, and what to check next. You can pause or stop it at any time.';
  const xAnyReady = () => Object.values(state.previews || {}).some((it) => it.status === 'ready' && ((it.layers || []).some((l) => l.kind === 'geojson') || it.series));
  const xUrl = () => `api/session/${encodeURIComponent(state.sid)}/explore`;
  async function xPost(action) {
    if (!state.sid) return null;
    if (state.restoring) await state.restoring;
    try {
      const r = await fetch(xUrl(), { method: 'POST', headers: headers(), body: JSON.stringify({ action }) });
      const j = await r.json().catch(() => ({}));
      if (j.error) toast(j.error);
      if (j.status) { if (action === 'start') state.xSeen = false; xApply(j); }
      if (xLive(j.status) || action === 'stop') xWatch(); // after Stop, until the step under way has finished
      return j;
    } catch { toast('Could not reach the server.'); return null; }
  }
  async function maybeExplore() {
    if (!state.sid || state.xAsking || !xAuto() || !xAnyReady()) return;
    const st = state.explore && state.explore.status;
    if (st && st !== 'idle' && st !== 'stale') return;
    state.xAsking = true;
    try {
      if (!st) { // first look: the server may already be exploring this session
        const j = await getJSON(xUrl()).catch(() => null);
        if (!j) return;
        xApply(j);
        if (j.status !== 'idle' && j.status !== 'stale') { if (xLive(j.status)) xWatch(); return; }
      }
      await xPost('start');
    } finally { state.xAsking = false; }
  }
  function xWatch() {
    clearTimeout(state.xTimer); const sid = state.sid; const gen = state.xGen = (state.xGen || 0) + 1;
    const tick = async () => {
      if (gen !== state.xGen || sid !== state.sid) return;
      let j = null; try { j = await getJSON(xUrl()); } catch { j = null; }
      if (gen !== state.xGen || sid !== state.sid) return;
      if (j) xApply(j);
      const ending = j && j.status === 'stopped' && !/^Stopped/.test(j.step || '') && (state.xEnd = (state.xEnd || 0) + 1) < 30;
      if (!j || j.status === 'running' || j.status === 'waiting' || ending) state.xTimer = setTimeout(tick, j ? 1800 : 5000);
      else state.xEnd = 0;
    };
    state.xTimer = setTimeout(tick, 900);
  }
  const xEdgeKey = () => ((state.explore || {}).findings || []).filter((f) => f.edge).map((f) => f.edge.join('>') + f.rho).join('|');
  function xApply(j) {
    const before = ((state.explore || {}).findings || []).length; const edges = xEdgeKey();
    state.explore = j;
    renderInsights();
    if (xEdgeKey() !== edges && state.graph) renderGraph();
    const n = (j.findings || []).length;
    if (before === 0 && n > 0 && !$('#tab-insights').classList.contains('on')) toast('Y-finder found patterns in your data. See the Insights tab.');
    if (state.conv && n && (j.status === 'done' || j.status === 'stopped') && state.conv.explore !== j) { state.conv.explore = j; convSave(); }
  }
  const X_STATUS = { running: 'Exploring your data', waiting: 'Exploring your data', paused: 'Paused', done: 'Done', stopped: 'Stopped',
    stale: 'Your data changed since this exploration', idle: 'Ready to explore', off: 'Exploration is off on this server' };
  const PAUSE_SVG = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="3.5" y="3" width="3" height="10" rx="1" fill="currentColor"/><rect x="9.5" y="3" width="3" height="10" rx="1" fill="currentColor"/></svg>';
  const PLAY_SVG = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M5 3.2v9.6L13 8z" fill="currentColor"/></svg>';
  function renderInsights() {
    const x = state.explore || { status: 'idle' }; const fs = x.findings || []; const live = xLive(x.status);
    $('#nIns').hidden = !fs.length; $('#nIns').textContent = fs.length;
    $('.tabs [data-tab=insights]').classList.toggle('running', x.status === 'running' || x.status === 'waiting');
    const ready = xAnyReady();
    $('#insEmpty').hidden = !(x.status === 'idle' && !fs.length);
    const onlyImages = !ready && Object.values(state.previews || {}).some((it) => it.status === 'ready');
    $('#insEmpty').textContent = onlyImages
      ? 'The previews in this conversation are images and map tiles. Y-finder shows them but does not measure them. Findings appear here when a preview has values: points, areas or a table.'
      : INS_EMPTY;
    let ctl = '';
    if (live) ctl = (x.status === 'paused' ? '<button class="btn primary" data-xa="resume">Resume</button>' : '<button class="btn" data-xa="pause">Pause</button>') + '<button class="btn" data-xa="stop">Stop</button>';
    else if (x.status === 'done' || x.status === 'stopped' || x.status === 'stale') ctl = '<button class="btn" data-xa="start">Explore again</button>';
    else if (x.status === 'idle' && ready) ctl = '<button class="btn primary" data-xa="start">Explore now</button>';
    const plan = x.plan || [];
    const showHead = x.status !== 'idle' || ready;
    $('#insHead').innerHTML = !showHead ? '' : `<div class="xhead ${esc(x.status)}">
      <div class="xh-row"><span class="xdot ${esc(x.status)}"></span><b>${esc(X_STATUS[x.status] || '')}</b><span class="xh-ctl">${ctl}</span></div>
      ${x.step && x.status !== 'idle' ? `<div class="xh-step">${esc(x.step)}</div>` : ''}
      ${plan.length ? `<details class="xplan"${state.xPlanOpen ? ' open' : ''}><summary>Plan from your question and graph · ${x.done || 0} of ${x.total || plan.length} steps</summary>
        <ol>${plan.map((p) => `<li class="${esc(p.status)}"><span class="pi" aria-hidden="true"></span><span>${esc(p.title)}${p.why ? `<span class="muted"> · ${esc(p.why)}</span>` : ''}</span></li>`).join('')}</ol></details>` : ''}
      <label class="xauto"><input type="checkbox" id="xAutoBox"${xAuto() ? ' checked' : ''}> Explore automatically when previews are ready</label></div>`;
    $$('#insHead [data-xa]').forEach((b) => { b.onclick = () => xPost(b.dataset.xa); });
    const det = $('#insHead details'); if (det) det.ontoggle = () => { state.xPlanOpen = det.open; };
    const ab = $('#xAutoBox'); if (ab) ab.onchange = () => { local.set('yf_explore_auto', ab.checked ? '1' : '0'); if (ab.checked) maybeExplore(); };
    const sy = x.synthesis;
    $('#insSyn').innerHTML = sy && sy.summary ? `<section class="xsyn" aria-label="What the findings suggest">
      <h3>What this suggests for your question</h3>
      <p>${esc(sy.summary).replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_m, g) => g.split(/\s*,\s*/).map((k) => `<button class="xcite" data-f="${k}">${k}</button>`).join(''))}</p>
      ${(sy.next || []).length ? `<div class="xs-sub">Next checks</div><ul>${sy.next.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
      ${sy.caveat ? `<div class="lim"><b>Careful:</b> ${esc(sy.caveat)}</div>` : ''}
      <div class="xs-by">Written by the AI model from the findings below; every number in them was computed by Y-finder.</div></section>` : '';
    $$('#insSyn .xcite').forEach((b) => { b.onclick = () => { const c = $(`#insList [data-n="${b.dataset.f}"]`); if (c) { c.scrollIntoView({ behavior: 'smooth', block: 'center' }); c.classList.add('flash'); setTimeout(() => c.classList.remove('flash'), 1400); } }; });
    const nodeLabel = (id) => ((state.graph?.nodes || []).find((n) => n.id === id) || {}).label || id;
    $('#insList').innerHTML = fs.map((f, i) => {
      const tag = f.kind === 'relate'
        ? (f.edge ? `<span class="xedge" title="The link in your graph that this checks">${esc(nodeLabel(f.edge[0]))} → ${esc(nodeLabel(f.edge[1]))}</span>` : '<span class="xedge">Two layers of the outcome</span>')
          + (f.rho != null ? `<span class="xrho ${Math.abs(f.rho) >= 0.3 ? 'on' : ''}" title="${f.chart && f.chart.type === 'groups' ? 'Rank-biserial effect: inside versus outside' : 'Spearman rank correlation'}">${f.chart && f.chart.type === 'groups' ? 'effect' : 'ρ'} ${f.rho > 0 ? '+' : ''}${esc(f.rho)}</span>` : '')
        : (f.factors || []).slice(0, 1).map((id) => `<span class="xfac">${esc(nodeLabel(id))}</span>`).join('');
      return `<article class="xcard ${esc(f.kind)}" data-n="${i + 1}">
        <div class="xc-top"><span class="xnum">${i + 1}</span>${tag}${f.coverage_warning ? '<span class="badge partial" title="The preview is not an even sample over time">Uneven sample</span>' : ''}</div>
        <h4>${esc(f.headline)}</h4>
        ${xChart(f.chart)}
        <p class="xc-d${state.xOpen && state.xOpen.has(f.id) ? ' open' : ''}">${esc(f.detail)}</p>${(f.detail || '').length > 150 ? `<button class="linkbtn xmore" data-x="more">${state.xOpen && state.xOpen.has(f.id) ? 'Less' : 'More'}</button>` : ''}
        <div class="row">${(f.focus || {}).layers?.length || (f.focus || {}).points?.length ? '<button class="btn" data-x="map">Show on map</button>' : ''}<button class="btn" data-x="ask">Ask about this</button></div>
      </article>`;
    }).join('');
    $$('#insList .xcard').forEach((c) => {
      const f = fs[+c.dataset.n - 1]; if (!f) return;
      const mb = c.querySelector('[data-x=map]'); if (mb) mb.onclick = () => xFocus(f, +c.dataset.n);
      const more = c.querySelector('[data-x=more]');
      if (more) more.onclick = () => { state.xOpen = state.xOpen || new Set(); const d = c.querySelector('.xc-d'); const open = d.classList.toggle('open');
        open ? state.xOpen.add(f.id) : state.xOpen.delete(f.id); more.textContent = open ? 'Less' : 'More'; };
      c.querySelector('[data-x=ask]').onclick = () => { $('#q').value = `About finding ${c.dataset.n} ("${f.headline}"): `; switchTab('chat'); $('#q').focus(); autosize(); };
    });
    $('#insFoot').innerHTML = fs.length || (x.skipped || []).length ? `<div class="xfoot">Computed on the map previews, which are samples, not on the full datasets.
      A correlation across places is a lead to check, not a test of the link.${(x.skipped || []).length ? `<br>Not measured: ${x.skipped.map(esc).join('; ')}.` : ''}</div>` : '';
    // the pill on the map: visible while exploring, and once at the end until the user looks
    const pill = $('#xPill'); const showPill = live || ((x.status === 'done' || x.status === 'stopped') && fs.length && !state.xSeen);
    pill.hidden = !showPill || state.gisMode;
    if (showPill) {
      pill.className = 'xpill ' + x.status;
      $('#xPillText').textContent = live ? `${x.status === 'paused' ? 'Paused' : 'Exploring'}${fs.length ? ` · ${fs.length} found` : ''} · ${x.step || ''}`
        : `${fs.length} finding${fs.length === 1 ? '' : 's'} in your data · View`;
      $('#xPillPause').hidden = !live; $('#xPillStop').hidden = !live;
      $('#xPillPause').innerHTML = x.status === 'paused' ? PLAY_SVG : PAUSE_SVG;
      $('#xPillPause').title = x.status === 'paused' ? 'Resume' : 'Pause'; $('#xPillPause').setAttribute('aria-label', $('#xPillPause').title);
    }
  }
  $('#xPillText').onclick = () => { state.xSeen = true; if (document.body.classList.contains('collapsed')) $('#btnCollapse').click(); switchTab('insights'); renderInsights(); };
  $('#xPillPause').onclick = () => xPost(state.explore && state.explore.status === 'paused' ? 'resume' : 'pause');
  $('#xPillStop').onclick = () => xPost('stop');
  function xMarkers(pts) {
    (state.xMk || []).forEach((m) => m.remove()); state.xMk = [];
    (pts || []).slice(0, 8).forEach((p, i) => {
      if (!Number.isFinite(+p[0]) || !Number.isFinite(+p[1])) return;
      const el = document.createElement('div'); el.className = 'xmk';
      el.innerHTML = `<b>${i + 1}</b>${p[2] ? `<span>${esc(p[2])}</span>` : ''}`; el.title = p[2] || '';
      state.xMk.push(new maplibregl.Marker({ element: el, anchor: 'left' }).setLngLat([+p[0], +p[1]]).addTo(map));
    });
  }
  async function xFocus(f) {
    const fo = f.focus || {}; const ids = new Set(fo.layers || []);
    for (const d of fo.add || []) {
      if (state.layers.has(d.id)) continue;
      const t = state.conv ? convTurn(null) : null; const ev = { type: 'layer', layer: d }; if (t) { convRecord(t, ev); convSave(); }
      await addLayer(d);
    }
    (fo.hide || []).forEach((id) => { const e = state.layers.get(id); if (e && e.visible) toggleLayer(id); });
    // only the finding's layers stay on (the study box, boundaries and imagery too), so it reads on the map; Layers turns others back on
    [...state.layers.entries()].forEach(([id, e]) => { if (e.visible && !ids.has(id) && e.d.kind === 'geojson' && e.d.style !== 'scope' && e.d.style !== 'boundary') toggleLayer(id); });
    ids.forEach((id) => { const e = state.layers.get(id); if (e && !e.visible) toggleLayer(id); });
    xMarkers(fo.points || []);
    if (fo.bbox) fit(fo.bbox); else if (state.scope && state.scope.bbox) fit(state.scope.bbox);
    if (state.gisMode) setMode(false);
    refreshPreviewLines();
    if (innerWidth <= 720 && !document.body.classList.contains('collapsed')) $('#btnCollapse').click(); // phones: show the map
  }

  // small charts on the finding cards (inline SVG, no library)
  function xChart(c) {
    if (!c || !c.type) return '';
    const W = 300, H = 118, L = 40, R = 10, T = 10, B = 26;
    const f = (v) => fmtNum(+v);
    const cut = (t, n) => (String(t).length > n ? String(t).slice(0, n - 1).trimEnd() + '…' : String(t));
    const frame = (inner, label) => `<figure class="xchart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">${inner}</svg></figure>`;
    const axis = `<line x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}" class="ax"/><line x1="${L}" y1="${T}" x2="${L}" y2="${H - B}" class="ax"/>`;
    const sx = (lo, hi) => (v) => L + ((v - lo) / Math.max(1e-12, hi - lo)) * (W - L - R);
    const sy = (lo, hi) => (v) => H - B - ((v - lo) / Math.max(1e-12, hi - lo)) * (H - T - B);
    if (c.type === 'scatter') {
      // skewed values: log when all are positive (server's choice), square root when many are zero
      const skew = (a) => { const s = a.map(Number).sort((p, q) => p - q); const med = s[s.length >> 1]; return s[0] >= 0 && (med === 0 || s[s.length - 1] / med > 25); };
      const mode = (lg, a) => (lg ? 'log' : skew(a) ? 'sqrt' : '');
      const mx = mode(c.logx, c.x || []), my = mode(c.logy, c.y || []);
      const tf = (m) => (m === 'log' ? (v) => Math.log10(Math.max(+v, 1e-9)) : m === 'sqrt' ? (v) => Math.sqrt(Math.max(+v, 0)) : (v) => +v);
      const xs = (c.x || []).map(tf(mx)), ys = (c.y || []).map(tf(my)); if (xs.length < 3) return '';
      const T2 = T + 10; const sy2 = (lo, hi) => (v) => H - B - ((v - lo) / Math.max(1e-12, hi - lo)) * (H - T2 - B);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys); const X = sx(x0, x1), Y = sy2(y0, y1);
      const inv = (m, v) => (m === 'log' ? 10 ** v : m === 'sqrt' ? v * v : v);
      const note = (m) => (m === 'log' ? ' (log scale)' : m === 'sqrt' ? ' (square-root scale)' : '');
      const dots = xs.map((v, i) => `<circle cx="${X(v).toFixed(1)}" cy="${Y(ys[i]).toFixed(1)}" r="2.3"/>`).join('');
      const axis2 = `<line x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}" class="ax"/><line x1="${L}" y1="${T2}" x2="${L}" y2="${H - B}" class="ax"/>`;
      return frame(`${axis2}<g class="dots">${dots}</g>
        <text x="${L}" y="${H - B + 11}" class="tk">${esc(f(inv(mx, x0)))}</text><text x="${W - R}" y="${H - B + 11}" class="tk" text-anchor="end">${esc(f(inv(mx, x1)))}</text>
        <text x="${L - 4}" y="${H - B}" class="tk" text-anchor="end">${esc(f(inv(my, y0)))}</text><text x="${L - 4}" y="${T2 + 6}" class="tk" text-anchor="end">${esc(f(inv(my, y1)))}</text>
        <text x="${(L + W - R) / 2}" y="${H - 3}" class="al" text-anchor="middle">${esc(cut(c.xl + note(mx), 62))}</text>
        <text x="${L}" y="${T + 1}" class="al">↑ ${esc(cut(c.yl + note(my), 58))}</text>`, `Scatter plot of ${c.yl} against ${c.xl}`);
    }
    if (c.type === 'hist' || c.type === 'bars') {
      const items = c.type === 'hist' ? (c.bins || []).map((b) => [b[0], b[2], b[1]]) : (c.items || []);
      if (!items.length) return '';
      const mx = Math.max(...items.map((it) => +it[1]), 1); const bw = (W - L - R) / items.length; const Y = sy(0, mx);
      const peak = items.reduce((a, b) => (+b[1] > +a[1] ? b : a), items[0]);
      const bars = items.map((it, i) => `<rect x="${(L + i * bw + 1).toFixed(1)}" y="${Y(+it[1]).toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" height="${(H - B - Y(+it[1])).toFixed(1)}" class="${it === peak ? 'pk' : ''}"><title>${esc(String(it[0]))}: ${esc(f(it[1]))}</title></rect>`).join('');
      const first = c.type === 'hist' ? f(items[0][0]) : String(items[0][0]); const last = c.type === 'hist' ? f(items[items.length - 1][2]) + (c.clipped ? `+ (max ${f(c.max)})` : '') : String(items[items.length - 1][0]);
      return frame(`${axis}${bars}<text x="${L}" y="${H - B + 11}" class="tk">${esc(first)}</text><text x="${W - R}" y="${H - B + 11}" class="tk" text-anchor="end">${esc(last)}</text>
        <text x="${L - 4}" y="${T + 7}" class="tk" text-anchor="end">${esc(f(mx))}</text><text x="${L - 4}" y="${H - B}" class="tk" text-anchor="end">0</text>
        <text x="${(L + W - R) / 2}" y="${H - 3}" class="al" text-anchor="middle">${esc(c.type === 'hist' ? (c.label || '') : (c.label || ''))}${c.type === 'hist' ? ' · number of places' : ''}</text>`,
      c.type === 'hist' ? `Histogram of ${c.label}` : `${c.label}`);
    }
    if (c.type === 'line') {
      const ok = (p) => Number.isFinite(+p[0]) && Number.isFinite(+p[1]);
      const pts = (c.points || []).filter(ok); if (pts.length < 2) return '';
      const pts2 = (c.points2 || []).filter(ok); // a second line to compare (weekends against weekdays)
      const xs = pts.concat(pts2).map((p) => +p[0]), ys = pts.concat(pts2).map((p) => +p[1]);
      const X = sx(Math.min(...xs), Math.max(...xs)), y0 = Math.min(...ys), y1 = Math.max(...ys), Y = sy(y0, y1);
      const path = (a) => a.map((p, i) => `${i ? 'L' : 'M'}${X(+p[0]).toFixed(1)},${Y(+p[1]).toFixed(1)}`).join('');
      const d = path(pts) + '';
      return frame(`${axis}${pts2.length > 1 ? `<path d="${path(pts2)}" class="ln ln2"/>` : ''}<path d="${d}" class="ln"/><text x="${L}" y="${H - B + 11}" class="tk">${esc(String(xs[0]))}</text><text x="${W - R}" y="${H - B + 11}" class="tk" text-anchor="end">${esc(String(xs[xs.length - 1]))}</text>
        <text x="${L - 4}" y="${T + 7}" class="tk" text-anchor="end">${esc(f(y1))}</text><text x="${L - 4}" y="${H - B}" class="tk" text-anchor="end">${esc(f(y0))}</text>
        <text x="${(L + W - R) / 2}" y="${H - 3}" class="al" text-anchor="middle">${esc(c.label || '')}</text>`, `Trend of ${c.label}`);
    }
    if (c.type === 'groups') {
      const gs = (c.groups || []).filter((g) => (g[1] || []).length); if (gs.length < 2) return '';
      const all = gs.flatMap((g) => g[1].map(Number)); const X = sx(Math.min(...all), Math.max(...all));
      const rowH = (H - T - B) / gs.length;
      const med = (a) => { const s = a.map(Number).sort((p, q) => p - q); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
      const rows = gs.map((g, r) => { const cy = T + rowH * (r + 0.5); const m = med(g[1]);
        return `<text x="${L - 4}" y="${cy + 3}" class="tk" text-anchor="end">${esc(g[0])}</text>` + g[1].map((v, i) => `<circle cx="${X(+v).toFixed(1)}" cy="${(cy + (((i * 37) % 11) - 5) * rowH / 26).toFixed(1)}" r="2.3"/>`).join('')
          + `<line x1="${X(m).toFixed(1)}" x2="${X(m).toFixed(1)}" y1="${cy - rowH * 0.36}" y2="${cy + rowH * 0.36}" class="md"/>`; }).join('');
      return frame(`<line x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}" class="ax"/><g class="dots">${rows}</g>
        <text x="${L}" y="${H - B + 11}" class="tk">${esc(f(Math.min(...all)))}</text><text x="${W - R}" y="${H - B + 11}" class="tk" text-anchor="end">${esc(f(Math.max(...all)))}</text>
        <text x="${(L + W - R) / 2}" y="${H - 3}" class="al" text-anchor="middle">${esc(c.label || '')} · line = median</text>`, `${c.label} inside and outside`);
    }
    return '';
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
      if (e.d.kind !== 'geojson') { skipped++; continue; }
      if (e.d.style === 'footprint') continue; // scene outlines belong with the imagery, which stays in Map view
      // a readable file name, since GeoLibre names each layer after its file
      const nice = String(e.d.title || 'layer').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'layer';
      p.append('data', new URL(String(e.d.url).replace(/\.geojson$/, '') + '/' + nice + '.geojson', location.href).href);
    }
    if (state.mapReady) { // open GeoLibre where Y-finder is looking
      const c = map.getCenter(); p.set('lat', c.lat.toFixed(4)); p.set('lon', c.lng.toFixed(4)); p.set('zoom', map.getZoom().toFixed(2));
    }
    if (!state.gisFull) { p.set('layout', 'compact'); p.set('panels', 'collapsed'); }
    p.set('welcome', '0');
    return { url: 'https://web.geolibre.app/?' + p.toString(), skipped };
  }
  function loadGeoLibre() {
    const { url, skipped } = geolibreURL(); const f = $('#geolibre');
    if (f.dataset.src !== url) { f.src = url; f.dataset.src = url; }
    const local = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
    const note = local ? 'Running locally: GeoLibre cannot fetch layers from this computer. Download them from Layers and drop them in.'
      : skipped ? `${skipped} image layer${skipped === 1 ? '' : 's'} stay${skipped === 1 ? 's' : ''} in Map view; GeoLibre opens the others.` : (state.layers.size ? 'Your layers are loaded in GeoLibre.' : 'Ask Y-finder for data first, or explore GeoLibre.');
    $('#gisNote').textContent = note; $('#gisBar').title = note;
    if (state.gisMode && note !== state.gisNoteShown) { state.gisNoteShown = note; toast(note); }
  }
  function setMode(gis) {
    state.gisMode = gis; $('#modeGIS').classList.toggle('on', gis); $('#modeMap').classList.toggle('on', !gis);
    document.body.classList.toggle('gis', gis); if (!gis) state.gisNoteShown = null; // GeoLibre starts below the top bar
    $('#geolibre').hidden = !gis; $('#gisBar').hidden = !gis; $('.layers-box').hidden = gis; renderLegend();
    if (gis) $('#xPill').hidden = true; else setTimeout(renderInsights, 0); // the exploration pill belongs to Y-finder's map
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
    // the layout can change before the map has loaded (the large chat is chosen at startup): resize anyway,
    // otherwise the canvas keeps its first width and every zoom centres too far to the right
    if (!state.mapReady) { try { map.resize(); } catch { /* not ready to measure yet */ } return; }
    const c = map.getCenter(); map.resize(); map.setCenter(c);
  }
  $('#btnCollapse').onclick = () => { const c = document.body.classList.toggle('collapsed'); $('#btnCollapse').setAttribute('aria-expanded', String(!c)); requestAnimationFrame(mapResized); };
  const isWide = () => document.body.classList.contains('wide');
  function setWide(w) {
    document.body.classList.toggle('wide', w); $('#btnWide').setAttribute('aria-pressed', String(w));
    $('#btnWide').title = w ? 'Smaller chat (more map)' : 'Larger chat';
    setHist(w); // the large chat shows the conversation list beside it
    local.set('yf_wide', w ? '1' : '0');
    if (typeof restorePanelWidth === 'function') restorePanelWidth();
    requestAnimationFrame(mapResized);
    setTimeout(() => { if (state.cy) { state.cy.resize(); state.cy.fit(undefined, 18); } }, 60);
  }
  $('#btnWide').onclick = () => setWide(!isWide());

  // ------------------------------------------------------------------ drag the border between the panel and the map
  // The width is kept per mode (normal and large chat), so the Larger chat button still switches between two sizes.
  const panelKey = () => (isWide() ? 'yf_panel_w_wide' : 'yf_panel_w');
  const panelLimits = () => [340, Math.max(360, innerWidth - 300)]; // the map always keeps at least 300 px
  function setPanelWidth(px, save) {
    const [lo, hi] = panelLimits();
    if (px == null) { document.body.style.removeProperty('--panel-w'); if (save) local.set(panelKey(), ''); }
    else { const w = Math.round(Math.min(hi, Math.max(lo, px))); document.body.style.setProperty('--panel-w', w + 'px'); if (save) local.set(panelKey(), String(w)); }
    $('#panelResizer').setAttribute('aria-valuenow', String(Math.round($('.panel').getBoundingClientRect().width)));
  }
  function restorePanelWidth() { const v = parseInt(local.get(panelKey()) || '', 10); setPanelWidth(Number.isFinite(v) && v > 0 ? v : null, false); }
  {
    const rz = $('#panelResizer'); let raf = 0;
    const redraw = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { mapResized(); if (state.cy) state.cy.resize(); }); };
    rz.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault(); rz.setPointerCapture(e.pointerId); document.body.classList.add('resizing');
      const move = (ev) => { setPanelWidth(ev.clientX - 18, false); redraw(); };
      const up = () => {
        rz.removeEventListener('pointermove', move); rz.removeEventListener('pointerup', up); rz.removeEventListener('pointercancel', up);
        document.body.classList.remove('resizing'); setPanelWidth($('.panel').getBoundingClientRect().width, true); redraw();
        setTimeout(() => { if (state.cy) state.cy.fit(undefined, 18); }, 60);
      };
      rz.addEventListener('pointermove', move); rz.addEventListener('pointerup', up); rz.addEventListener('pointercancel', up);
    });
    rz.addEventListener('dblclick', () => { setPanelWidth(null, true); redraw(); });
    rz.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 96 : 32; const w = $('.panel').getBoundingClientRect().width;
      if (e.key === 'ArrowLeft') setPanelWidth(w - step, true); else if (e.key === 'ArrowRight') setPanelWidth(w + step, true);
      else if (e.key === 'Home' || e.key === 'Escape') setPanelWidth(null, true); else return;
      e.preventDefault(); redraw();
    });
    addEventListener('resize', () => { if (document.body.style.getPropertyValue('--panel-w')) restorePanelWidth(); });
  }
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
    state.explore = null; state.xSeen = false; clearTimeout(state.xTimer); state.xGen = (state.xGen || 0) + 1; xMarkers([]);
    destroyGraph();
    msgs.innerHTML = ''; $('#welcome').hidden = false; $('#nodeInfo').hidden = true; $('#attachInfo').hidden = true;
    STEPS.forEach((s) => step(s, null));
    if (state.mapReady && map.getSource('coverage')) map.getSource('coverage').setData({ type: 'FeatureCollection', features: [] });
    if (state.gisMode) setMode(false);
    renderGraph(); renderScope(); renderData(); renderInsights(); switchTab('chat');
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
      userMsg(t.q); const m = botMsg(); m.turn = c.turns.indexOf(t);
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
    if (c.explore) { state.explore = { ...c.explore, status: xLive(c.explore.status) ? 'stopped' : c.explore.status }; state.xSeen = true; renderInsights(); if (state.graph) renderGraph(); }
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
  { const pref = local.get('yf_wide'); if (innerWidth > 980 && (pref === '1' || (pref == null && innerWidth >= 1280))) setWide(true); else { restorePanelWidth(); renderHist(); } }

  // ------------------------------------------------------------------ demo cases (recorded runs, replayed, then live)
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const DEMO_THEMES = [['health', 'Health'], ['environment', 'Environment'], ['society', 'Cities and society'], ['history', 'History and culture']];
  async function loadDemos() {
    try {
      const j = await getJSON('static/demos/index.json?v=' + (window.YF_VERSION || '')); if (!j.cases || !j.cases.length) return;
      const words = (c) => [c.title, c.field, c.program, c.who, c.based_on, ...(c.shows || [])].join(' ').toLowerCase();
      const card = (c) => `<button class="demo-card" data-id="${esc(c.id)}" data-theme="${esc(c.theme || '')}" data-text="${esc(words(c))}" title="${esc(c.who + (c.program ? '\nBased on: ' + c.program : '') + '\nShows: ' + c.shows.join('; '))}">
        <div class="field">${esc(c.program ? c.program : c.field)}</div><div class="title">${esc(c.title)}</div></button>`;
      const yaleAll = j.cases.filter((c) => c.group === 'yale');
      // six Yale research cases from different fields up front; the other Yale cases and the general demos stay one click away
      const yale = yaleAll.slice(0, 6), rest = yaleAll.slice(6).concat(j.cases.filter((c) => c.group !== 'yale'));
      // a long list gets theme chips and a search box (words from the title, field, program, person and what it shows)
      const themes = DEMO_THEMES.filter(([k]) => rest.some((c) => c.theme === k));
      const finder = rest.length > 9 ? `<div class="demo-find"><div class="demo-chips" role="group" aria-label="Show demos by theme">
          <button type="button" data-t="" aria-pressed="true">All ${rest.length}</button>${themes.map(([k, label]) => `<button type="button" data-t="${k}" aria-pressed="false">${label} ${rest.filter((c) => c.theme === k).length}</button>`).join('')}</div>
          <input type="search" id="demoQ" placeholder="Find a demo: place, field or data" aria-label="Find a demo by place, field or data"></div>` : '';
      $('#demos').innerHTML = yale.length
        ? `<div class="demo-grid">${yale.map(card).join('')}</div>${rest.length ? `<details class="more-demos"><summary>More demos (${rest.length})</summary>${finder}<div class="demo-grid" id="moreGrid">${rest.map(card).join('')}</div><p class="demo-none" hidden>No demo matches. Try another word or theme.</p></details>` : ''}`
        : `<div class="demo-grid">${rest.map(card).join('')}</div>`;
      if (yale.length) $('#demoWrap .sec-h').textContent = 'Watch a demo: Yale research cases';
      $$('.demo-card').forEach((b) => { b.onclick = () => playDemo(b.dataset.id); });
      if (finder) {
        let theme = '';
        const filter = () => {
          const q = $('#demoQ').value.toLowerCase().split(/\s+/).filter(Boolean);
          let n = 0;
          $$('#moreGrid .demo-card').forEach((b) => {
            const show = (!theme || b.dataset.theme === theme) && q.every((w) => b.dataset.text.includes(w));
            b.hidden = !show; n += show;
          });
          $('.more-demos .demo-none').hidden = n > 0;
        };
        $$('.demo-chips button').forEach((b) => { b.onclick = () => {
          theme = b.dataset.t; $$('.demo-chips button').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); filter();
        }; });
        $('#demoQ').oninput = filter;
      }
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
          const m = botMsg(); const t = convTurn(st.message); m.turn = state.conv.turns.length - 1;
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
    $('#dlgBody').innerHTML = md(`**Y-finder** is a prototype geospatial data guide, built by Huan Ning as a proposal for the Yale Center for Geospatial Solutions (YCGS). It is not an official Yale or YCGS service. The [design notes](design) say how it was built and tested, what it costs, and what YCGS would need to run it.

**How it works (tri-projection).** Y-finder turns a research question into an *influence graph* of factors, agrees a *scope* with you (area, time window, resolution, unit of analysis), then searches for *data* and labels how each source fits that scope. The fit labels come from catalog metadata and simple rules, not from the language model.

**Where the data comes from.** ${state.config ? Number(state.config.catalog.counts?.total || 0).toLocaleString() : '3,000+'} catalog cards: YCGS curated cards with Yale access routes, Microsoft Planetary Computer, Google Earth Engine and its community catalog, the layers collected in [GeoLibre](https://geolibre.app/), and World Bank indicators. Live searches cover Yale Dataverse, Harvard Dataverse, Yale Library GeoBlacklight, ArcGIS Online, Data.gov, US open data portals (including Connecticut Open Data), Planetary Computer imagery and the web (DeepSeek search).

**Maps and tables.** Many factors are measured by tables (statistics, surveys, records) as well as maps; Y-finder looks for both and marks tables with a *Table* badge.

**Previews.** For each recommended dataset Y-finder draws an online map service directly, or downloads a small slice for your study area (up to 100 MB) and shows it on the map or as a table preview with a CSV. Licensed and account-only sources say why there is no preview.

**Map and GIS.** Layers you see can be downloaded as GeoJSON, or opened in [GeoLibre](https://web.geolibre.app/) with *Full GIS*. Influence graphs export to [Research Architect](https://app.researcharchitect.ai/).

**Access handbooks.** Each data source can have a handbook in the style of an Agent Skill: access route, endpoint, a Python example and pitfalls. It extends the handbooks of [LLM-Find](https://github.com/gladcolor/llm-find).

**What a consult costs.** Under each answer: the time, the tokens and an estimated cost at the model's list prices. A typical turn reads about 50,000 tokens and writes 2,000, which is about 6 cents on GPT-6.1 Terra before caching.

**Your guide and your feedback.** *Download guide* on the Data tab makes one document with the scope, every dataset and its fit, methods, next steps and what the previews showed; print it, save it as a PDF or copy it as Markdown. The *Yes* or *No* under each answer is stored with the question's first 300 characters only, never the conversation, to build the evaluation set from real use.

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
