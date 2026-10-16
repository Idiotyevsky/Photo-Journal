/* Local colour memories. The host owns navigation and photo viewing. */
(() => {
  'use strict';
  const PERIODS = [['month', '按月'], ['quarter', '按季'], ['year', '按年']];
  const SCOPES = [['all', '全部照片'], ['favorites', '我的收藏']];
  const METRICS = [
    {key: 'warmth', label: '冷暖', low: '偏冷', middle: '冷暖均衡', high: '偏暖', thresholds: [.44, .56], decrease: '更冷', increase: '更暖'},
    {key: 'brightness', label: '明暗', low: '低调', middle: '柔和', high: '明亮', thresholds: [.35, .68], decrease: '更暗', increase: '更亮'},
    {key: 'saturation', label: '饱和', low: '淡雅', middle: '适中', high: '浓郁', thresholds: [.23, .55], decrease: '更淡', increase: '更浓'},
  ];
  const model = {period: 'month', scope: 'all', revision: 0, data: null, dataKey: '', loading: false, error: '', compare: false, compareA: '', compareB: '', cache: new Map(), pending: new Map()};
  let host = null;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
  const filterKey = () => `${model.period}:${model.scope}`;
  const active = () => host?.getState?.()?.view === 'colors';
  const redraw = () => { if (active()) host?.redraw?.(); };
  const icon = name => host?.icon?.(name) || '';
  const count = value => Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
  const metric = value => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
  const ids = values => [...new Set((Array.isArray(values) ? values : []).filter(value => typeof value === 'string' && value))];
  const percent = value => value > 0 && value < .01 ? '<1' : String(Math.round(value * 100));
  const descriptor = (item, value) => value === null ? '待分析' : value < item.thresholds[0] ? item.low : value > item.thresholds[1] ? item.high : item.middle;
  const localImage = value => typeof value === 'string' && /^\/(?!\/)/.test(value) && !/[\u0000-\u001f\\]/.test(value) ? value : '';

  function normalize(payload, period, scope) {
    if (!payload || !Array.isArray(payload.periods)) throw new Error('暂时无法读取色彩结果，请重试。');
    if ((payload.period && payload.period !== period) || (payload.scope && payload.scope !== scope)) throw new Error('筛选结果暂未对齐，请重试。');
    const seen = new Set();
    const periods = payload.periods.filter(value => value && typeof value.key === 'string' && !seen.has(value.key) && seen.add(value.key)).map(value => {
      const photoIds = ids(value.photo_ids), membership = new Set(photoIds);
      const palette = (Array.isArray(value.palette) ? value.palette : []).filter(color => color && /^#[a-f0-9]{6}$/i.test(color.hex) && typeof color.share === 'number' && Number.isFinite(color.share) && color.share > 0).slice(0, 5).map(color => ({hex: color.hex.toUpperCase(), share: Math.min(1, color.share), photo_ids: ids(color.photo_ids).filter(id => membership.has(id))}));
      const sum = palette.reduce((total, color) => total + color.share, 0);
      if (sum) palette.forEach(color => { color.share /= sum; });
      return {key: value.key, label: String(value.label || value.key), photo_count: count(value.photo_count), palette, metrics: Object.fromEntries(METRICS.map(item => [item.key, metric(value.metrics?.[item.key])])), representative_ids: ids(value.representative_ids).filter(id => membership.has(id)).slice(0, 4), photo_ids: photoIds};
    });
    // The server orders newest first; undated memories remain outside the trend.
    return {period, scope, total_photos: count(payload.total_photos), analyzed_photos: count(payload.analyzed_photos), skipped_photos: count(payload.skipped_photos), undated_photos: count(payload.undated_photos), periods: [...periods.filter(item => item.key !== 'undated'), ...periods.filter(item => item.key === 'undated')]};
  }
  function currentData() { return model.dataKey === filterKey() ? model.data : null; }
  function settleComparison(data) {
    const keys = data.periods.map(item => item.key);
    if (keys.length < 2) { model.compare = false; model.compareA = keys[0] || ''; model.compareB = ''; return; }
    if (!keys.includes(model.compareA)) model.compareA = keys[1];
    if (!keys.includes(model.compareB) || model.compareB === model.compareA) model.compareB = keys.find(key => key !== model.compareA);
  }
  function configure(context) { host = context; }
  async function ensureLoaded(force = false) {
    if (!host?.api) return null;
    // Rendering may ask again synchronously. A failed request stays failed until
    // an explicit retry, filter change, or library invalidation clears it.
    if (!force && model.error) return null;
    if (force) { model.revision++; model.cache.clear(); }
    const key = filterKey(), revision = model.revision, requestKey = `${revision}:${key}`;
    const cached = model.cache.get(key);
    if (!force && cached) {
      const changed = model.data !== cached || model.loading || model.error;
      model.data = cached; model.dataKey = key; model.loading = false; model.error = ''; settleComparison(cached);
      if (changed) redraw();
      return cached;
    }
    if (model.pending.has(requestKey)) {
      const changed = !model.loading || Boolean(model.error);
      model.loading = true; model.error = '';
      if (changed) redraw();
      return model.pending.get(requestKey);
    }
    model.loading = true; model.error = '';
    const period = model.period, scope = model.scope;
    const promise = Promise.resolve().then(() => host.api(`/api/colors?period=${encodeURIComponent(period)}&scope=${encodeURIComponent(scope)}`)).then(response => {
      const data = normalize(response, period, scope);
      if (revision !== model.revision) return null;
      model.cache.set(key, data);
      if (key === filterKey()) { model.data = data; model.dataKey = key; model.loading = false; model.error = ''; settleComparison(data); redraw(); }
      return data;
    }).catch(error => {
      if (revision === model.revision && key === filterKey()) { model.data = null; model.dataKey = ''; model.loading = false; model.error = String(error?.message || '色彩暂时无法打开。'); redraw(); }
      return null;
    }).finally(() => { model.pending.delete(requestKey); });
    model.pending.set(requestKey, promise);
    redraw();
    return promise;
  }
  function invalidate() {
    model.revision++; model.cache.clear(); model.data = null; model.dataKey = ''; model.error = ''; model.loading = false;
    if (active()) return ensureLoaded();
    return Promise.resolve(null);
  }
  function setFilter(name, value) {
    const choices = name === 'period' ? PERIODS : SCOPES;
    if (!choices.some(item => item[0] === value) || model[name] === value) return Promise.resolve(currentData());
    model[name] = value; model.data = null; model.dataKey = ''; model.error = ''; model.loading = false; model.compareA = ''; model.compareB = '';
    return ensureLoaded();
  }
  function controls(data) {
    return `<div class="colors-controls"><div class="colors-period-tabs" role="group" aria-label="色彩时间分组">${PERIODS.map(([key, label]) => `<button class="colors-period-tab ${model.period === key ? 'active' : ''}" data-action="color-period" data-period="${key}" aria-pressed="${model.period === key}">${label}</button>`).join('')}</div><div class="colors-controls-right"><label class="colors-scope-label"><span class="visually-hidden">照片范围</span><select class="colors-scope" id="color-scope" data-color-field="scope" aria-label="照片范围">${SCOPES.map(([key, label]) => `<option value="${key}" ${model.scope === key ? 'selected' : ''}>${label}</option>`).join('')}</select></label><button class="button secondary colors-compare-toggle" data-action="color-compare" aria-expanded="${model.compare}" ${!data || data.periods.length < 2 ? 'disabled' : ''}>${icon('grid')}比较两段时光</button></div></div>`;
  }
  function empty(title, message, action = '') { return `<section class="colors-empty"><span class="colors-empty-mark" aria-hidden="true"></span><h2>${title}</h2><p>${message}</p>${action}</section>`; }
  function summary(data) {
    return `<div class="colors-summary"><span>${data.periods.length} 段时光<span class="colors-summary-separator">/</span>${data.analyzed_photos} 张照片的颜色</span><span class="colors-summary-notes">${[data.skipped_photos ? `${data.skipped_photos} 张暂未解析` : '', data.undated_photos ? `${data.undated_photos} 张未注明日期` : ''].filter(Boolean).join(' · ')}</span></div>`;
  }
  function trajectories(data) {
    const periods = data.periods.filter(item => item.key !== 'undated').slice(0, 24).reverse();
    if (periods.length < 2) return '';
    return `<div class="colors-trajectories" aria-label="色彩随时间的变化">${METRICS.map(item => {
      const points = periods.map((period, index) => ({index, value: period.metrics[item.key]})).filter(point => point.value !== null);
      if (points.length < 2) return '';
      const path = points.map(point => `${(3 + point.index / (periods.length - 1) * 154).toFixed(2)},${(26 - point.value * 22).toFixed(2)}`).join(' ');
      const latest = points[points.length - 1];
      return `<figure class="colors-trajectory colors-trajectory-${item.key}"><figcaption><span>${item.label}</span><strong>${descriptor(item, latest.value)}</strong></figcaption><svg viewBox="0 0 160 31" role="img" aria-label="${item.label}从较早到最近的变化"><path d="M3 26H157" class="colors-trend-baseline"/><polyline points="${path}" class="colors-trend-line"/><circle cx="${(3 + latest.index / (periods.length - 1) * 154).toFixed(2)}" cy="${(26 - latest.value * 22).toFixed(2)}" r="2.7" class="colors-trend-dot"/></svg></figure>`;
    }).join('')}</div>`;
  }
  function palette(period, compact = false) {
    if (!period.palette.length) return '<div class="colors-no-palette">这一段时光暂未提取到颜色。</div>';
    return `<div class="colors-palette ${compact ? 'is-compact' : ''}"><div class="colors-band" role="group" aria-label="${esc(period.label)}的主色比例">${period.palette.map((color, index) => `<button class="colors-band-segment" style="--swatch:${color.hex};--share:${color.share.toFixed(6)}" data-action="color-swatch" data-period-key="${esc(period.key)}" data-color-index="${index}" aria-label="${esc(period.label)}，${color.hex}，约${percent(color.share)}%" title="${color.hex} · ${percent(color.share)}%" ${!color.photo_ids.length ? 'disabled' : ''}></button>`).join('')}</div><div class="colors-swatches">${period.palette.map((color, index) => `<button class="colors-swatch" data-action="color-swatch" data-period-key="${esc(period.key)}" data-color-index="${index}" aria-label="查看${esc(period.label)}中接近${color.hex}的照片，约${percent(color.share)}%" ${!color.photo_ids.length ? 'disabled' : ''}><span class="colors-swatch-paint" style="--swatch:${color.hex}" aria-hidden="true"></span><span class="colors-swatch-info"><span>${color.hex}</span><span>${percent(color.share)}%</span></span></button>`).join('')}</div></div>`;
  }
  function metricWords(period) {
    return `<div class="colors-metrics">${METRICS.map(item => `<div class="colors-metric colors-metric-${item.key}"><span>${item.label}</span><strong>${descriptor(item, period.metrics[item.key])}</strong>${period.metrics[item.key] === null ? '' : `<span class="colors-metric-track" aria-hidden="true"><i style="--value:${(period.metrics[item.key] * 100).toFixed(2)}%"></i></span>`}</div>`).join('')}</div>`;
  }
  function representatives(period) {
    const photos = new Map((host?.getState?.()?.photos || []).map(photo => [photo.id, photo]));
    const items = period.representative_ids.map(id => ({id, src: localImage(photos.get(id)?.thumbnail_url)})).filter(item => item.src);
    if (!items.length) return '';
    return `<div class="colors-representatives" aria-label="${esc(period.label)}的代表作品">${items.map((item, index) => `<button class="colors-representative" data-action="color-photo" data-photo-id="${esc(item.id)}" data-period-key="${esc(period.key)}" aria-label="打开${esc(period.label)}的第${index + 1}张代表作品"><img src="${esc(item.src)}" alt="${esc(period.label)}的代表作品 ${index + 1}" loading="lazy" decoding="async"></button>`).join('')}<span class="colors-representative-caption">一些当时的光影</span></div>`;
  }
  function periodCard(period) {
    return `<article class="colors-period" data-color-period="${esc(period.key)}"><div class="colors-period-date"><h2>${esc(period.label)}</h2><span>${period.photo_count} 张照片</span></div><div class="colors-period-body">${palette(period)}${metricWords(period)}${representatives(period)}<button class="inline-link colors-period-open" data-action="color-open-period" data-period-key="${esc(period.key)}">看看这一段时光${icon('right')}</button></div></article>`;
  }
  function compare(data) {
    if (!model.compare || data.periods.length < 2) return '';
    const first = data.periods.find(item => item.key === model.compareA), second = data.periods.find(item => item.key === model.compareB);
    if (!first || !second) return '';
    const select = (name, selected, label) => `<label class="colors-compare-select"><span>${label}</span><select id="color-${name}" data-color-field="${name}" aria-label="${label}">${data.periods.map(period => `<option value="${esc(period.key)}" ${period.key === selected ? 'selected' : ''}>${esc(period.label)}</option>`).join('')}</select></label>`;
    const card = (period, name, label) => `<div class="colors-compare-card">${select(name, period.key, label)}${palette(period, true)}<span class="colors-compare-count">${period.photo_count} 张照片</span></div>`;
    return `<section class="colors-compare" aria-label="两段时光的色彩比较"><div class="colors-compare-heading"><h2>并排回望</h2><button class="icon-button" data-action="color-close-compare" aria-label="收起比较">${icon('close')}</button></div><div class="colors-compare-grid">${card(first, 'compare-a', '第一段时光')}${card(second, 'compare-b', '第二段时光')}</div><div class="colors-compare-deltas" aria-label="第二段相对第一段的变化">${METRICS.map(item => {
      const a = first.metrics[item.key], b = second.metrics[item.key];
      if (a === null || b === null) return `<div><span>${item.label}</span><strong>暂不可比</strong></div>`;
      const change = b - a, amount = Math.round(Math.abs(change) * 100), word = amount < 2 ? '接近' : change > 0 ? item.increase : item.decrease;
      return `<div><span>${item.label}</span><strong>${word}</strong><small>${change > 0 ? '+' : change < 0 ? '−' : ''}${amount} 个百分点</small></div>`;
    }).join('')}</div><p class="colors-compare-footnote">第二段相对第一段</p></section>`;
  }
  function render() {
    const data = currentData();
    let content;
    if (model.loading || (!data && !model.error)) content = '<div class="colors-loading" role="status"><span class="spinner"></span><p>正在拾取照片里的颜色…</p></div>';
    else if (model.error) content = empty('色彩暂时无法打开', esc(model.error), '<button class="button secondary" data-action="color-retry">重新读取</button>');
    else if (!data.total_photos) content = model.scope === 'favorites' ? empty('把偏爱的颜色，留在这里。', '收藏照片后，就能回看它们的色彩。', '<button class="button secondary" data-action="color-scope" data-scope="all">看看全部照片</button>') : empty('还没有留下色彩足迹。', '导入照片后，在这里回望每一段时光。', '<button class="button primary" data-action="import">导入照片</button>');
    else if (!data.periods.length) content = summary(data) + empty('暂时还没有可呈现的色彩。', data.skipped_photos ? `${data.skipped_photos} 张照片暂未解析。` : '稍后再来看看。');
    else content = summary(data) + trajectories(data) + compare(data) + `<div class="colors-timeline">${data.periods.map(periodCard).join('')}</div>`;
    return `<section class="colors-page" aria-label="色彩足迹">${controls(data)}${content}</section>`;
  }
  function findPeriod(key) { return currentData()?.periods.find(period => period.key === key); }
  async function handleClick(button) {
    const action = button?.dataset?.action;
    if (!action?.startsWith('color-')) return false;
    if (button.disabled) return true;
    if (action === 'color-period') { await setFilter('period', button.dataset.period); return true; }
    if (action === 'color-scope') { await setFilter('scope', button.dataset.scope); return true; }
    if (action === 'color-retry') { await ensureLoaded(true); return true; }
    if (action === 'color-compare' || action === 'color-close-compare') {
      const data = currentData();
      model.compare = action === 'color-compare' && data?.periods.length > 1 ? !model.compare : false;
      if (data) settleComparison(data); redraw(); return true;
    }
    const period = findPeriod(button.dataset.periodKey);
    if (!period) return true;
    if (action === 'color-open-period') { host?.openPhotos?.({title: period.label, photoIds: [...period.photo_ids]}); return true; }
    if (action === 'color-swatch') {
      const index = Number(button.dataset.colorIndex), color = Number.isInteger(index) ? period.palette[index] : null;
      if (color?.photo_ids.length) host?.openPhotos?.({title: `${period.label} · ${color.hex}`, photoIds: [...color.photo_ids]});
      return true;
    }
    if (action === 'color-photo' && period.representative_ids.includes(button.dataset.photoId)) { host?.openPhoto?.(button.dataset.photoId); return true; }
    return true;
  }
  async function handleChange(target) {
    const field = target?.dataset?.colorField, value = String(target?.value || '');
    if (field === 'period' || field === 'scope') { await setFilter(field, value); return true; }
    if (!['compare-a', 'compare-b'].includes(field)) return false;
    const data = currentData();
    if (!data?.periods.some(period => period.key === value)) return true;
    const key = field === 'compare-a' ? 'compareA' : 'compareB', other = key === 'compareA' ? 'compareB' : 'compareA', previous = model[key];
    model[key] = value;
    if (model[other] === value) model[other] = previous && previous !== value ? previous : data.periods.find(period => period.key !== value)?.key || '';
    redraw(); return true;
  }
  window.ShiguangColors = {configure, ensureLoaded, invalidate, render, handleClick, handleChange};
})();
