/* 拾光 — local, private photo journal. No remote services or analytics. */
(() => {
  'use strict';
  const app = document.getElementById('app');
  const modalRoot = document.getElementById('modal-root');
  const toastRoot = document.getElementById('toast-root');
  const icons = {
    photo: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    pin: '<path d="M20 10c0 6-8 11-8 11S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
    book: '<path d="M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H3V4Zm16 0h-4a3 3 0 0 0-3 3v14a4 4 0 0 1 4-2h4V4Z"/>',
    heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    left: '<path d="m15 5-7 7 7 7"/>',
    right: '<path d="m9 5 7 7-7 7"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    edit: '<path d="m16 3 5 5-12 12-6 1 1-6L16 3Zm-2 2 5 5"/>',
    trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    folder: '<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    camera: '<path d="m8 5 2-2h4l2 2h4a2 2 0 0 1 2 2v12H2V7a2 2 0 0 1 2-2h4Z"/><circle cx="12" cy="12" r="4"/>',
    lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v2"/>',
    settings: '<path d="m9 3-.7 2.1-2 .8-2-.5-2 3.5 1.5 1.6-.2 2.2L2 14l2 3.5 2.2-.4 1.8 1.3.5 2.2h4l.8-2.1 2-.8 2 .5 2-3.5-1.5-1.6.2-2.2L22 10l-2-3.5-2.2.4L16 5.6 15.5 3h-4Z"/><circle cx="12" cy="12" r="3"/>',
    video: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="m10 8 6 4-6 4V8ZM3 8h3M3 16h3M18 8h3M18 16h3"/>',
    leaf: '<path d="M20 3C9 2 3 7 5 14s15 7 15-11ZM5 21 16 9"/>',
  };
  const icon = (name, extra = '') => `<svg class="icon ${extra}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.photo}</svg>`;
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  const state = { photos: [], journals: [], deletedJournalKeys: new Set(), collections: [], view: 'collections', search: '', sort: 'date-desc', layout: 'grid', scope: null, loading: true, error: '', modal: null, lightboxId: null, files: [], importing: false, importResults: [], importProgress: 0, importDone: false, skippedFiles: 0, storageBytes: 0, selectionMode: false, selectedIds: new Set() };
  window.shiguangIsImporting = () => state.importing || Boolean(window.ShiguangSettings?.isImporting());
  window.ShiguangAtlas?.configure({getState:()=>state,api,modalFrame,showModal,renderModal,closeModal,toast,icon,redraw:()=>{if(state.view==='places'&&!state.scope)updateCollection();},openPlace:openPlaceScope,openCountry:code=>{code=String(code||'').toUpperCase();state.scope={type:'country',key:code,title:countryName(code)||'待补充国家'};state.view='photos';state.search='';state.layout='grid';clearSelection();render();}});
  window.ShiguangMusic?.configure({onError:message=>toast(message,'error')});
  window.ShiguangFullscreen?.configure({onError:message=>toast(message,'error')});
  window.ShiguangSettings?.configure({getState:()=>state,api,modalFrame,showModal,renderModal,closeModal,toast,icon});
  window.ShiguangVideoExport?.configure({getState:()=>state,api,modalFrame,showModal,renderModal,closeModal,toast,icon});
  const navs = [ ['collections','folder','作品集'], ['photos','grid','全部照片'], ['timeline','clock','时间轴'], ['places','pin','拍摄地点'], ['journal','book','旅行手记'], ['favorites','heart','我的收藏'] ];
  const collectionRecord = id => state.collections.find(item => item.id === id);
  const currentCollection = () => state.scope?.type === 'collection' ? collectionRecord(state.scope.key) : null;
  const supportedPhoto = file => /\.(jpe?g|png|webp|gif|bmp|tiff?)$/i.test(file.name);
  const matches = (values, query) => values.some(value => String(value || '').toLocaleLowerCase().includes(query));
  const photoCity = photo => String(photo.city || photo.place || '').trim();
  function inferredCountry(city) {
    const country = window.ShiguangAtlas?.countryForPlace?.(city);
    return String((country && typeof country === 'object' ? country.code : country) || '').toUpperCase();
  }
  const photoCountry = photo => String(photo.country || inferredCountry(photoCity(photo)) || '').toUpperCase();
  function countryDirectory() {
    const countries = window.ShiguangAtlas?.countries?.();
    return Array.isArray(countries) && countries.length ? countries : [{code:'CN',name:'中国'}];
  }
  const countryName = code => countryDirectory().find(country=>country.code===code)?.name || code;
  function photoLocation(photo, fallback = '地点待补充') {
    return [countryName(photoCountry(photo)),photoCity(photo)].filter(Boolean).join(' · ') || fallback;
  }
  function placeTitle(city, photos) {
    const countries = [...new Set(photos.map(photoCountry).filter(Boolean))];
    return [countries.length===1 ? countryName(countries[0]) : '',city].filter(Boolean).join(' · ') || '待补充地点';
  }
  function countryOptions(selected = '') {
    const countries = [...countryDirectory()];
    if (selected && !countries.some(country=>country.code===selected)) countries.push({code:selected,name:selected});
    return `<option value="" ${!selected?'selected':''}>未选择国家</option>${countries.map(country=>`<option value="${esc(country.code)}" ${country.code===selected?'selected':''}>${esc(country.name)}</option>`).join('')}`;
  }
  function openPlaceScope(place, country = '') {
    const code = String(country || '').toUpperCase();
    const photos = state.photos.filter(photo=>photoCity(photo)===place && (!code || photoCountry(photo)===code));
    state.scope = {type:'place',key:place,title:placeTitle(place,photos),...(code?{country:code}:{})};
    state.view='photos';state.search='';state.layout='grid';clearSelection();render();
  }
  async function loadCountryOptions(activeModal) {
    try {
      await window.ShiguangAtlas?.loadCountries?.();
      if (state.modal !== activeModal) return;
      const select = document.getElementById(activeModal.type==='import'?'import-country':'edit-country');
      if (!select) return;
      let selected = select.value;
      if (activeModal.type==='edit' && !selected && !select.dataset.countryEdited) {
        const photo = state.photos.find(item=>item.id===activeModal.id);
        if (photo) selected = String(photo.country || inferredCountry(document.getElementById('edit-place')?.value ?? photoCity(photo)) || '').toUpperCase();
      }
      select.innerHTML = countryOptions(selected);
      if (activeModal.type==='import') activeModal.country = selected;
    } catch (error) { toast('国家列表暂时无法载入，可以稍后再补充。','error'); }
  }
  function clearSelection() { state.selectionMode = false; state.selectedIds.clear(); }
  function storeCollection(collection) {
    state.collections = [...state.collections.filter(item => item.id !== collection.id), collection];
    if (state.scope?.type === 'collection' && state.scope.key === collection.id) state.scope.title = collection.title;
  }
  function openCollection(id, shouldRender = true) {
    const collection = collectionRecord(id);
    if (!collection) throw new Error('这个作品集暂时无法打开，请重新连接后重试。');
    state.view = 'collections'; state.scope = {type:'collection', key:id, title:collection.title};
    state.search = ''; state.layout = 'grid'; clearSelection();
    if (shouldRender) render();
  }
  const dateText = (value, options = {}) => {
    if (!value) return '日期待补充';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '日期待补充' : date.toLocaleDateString('zh-CN', {year:'numeric', month:'long', day:'numeric', ...options});
  };
  const monthKey = photo => /^\d{4}-\d{2}/.test(photo.taken_at || '') ? photo.taken_at.slice(0, 7) : 'undated';
  const monthTitle = key => key === 'undated' ? '日期待补充' : `${key.slice(0,4)} 年 ${Number(key.slice(5,7))} 月`;
  const journalKey = photo => photo.trip ? `trip:${photo.trip}` : `month:${monthKey(photo)}`;
  const journalRecord = key => state.journals.find(item => item.key === key);
  const journalTitle = (key, photos) => journalRecord(key)?.title || (key.startsWith('trip:') ? key.slice(5) : `${monthTitle(photos[0] ? monthKey(photos[0]) : key.slice(6))}的影像`);
  const imageUrl = photo => /\.(tiff?|bmp)$/i.test(photo.filename) ? photo.thumbnail_url : photo.url;
  const sizeText = bytes => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;
  const localISO = date => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0,19);
  const group = (photos, keyFn) => {
    const groups = new Map();
    photos.forEach(photo => { const key = keyFn(photo); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(photo); });
    return [...groups];
  };
  function journalGroups(photos) {
    const groups = new Map(group(photos, journalKey));
    const query = state.search.trim().toLocaleLowerCase();
    state.journals.forEach(record => {
      if (!groups.has(record.key) && (!query || [record.title,record.note,record.key].some(text=>String(text||'').toLocaleLowerCase().includes(query)))) groups.set(record.key, []);
    });
    return [...groups].filter(([key])=>!state.deletedJournalKeys.has(key));
  }
  function photoDateRange(photos) {
    const dates = photos.map(photo=>photo.taken_at).filter(Boolean).sort();
    if (!dates.length) return '日期待补充';
    return dates.length > 1 && dates[0].slice(0,10) !== dates[dates.length-1].slice(0,10) ? `${dateText(dates[0])} — ${dateText(dates[dates.length-1])}` : dateText(dates[0]);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {cache:'no-store', ...options});
    const data = await response.json().catch(() => ({error: '无法读取相册响应。'}));
    if (!response.ok) throw new Error(data.error || '操作没有完成，请重试。');
    return data;
  }
  function toast(message, kind = 'success') {
    const element = document.createElement('div');
    element.className = `toast ${kind}`;
    element.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    element.innerHTML = `${icon(kind === 'error' ? 'info' : 'check')}<span class="toast-message">${esc(message)}</span>`;
    toastRoot.append(element);
    setTimeout(() => element.remove(), 5500);
  }
  async function refresh() {
    try {
      const [photos, journals, collections] = await Promise.all([api('/api/photos'), api('/api/journals'), api('/api/collections')]);
      state.photos = photos.photos;
      state.storageBytes = photos.storage_bytes || 0;
      state.journals = journals.journals;
      state.deletedJournalKeys = new Set(journals.deleted_journal_keys || []);
      state.collections = collections.collections;
      const existingIds = new Set(state.photos.map(photo => photo.id));
      state.selectedIds = new Set([...state.selectedIds].filter(id => existingIds.has(id)));
      if (state.scope?.type === 'collection') {
        const collection = currentCollection();
        if (collection) state.scope.title = collection.title;
        else { state.scope = null; clearSelection(); }
      }
      if (state.scope?.type === 'journal' && state.deletedJournalKeys.has(state.scope.key)) {state.scope=null;state.view='journal';state.search='';clearSelection();}
      state.error = '';
    } catch (error) { state.error = error.message; }
    state.loading = false;
    render();
  }
  function filteredPhotos() {
    let photos = [...state.photos];
    if (state.view === 'favorites') photos = photos.filter(photo => photo.favorite);
    if (state.scope?.type === 'place') photos = photos.filter(photo => photoCity(photo) === state.scope.key && (!state.scope.country || photoCountry(photo)===state.scope.country));
    if (state.scope?.type === 'country') photos = photos.filter(photo => photoCountry(photo) === state.scope.key);
    if (state.scope?.type === 'journal') photos = photos.filter(photo => journalKey(photo) === state.scope.key);
    if (state.scope?.type === 'collection') {
      const members = new Set(currentCollection()?.photo_ids || []);
      photos = photos.filter(photo => members.has(photo.id));
    }
    if (state.search.trim()) {
      const query = state.search.trim().toLocaleLowerCase();
      photos = photos.filter(photo => {
        const journal = journalRecord(journalKey(photo));
        const collectionTitles = state.collections.filter(item => item.photo_ids.includes(photo.id)).map(item => item.title);
        return matches([photo.title, photo.filename, photo.caption, photoCity(photo), photoCountry(photo), countryName(photoCountry(photo)), photo.trip, photo.taken_at, photo.camera, journal?.title, journal?.note, ...collectionTitles], query);
      });
    }
    const dateValue = photo => photo.taken_at || '';
    photos.sort((a,b) => state.sort === 'date-asc' ? dateValue(a).localeCompare(dateValue(b)) : state.sort === 'import-desc' ? (b.imported_at || '').localeCompare(a.imported_at || '') : state.sort === 'name' ? (a.title || a.filename).localeCompare(b.title || b.filename, 'zh-CN') : dateValue(b).localeCompare(dateValue(a)));
    return photos;
  }
  function brandMark() {
    return `<svg class="brand-symbol" viewBox="0 0 44 44" fill="none" stroke="#384b5a" stroke-width="1.5" stroke-linecap="square" stroke-linejoin="miter" aria-hidden="true"><path d="M4 14V5h9M31 5h9v9M40 30v9h-9M13 39H4v-9"/><path d="M14 31V19a8 8 0 0 1 16 0v12H14ZM22 11v20M14 22h16"/></svg>`;
  }
  function renderCollectionIntro() {
    return `<section class="collection-intro" aria-label="作品集概览"><div class="intro-copy"><div class="intro-eyebrow">PHOTOGRAPHIC JOURNAL / 01</div><h1>我的摄影作品集</h1><div class="intro-stats"><span><strong>${state.collections.length}</strong> 份作品集</span><span><strong>${state.photos.length}</strong> 张摄影作品</span></div></div><figure class="intro-art"><img class="intro-classic-art" src="/assets/european-sketch.png" alt="欧式街角的简约手绘插画" draggable="false"><img class="intro-italian-art" src="/assets/italian-oil-vignette.png" alt="托斯卡纳农舍与橄榄枝的油画小景" draggable="false"></figure></section>`;
  }
  function renderPageHeading(heading, description, actions) {
    const labels = {photos:'COLLECTED PHOTOGRAPHS',timeline:'CHRONOLOGICAL INDEX',places:'A GEOGRAPHY OF MOMENTS',journal:'FIELD NOTES',favorites:'SELECTED MOMENTS'};
    const eyebrow = currentCollection() ? 'PHOTOGRAPHIC PORTFOLIO' : state.scope?.type === 'journal' ? 'FIELD NOTES' : labels[state.view] || 'PRIVATE PHOTOGRAPHIC ARCHIVE';
    return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${esc(heading)}</h1>${description ? `<p class="heading-description collection-description">${esc(description)}</p>` : ''}</div>${actions}</div>`;
  }
  function renderToolbar(layoutTabs,collectionList,isTimeline,sortOptions) {
    return `<div class="toolbar">${layoutTabs}<div class="toolbar-controls"><label class="search-wrap">${icon('search')}<input class="search-input" id="photo-search" type="search" placeholder="${collectionList ? '搜索作品集、地点、照片…' : '搜索地点、作品、手记…'}" value="${esc(state.search)}" aria-label="${collectionList ? '搜索作品集' : '搜索照片'}"></label><select class="sort-select" id="photo-sort" aria-label="排序方式">${sortOptions.map(([key,label])=>`<option value="${key}" ${state.sort === key ? 'selected' : ''} ${isTimeline && !key.startsWith('date-') ? 'disabled' : ''}>${label}</option>`).join('')}</select></div></div>`;
  }
  function render() {
    const collection = currentCollection();
    const collectionList = state.view === 'collections' && !state.scope;
    const placesPage = state.view === 'places' && !state.scope;
    const photoView = !collectionList && (state.scope || ['photos','timeline','favorites'].includes(state.view));
    const isTimeline = state.view === 'timeline' || state.layout === 'timeline';
    if (isTimeline && !state.sort.startsWith('date-')) state.sort = 'date-desc';
    const activeNav = navs.find(item => item[0] === state.view) || navs[0];
    const headings = {collections:'我的摄影作品集',photos:'全部照片',timeline:'按时间翻阅',places:'拍摄地点',journal:'旅行手记',favorites:'收藏的片刻'};
    const descriptions = {collections:'按旅行、地点或主题，整理自己的摄影作品。',photos:'按时间、地点与作品集，整理自己的摄影作品。',timeline:'以拍摄时间为序，翻阅过去的影像。',places:'从地点，重访照片里的片刻。',journal:'记下照片以外的见闻。',favorites:'留作反复观看的照片。'};
    const heading = collection?.title || state.scope?.title || headings[state.view];
    const description = placesPage ? '' : collection ? (collection.description || '') : state.scope?.type === 'place' ? '同一处风景，每一次相遇都有不同。' : state.scope?.type === 'journal' ? '一些沿途的光影，和想要留下的话。' : descriptions[state.view];
    const headingActions = collection ? `<div class="heading-actions"><button class="button secondary" data-action="video-open" data-id="${esc(collection.id)}" ${collection.photo_ids.length?'':'disabled'}>${icon('video')} 导出视频</button><button class="button secondary" data-action="edit-collection" data-id="${esc(collection.id)}">${icon('edit')} 编辑作品集</button><button class="button secondary" data-action="add-existing" data-id="${esc(collection.id)}">${icon('grid')} 从已有照片添加</button><button class="button primary" data-action="import">${icon('upload')} 批量导入</button></div>` : state.scope?.type === 'journal' ? `<div class="heading-actions"><button class="button secondary" data-action="edit-journal">${icon('edit')} 写旅行手记</button><button class="icon-button journal-delete-detail" data-action="delete-journal" data-key="${esc(state.scope.key)}" title="删除旅途记忆" aria-label="删除${esc(state.scope.title)}">${icon('trash')}</button></div>` : '';
    const scopeIcon = collection ? 'folder' : ['place','country'].includes(state.scope?.type) ? 'pin' : 'book';
    const backText = collection ? '返回作品集' : ['place','country'].includes(state.scope?.type) ? '返回拍摄地点' : '返回旅行手记';
    const stats = state.scope ? `<button class="inline-link" data-action="clear-scope">${icon('left')} ${backText}</button>${collection ? `<div class="collection-detail-header"><div class="collection-cover-small">${collection.cover_url ? `<img src="${esc(collection.cover_url)}" alt="作品集封面">` : `<div class="collection-placeholder">${icon('folder')}</div>`}</div><div><div class="collection-meta"><span>${icon('photo')} ${collection.photo_ids.length} 张照片</span>${collection.place ? `<span>${icon('pin')} ${esc(collection.place)}</span>` : ''}</div></div></div>` : `<div class="filter-chip">${icon(scopeIcon)}<span>${esc(state.scope.title)}</span><button data-action="clear-scope" aria-label="${backText}">${icon('close')}</button></div>`}` : `<div class="stats-row"><div class="stat"><span class="stat-value">${state.collections.length}</span><span class="stat-label">份作品集</span></div><div class="stat"><span class="stat-value">${state.photos.length}</span><span class="stat-label">张摄影作品</span></div><div class="stat"><span class="stat-value">${new Set(state.photos.map(photoCity).filter(Boolean)).size}</span><span class="stat-label">个拍摄地点</span></div></div>`;
    const layoutTabs = photoView ? `<div class="view-tabs" aria-label="照片布局"><button class="view-tab ${!isTimeline ? 'active' : ''}" data-action="layout-grid">${icon('grid')} 照片画廊</button><button class="view-tab ${isTimeline ? 'active' : ''}" data-action="layout-timeline">${icon('clock')} 时间线</button><button class="view-tab ${state.selectionMode ? 'active' : ''}" data-action="toggle-selection">${icon('check')} ${state.selectionMode ? '退出批量管理' : '批量管理'}</button></div>` : collectionList ? '' : `<span class="section-label">${state.view === 'places' ? '以足迹，串起回忆' : '一段旅程，一页故事'}</span>`;
    const sortOptions = collectionList ? [['date-desc','最近更新'],['date-asc','创建时间 · 由早及晚'],['name','作品集名称']] : [['date-desc','拍摄时间 · 由近及远'],['date-asc','拍摄时间 · 由远及近'],['import-desc','最近导入'],['name','作品名称']];
    app.innerHTML = `<div class="app-shell ${collectionList ? 'collection-home' : ''}"><aside class="sidebar"><a class="brand brand-icon-only" href="#" data-action="home" aria-label="相册首页" title="相册首页"><span class="brand-mark">${brandMark()}</span></a><nav class="nav-list" aria-label="相册导航">${navs.map(([key, svg, label])=>`<button class="nav-item ${state.view === key ? 'active' : ''}" data-action="nav" data-view="${key}" ${state.view === key ? 'aria-current="page"' : ''}>${icon(svg)}<span>${label}</span>${['collections','photos','favorites'].includes(key) ? `<span class="nav-count">${key === 'collections' ? state.collections.length : key === 'photos' ? state.photos.length : state.photos.filter(photo=>photo.favorite).length}</span>` : ''}</button>`).join('')}</nav><div class="sidebar-bottom"><button class="inline-link" data-action="about">使用说明 ${icon('right')}</button></div></aside><main class="main-area"><header class="topbar"><div class="breadcrumb">我的相册 <span>/</span> ${collection ? '<button class="inline-link" data-action="collections-home">作品集</button> <span>/</span> ' : ''}<strong>${esc(state.scope?.title || activeNav[2])}</strong></div><div class="topbar-right">${window.ShiguangMusic?.renderButton() || ''}${window.ShiguangFullscreen?.renderButton() || ''}<button class="icon-button settings-button" data-action="settings-open" aria-label="设置" title="设置">${icon('settings')}</button><button class="icon-button about-button" data-action="about" aria-label="使用说明">${icon('info')}</button><button class="button secondary" data-action="create-collection">${icon('folder')} 创建作品集</button><button class="button primary" data-action="import">${icon('plus')} 导入照片</button></div></header><div class="page-content">${collectionList ? renderCollectionIntro() : renderPageHeading(heading,description,headingActions)}${collectionList || placesPage ? '' : stats}${state.scope?.type === 'journal' ? renderJournalNote() : ''}${placesPage ? '' : renderToolbar(layoutTabs,collectionList,isTimeline,sortOptions)}<div id="collection" aria-live="polite">${renderCollection()}</div></div></main></div><div class="global-drop-overlay" id="global-drop"><div class="global-drop-content">${icon('upload')}<h2>把沿途的风景，放进来。</h2><p>可一次拖入多张照片，松开鼠标开始整理</p></div></div>`;
  }
  function renderCollectionList() {
    const query = state.search.trim().toLocaleLowerCase();
    let collections = state.collections.filter(collection => !query || matches([collection.title,collection.description,collection.place],query) || state.photos.some(photo => collection.photo_ids.includes(photo.id) && matches([photo.title,photo.filename,photo.caption,photoCity(photo),countryName(photoCountry(photo)),photo.trip],query)));
    collections.sort((a,b)=>state.sort === 'name' ? a.title.localeCompare(b.title,'zh-CN') : state.sort === 'date-asc' ? a.created_at.localeCompare(b.created_at) : b.updated_at.localeCompare(a.updated_at));
    return `${!collections.length && query ? '<p class="batch-hint">未找到匹配的作品集。可以换一个关键词，或创建新的作品集。</p>' : ''}<div class="collections-grid">${collections.map((collection,index)=>`<article class="collection-card ${collection.cover_url ? 'has-cover' : 'without-cover'}"><button class="collection-open" data-action="open-collection" data-id="${esc(collection.id)}"><div class="collection-cover">${collection.cover_url ? `<img src="${esc(collection.cover_url)}" alt="${esc(collection.title)}的封面" loading="lazy">` : `<div class="collection-placeholder"><svg class="collection-blank-mark" viewBox="0 0 64 64" fill="none" aria-hidden="true"><path d="M12 9h40v46H12zM21 16v32M27 25h16M27 31h12M27 37h8" stroke="currentColor" stroke-width="1"/><path d="M17 9V5h39v46h-4" stroke="currentColor" stroke-width=".8"/></svg><span>还没有照片</span><small>NO PHOTOGRAPHS YET</small></div>`}</div><div class="collection-info"><div class="collection-book-meta"><span class="collection-number">VOL. ${String(index+1).padStart(2,'0')}</span><span class="collection-small-count">${collection.photo_ids.length} 张照片</span></div><h3>${esc(collection.title)}</h3><p>${esc(collection.description)}</p>${collection.place ? `<div class="collection-meta"><span>${esc(collection.place)}</span></div>` : ''}</div></button><div class="collection-actions"><button class="inline-link" data-action="open-collection" data-id="${esc(collection.id)}">翻开作品集 ${icon('right')}</button><button class="icon-button" data-action="edit-collection" data-id="${esc(collection.id)}" aria-label="编辑${esc(collection.title)}">${icon('edit')}</button><button class="icon-button danger" data-action="delete-collection" data-id="${esc(collection.id)}" aria-label="删除${esc(collection.title)}">${icon('trash')}</button></div></article>`).join('')}<button class="collection-create-card" data-action="create-collection"><span class="collection-create-eyebrow">THE NEXT VOLUME</span>${icon('plus')}<strong>创建作品集</strong></button></div>`;
  }
  function renderSelectionToolbar() {
    if (!state.selectionMode) return '';
    return `<div class="selection-toolbar"><div class="selection-summary"><strong>已选择 ${state.selectedIds.size} 张</strong><button class="inline-link" data-action="select-all">全选当前结果</button><button class="inline-link" data-action="clear-selection">清空选择</button></div><div class="selection-actions"><button class="button secondary" data-action="assign-selected" ${!state.selectedIds.size ? 'disabled' : ''}>${icon('folder')} 添加到作品集</button>${currentCollection() ? `<button class="button secondary" data-action="remove-selected" ${!state.selectedIds.size ? 'disabled' : ''}>移出当前作品集</button>` : ''}<button class="button ghost" data-action="toggle-selection">完成</button></div></div>`;
  }
  function renderJournalNote() {
    const record = journalRecord(state.scope.key);
    return record?.note ? `<div class="journal-note"><span class="eyebrow">JOURNEY NOTES</span><p>${esc(record.note)}</p></div>` : `<div class="note-callout">${icon('book')}<span>这段旅途，还差几句当时的心情。</span><button class="inline-link" data-action="edit-journal">写下手记 ${icon('arrow')}</button></div>`;
  }
  function emptyArt() {
    return `<figure class="empty-art"><img class="intro-classic-art" src="/assets/european-sketch.png" alt="欧式街角的简约手绘插画" draggable="false"><img class="intro-italian-art" src="/assets/italian-oil-vignette.png" alt="托斯卡纳农舍与橄榄枝的油画小景" draggable="false"><figcaption class="empty-art-caption">TRAVEL SKETCH</figcaption></figure>`;
  }
  function renderCollection() {
    if (state.loading) return `<div class="loading-state"><span class="spinner"></span><p>正在打开你的影像空间…</p></div>`;
    if (state.error) return `<div class="empty-small">${icon('info')}<h2>暂时无法打开相册</h2><p>${esc(state.error)}</p><button class="button secondary" data-action="retry">重新连接</button><p class="muted">请关闭应用后重新打开，或点击重新连接。</p></div>`;
    if (state.view === 'collections' && !state.scope) return renderCollectionList();
    if (state.view === 'places') return renderPlaces(filteredPhotos());
    const photos = filteredPhotos();
    if (currentCollection() && !photos.length) return `${renderSelectionToolbar()}<section class="empty-small collection-empty">${icon(state.search ? 'search' : 'folder')}<h2>${state.search ? '这里还没有匹配的照片' : '作品集已经建好，放进第一组照片吧。'}</h2><p>${state.search ? '试试其他关键词，或清空搜索查看全部照片。' : '可以一次选择多张照片，也可以把整个旅行文件夹导入。'}</p><div class="empty-actions">${state.search ? '<button class="button secondary" data-action="clear-search">清空搜索</button>' : `<button class="button primary" data-action="import">${icon('upload')} 批量导入照片</button><button class="button secondary" data-action="add-existing" data-id="${esc(currentCollection().id)}">从已有照片添加</button>`}</div><p class="batch-hint">同一张照片可以加入多个作品集，不会重复保存原图。</p></section>`;
    if (state.view === 'journal') {
      const groups=journalGroups(photos);
      if(groups.length)return renderJournals(groups);
      return `<section class="empty-small">${icon('book')}<h2>${state.search?'没有找到匹配的旅途记忆':'这里还没有旅途记忆'}</h2>${state.search?'<button class="button secondary" data-action="clear-search">清空搜索</button>':''}</section>`;
    }
    if (!photos.length && state.scope?.type === 'journal' && !state.search) return `<section class="empty-small">${icon('book')}<h2>文字还在，等新的风景到来。</h2><p>这篇手记暂时没有关联照片，你可以继续编辑文字，或导入新的作品。</p><button class="button secondary" data-action="import">${icon('plus')} 导入照片</button></section>`;
    if (!state.photos.length && !state.search) return `<section class="empty-state">${emptyArt()}<span class="empty-kicker">THE FIRST PHOTOGRAPH</span><h2>从第一张照片开始。</h2><div class="empty-actions"><button class="button primary" data-action="import">${icon('plus')} 导入我的照片 ${icon('arrow')}</button></div><div class="empty-footnote">${icon('upload')} 也可以将照片直接拖到这里</div></section>`;
    if (!photos.length) return `<section class="empty-small">${icon(state.view === 'favorites' ? 'heart' : 'search')}<h2>${state.search ? '还没有找到这些瞬间' : state.view === 'favorites' ? '把喜欢的瞬间，留在这里。' : '这里暂时还没有照片'}</h2><p>${state.search ? '试试其他地点、作品名称，或清空搜索。' : '打开照片，点亮爱心，就能在这里再次相遇。'}</p>${state.search ? '<button class="button secondary" data-action="clear-search">清空搜索</button>' : '<button class="button secondary" data-action="browse-photos">浏览全部照片</button>'}</section>`;

    if (state.view === 'timeline' || state.layout === 'timeline') return `${renderSelectionToolbar()}${renderTimeline(photos)}`;
    return `${renderSelectionToolbar()}<div class="photo-count">共 ${photos.length} 张作品${state.search ? ' · 搜索结果' : ''}</div><div class="photo-grid ${state.selectionMode ? 'selection-mode' : ''}">${photos.map(photoCard).join('')}</div>`;
  }
  function photoCard(photo) {
    const selected = state.selectedIds.has(photo.id);
    return `<article class="photo-card ${selected ? 'selected' : ''} ${state.selectionMode ? 'is-selecting' : ''}"><button class="photo-image-wrap" data-action="${state.selectionMode ? 'select-photo' : 'open-photo'}" data-id="${esc(photo.id)}" aria-label="${state.selectionMode ? '选择' : '查看'} ${esc(photo.title || photo.filename)}"><img src="${esc(photo.thumbnail_url)}" alt="" loading="lazy" decoding="async"><span class="photo-overlay"><span class="photo-open-label">${icon(state.selectionMode ? 'check' : 'photo')} ${state.selectionMode ? '选择照片' : '查看作品'}</span></span></button><button class="select-photo-button" data-action="select-photo" data-id="${esc(photo.id)}" aria-label="${selected ? '取消选择' : '选择'} ${esc(photo.title || photo.filename)}" aria-pressed="${selected}">${icon('check')}<span class="photo-select-label">${selected ? '已选择' : '选择照片'}</span></button><button class="favorite-button ${photo.favorite ? 'active' : ''}" data-action="favorite" data-id="${esc(photo.id)}" aria-label="${photo.favorite ? '取消收藏' : '收藏'} ${esc(photo.title || photo.filename)}" aria-pressed="${Boolean(photo.favorite)}">${icon('heart')}</button><div class="photo-info"><div class="photo-meta"><span>${icon('pin')}${esc(photoLocation(photo,photo.lat != null ? '坐标已记录 · 待命名' : '地点待补充'))}</span><time>${dateText(photo.taken_at, {month:'2-digit',day:'2-digit'}).replace(/\//g,'.')}</time></div></div></article>`;
  }
  function renderTimeline(photos) {
    return `<div class="photo-count">共 ${photos.length} 张作品 · 按拍摄时间排列</div><div class="timeline">${group(photos, photo => (photo.taken_at || '').slice(0,4) || '待补充').map(([year, yearPhotos]) => `<section class="timeline-year"><div class="year-label">${esc(year)}<span>${yearPhotos.length} 张作品</span></div>${group(yearPhotos, monthKey).map(([month, monthPhotos]) => `<div class="timeline-month"><div class="month-label"><span>${month === 'undated' ? '待补充' : `${Number(month.slice(5,7))} 月`}</span><span>${monthPhotos.length} 张</span></div><div class="photo-grid">${monthPhotos.map(photoCard).join('')}</div></div>`).join('')}</section>`).join('')}</div>`;
  }
  function renderPlaces(photos) {
    const groups = group(photos, photo => JSON.stringify([photoCountry(photo),photoCity(photo)])).map(([,items])=>({place:photoCity(items[0]),country:photoCountry(items[0]),items}));
    return `${window.ShiguangAtlas ? window.ShiguangAtlas.render(state.photos) : ''}${renderToolbar('',false,false,[['date-desc','拍摄时间 · 由近及远'],['date-asc','拍摄时间 · 由远及近'],['import-desc','最近导入'],['name','作品名称']])}<div class="photo-count">${groups.filter(({place})=>place).length} 个拍摄地点${groups.some(({place})=>!place) ? ' · 含待补充地点的照片' : ''}</div><div class="place-grid">${groups.map(({place,country,items}) => `<button class="place-card" data-action="open-place" data-key="${esc(place)}" data-country="${esc(country)}"><div class="place-cover"><img src="${esc(items[0].thumbnail_url)}" loading="lazy" alt=""><span class="place-count">${items.length} 张作品</span></div><div class="place-info"><div><h3>${icon('pin')}${esc(placeTitle(place,items))}</h3><p>${esc(photoDateRange(items))}</p></div>${icon('arrow')}</div></button>`).join('')}</div>`;
  }
  function renderJournals(groups) {
    return `<div class="photo-count">${groups.length} 段旅途记忆</div><div class="journal-grid">${groups.map(([key, items]) => {
      const record=journalRecord(key),title=journalTitle(key,items),places=[...new Set(items.map(photo=>photoLocation(photo,'')).filter(Boolean))];
      return `<article class="journal-card"><button class="journal-open" data-action="open-journal" data-key="${esc(key)}"><div class="journal-cover">${items[0]?`<img src="${esc(items[0].thumbnail_url)}" alt="" loading="lazy">`:`<div class="journal-empty-cover">${icon('book')}<span>留下文字，也留下回忆。</span></div>`}<span>${items.length?`${items.length} 个瞬间`:'文字手记'}</span></div><div class="journal-body"><div class="journal-date">${items[0]?monthTitle(monthKey(items[0])):'文字记忆'} · JOURNEY</div><h2>${esc(title)}</h2>${record?.note?`<p class="journal-excerpt">${esc(record.note)}</p>`:''}<div class="journal-meta"><span>${icon('pin')}${esc(places.join(' · ')||(items.length?'地点待补充':'手记已独立保存'))}</span><span>翻开手记 ${icon('arrow')}</span></div></div></button><button class="icon-button journal-delete" data-action="delete-journal" data-key="${esc(key)}" title="删除旅途记忆" aria-label="删除${esc(title)}">${icon('trash')}</button></article>`;
    }).join('')}</div>`;
  }
  function updateCollection() {
    const focused = document.activeElement;
    const controlId = ['photo-search','photo-sort'].includes(focused?.id) ? focused.id : null;
    const selection = controlId === 'photo-search' ? [focused.selectionStart,focused.selectionEnd] : null;
    document.getElementById('collection').innerHTML = renderCollection();
    if (controlId && !focused.isConnected) {
      const control = document.getElementById(controlId);
      control?.focus();
      if (selection && control?.setSelectionRange) control.setSelectionRange(...selection);
    }
  }
  let previousFocus = null, exportPreviewSequence = 0;
  function showModal(type, data = {}) {
    if (!state.modal) previousFocus = document.activeElement;
    state.modal = {type, ...data};
    app.inert = true;
    document.body.classList.add('modal-open');
    renderModal();
    if (type==='import' || type==='edit') void loadCountryOptions(state.modal);
    setTimeout(() => modalRoot.querySelector('input:not([type="file"]),textarea,button')?.focus(), 0);
  }
  function closeModal(force = false) {
    if (state.importing || (!force && state.modal?.busy)) return;
    if (!force && state.modal?.returnModal) {
      state.modal = state.modal.returnModal; renderModal();
      setTimeout(()=>modalRoot.querySelector('input:not([type="file"]),button')?.focus(),0);
      return;
    }
    if (state.modal?.type==='settings') window.ShiguangSettings?.onClose();
    if (state.modal?.type==='video-export') window.ShiguangVideoExport?.onClose();
    state.modal = null;
    state.lightboxId = null;
    app.inert = false;
    modalRoot.innerHTML = '';
    document.body.classList.remove('modal-open');
    if (previousFocus?.isConnected) previousFocus.focus();
    else app.querySelector('[data-action="import"]')?.focus();
  }
  function modalFrame(title, description, body, footer, className='') {
    return `<div class="modal-backdrop" data-action="backdrop-close"><section class="modal ${className}" role="dialog" aria-modal="true" aria-labelledby="modal-title"><header class="modal-header"><div><h2 id="modal-title">${title}</h2><p>${description}</p></div><button class="icon-button close-modal" data-action="close-modal" aria-label="关闭" ${state.importing ? 'disabled' : ''}>${icon('close')}</button></header><div class="modal-body">${body}</div>${footer ? `<footer class="modal-footer">${footer}</footer>` : ''}</section></div>`;
  }
  function captureImportDraft() {
    if (state.modal?.type !== 'import') return;
    state.modal.city = document.getElementById('import-place')?.value ?? state.modal.city ?? state.modal.place ?? '';
    state.modal.place = state.modal.city;
    state.modal.country = document.getElementById('import-country')?.value ?? state.modal.country ?? 'CN';
    state.modal.trip = document.getElementById('import-trip')?.value ?? state.modal.trip ?? '';
    state.modal.collectionId = document.getElementById('import-collection')?.value ?? state.modal.collectionId ?? '';
  }
  function openImport() {
    state.files = []; state.importResults = []; state.importProgress = 0; state.importDone = false; state.skippedFiles = 0;
    const collection = currentCollection();
    const city = state.scope?.type==='place' ? state.scope.key : '';
    const country = state.scope?.type==='country' ? state.scope.key : state.scope?.country || inferredCountry(city) || 'CN';
    showModal('import', {collectionId:collection?.id || '',country,city,place:city,trip:state.scope?.type === 'journal' && state.scope.key.startsWith('trip:') ? state.scope.key.slice(5) : '',suggestedTitle:''});
  }
  function renderImportModal() {
    const draft = state.modal;
    const disabled = state.importing ? 'disabled' : '';
    const targets = `<option value="">仅导入全部照片，暂不加入作品集</option>${state.collections.map(collection=>`<option value="${esc(collection.id)}" ${draft.collectionId === collection.id ? 'selected' : ''}>${esc(collection.title)} · ${collection.photo_ids.length} 张</option>`).join('')}${draft.suggestedTitle ? `<option value="__folder__" ${draft.collectionId === '__folder__' ? 'selected' : ''}>新建作品集：${esc(draft.suggestedTitle)}</option>` : ''}<option value="__create__">＋ 新建作品集…</option>`;
    modalRoot.innerHTML = modalFrame('导入照片', '可一次导入多张照片或整个文件夹，原图只在这台电脑保存一份。', `<div class="import-target-select field"><label for="import-collection">导入到作品集</label><select id="import-collection" ${disabled}>${targets}</select><p class="field-hint">重复照片会复用相册中的原图，并加入所选作品集。</p></div><div class="drop-zone" id="import-drop"><input type="file" id="photo-files" class="visually-hidden" multiple accept=".jpg,.jpeg,.png,.webp,.gif,.bmp,.tif,.tiff" ${disabled}><input type="file" id="photo-folder" class="visually-hidden" multiple webkitdirectory directory ${disabled}><div class="drop-symbol">${icon('upload')}</div><div class="drop-title">${state.files.length ? `已选择 ${state.files.length} 张照片${state.importDone ? ' · 已完成整理' : ''}` : '把多张照片一起拖到这里'}</div><p class="drop-hint">JPG、PNG、WebP、GIF、BMP、TIFF · 每张不超过 100 MB</p><div class="import-source-actions"><button class="button secondary" data-action="choose-files" ${disabled}>${icon('photo')} ${state.files.length ? '继续添加照片' : '批量选择照片'}</button><button class="button secondary" data-action="choose-folder" ${disabled}>${icon('folder')} 导入整个文件夹</button>${state.files.length ? `<button class="button ghost small" data-action="clear-files" ${disabled}>清空待导入列表</button>` : ''}</div><p class="batch-hint">在 Windows 选择窗口中按 Ctrl+A 全选，或按住 Ctrl / Shift 多选。<br>再次选择会追加到当前列表，也可以直接拖入多张照片。</p><div class="selected-files">${esc(state.files.slice(0,5).map(file=>file.name).join('、'))}${state.files.length > 5 ? `…等 ${state.files.length} 张` : ''}</div>${state.skippedFiles ? `<p class="batch-hint">已跳过 ${state.skippedFiles} 个非支持格式的文件。</p>` : ''}</div><div class="import-options"><div class="form-grid"><div class="field"><label for="import-country">国家</label><select id="import-country" name="country" ${disabled}>${countryOptions(draft.country ?? 'CN')}</select></div><div class="field"><label for="import-place">城市 <span class="muted">选填</span></label><input id="import-place" name="city" placeholder="例如：扬州" maxlength="100" value="${esc(draft.city ?? draft.place ?? '')}" ${disabled}></div><div class="field"><label for="import-trip">所属旅程 <span class="muted">选填</span></label><input id="import-trip" placeholder="同名旅程的照片会汇成旅行手记" maxlength="200" value="${esc(draft.trip || '')}" ${disabled}></div></div><p class="field-hint">自动读取拍摄时间、GPS 与相机参数。地点和文字也可以稍后补充。</p></div><div id="import-feedback">${importFeedback()}</div>`, `<span class="muted">${icon('lock')} 原始文件不会被修改</span><div><button class="button ghost" data-action="close-modal" ${disabled}>${state.importResults.length && !state.importing ? '完成' : '取消'}</button><button class="button primary" id="start-import" data-action="start-import" ${!state.files.length || state.importing || state.importDone ? 'disabled' : ''}>${state.importing ? '<span class="spinner"></span> 正在批量导入' : `${icon('upload')} ${state.importDone ? '本批已完成' : (state.files.length ? `导入 ${state.files.length} 张照片` : '开始导入')}`}</button></div>`, 'import-modal');
  }
  function existingPickerPhotos() {
    const collection = collectionRecord(state.modal.collectionId);
    const members = new Set(collection?.photo_ids || []);
    const query = (state.modal.query || '').trim().toLocaleLowerCase();
    return state.photos.filter(photo=>!members.has(photo.id) && (!query || matches([photo.title,photo.filename,photoCity(photo),countryName(photoCountry(photo)),photo.caption,photo.trip],query)));
  }
  function renderExistingGrid() {
    const photos = existingPickerPhotos();
    return photos.length ? photos.map(photo=>`<div class="existing-photo-tile ${state.modal.selected.has(photo.id) ? 'selected' : ''}"><label><input type="checkbox" data-existing-photo="${esc(photo.id)}" ${state.modal.selected.has(photo.id) ? 'checked' : ''} aria-label="选择 ${esc(photo.title || photo.filename)}"><img src="${esc(photo.thumbnail_url)}" alt="${esc(photo.title || photo.filename)}" loading="lazy"><span>${esc(photo.title || photo.filename)}</span></label></div>`).join('') : '<p class="batch-hint">没有可添加的匹配照片。已在本作品集中的照片会自动排除。</p>';
  }
  function updateExistingPicker() {
    document.getElementById('existing-photo-grid').innerHTML = renderExistingGrid();
    document.getElementById('existing-selection-count').textContent = `已选择 ${state.modal.selected.size} 张`;
    document.getElementById('save-existing').disabled = !state.modal.selected.size;
  }
  function renderCollectionModal() {
    const modal = state.modal;
    if (modal.type === 'collection-create' || modal.type === 'collection-edit') {
      const collection = modal.type === 'collection-edit' ? collectionRecord(modal.id) : null;
      const title = collection?.title || modal.title || '';
      const members = collection ? state.photos.filter(photo=>collection.photo_ids.includes(photo.id)) : [];
      modalRoot.innerHTML = modalFrame(collection ? '编辑作品集' : '创建作品集', '先创建空作品集，再慢慢加入照片。照片可以同时属于多个作品集。', `<form id="collection-form"><div class="field"><label for="collection-title">作品集名称</label><input id="collection-title" name="title" required maxlength="200" placeholder="例如：扬州 · 春日漫游" value="${esc(title)}"></div><div class="field"><label for="collection-place">地点 <span class="muted">选填</span></label><input id="collection-place" name="place" maxlength="200" placeholder="例如：江苏 · 扬州" value="${esc(collection?.place || modal.place || '')}"></div><div class="field"><label for="collection-description">作品集简介 <span class="muted">选填</span></label><textarea id="collection-description" name="description" rows="4" maxlength="10000" placeholder="记录这次出发，或为这组摄影作品写几句话…">${esc(collection?.description || '')}</textarea></div>${collection ? `<div class="field"><label for="collection-cover">作品集封面</label><select id="collection-cover" name="cover_photo_id"><option value="">自动选择第一张照片</option>${members.map(photo=>`<option value="${esc(photo.id)}" ${collection.cover_photo_id === photo.id ? 'selected' : ''}>${esc(photo.title || photo.filename)}</option>`).join('')}</select><p class="field-hint">也可以打开作品集中的照片，将它设为封面。</p></div>` : ''}</form>`, `${collection ? `<button class="button ghost remove-photo" data-action="delete-collection" data-id="${esc(collection.id)}">${icon('trash')} 删除作品集</button>` : '<span></span>'}<div><button class="button ghost" data-action="close-modal">取消</button><button class="button primary" data-action="save-collection">${icon('check')} ${collection ? '保存修改' : '创建作品集'}</button></div>`, 'create-collection-modal edit-modal');
    } else if (modal.type === 'collection-delete') {
      const collection = collectionRecord(modal.id);
      modalRoot.innerHTML = modalFrame('删除这份作品集？', esc(collection?.title || ''), `<div class="note-callout">${icon('info')}<span>只删除作品集的名称、简介与整理关系。里面的照片仍保留在「全部照片」及其他作品集中，原图不会被删除。</span></div>`, `<button class="button ghost" data-action="close-modal">保留作品集</button><button class="button danger" data-action="confirm-delete-collection" data-id="${esc(modal.id)}">${icon('trash')} 删除作品集</button>`, 'edit-modal');
    } else if (modal.type === 'existing-photos') {
      const collection = collectionRecord(modal.collectionId);
      modalRoot.innerHTML = modalFrame('从已有照片，挑选这段旅途。', `加入「${esc(collection?.title || '')}」；可多选，原图不会重复保存。`, `<div class="toolbar"><label class="search-wrap">${icon('search')}<input id="existing-search" class="search-input" type="search" placeholder="搜索作品、地点…" value="${esc(modal.query || '')}" aria-label="搜索已有照片"></label><button class="button secondary small" data-action="existing-select-all">全选当前结果</button></div><p class="batch-hint">勾选多张照片后，一次加入作品集。已在本作品集中的照片不会重复显示。</p><div class="existing-photo-grid" id="existing-photo-grid">${renderExistingGrid()}</div>`, `<span class="selection-summary" id="existing-selection-count">已选择 ${modal.selected.size} 张</span><button class="button ghost" data-action="close-modal">取消</button><button class="button primary" id="save-existing" data-action="save-existing" ${!modal.selected.size ? 'disabled' : ''}>${icon('plus')} 添加所选照片</button>`, 'photo-picker-modal');
    } else if (modal.type === 'collection-choose') {
      modalRoot.innerHTML = modalFrame('把选中的照片，加入作品集。', `已选择 ${modal.photoIds.length} 张照片；照片也会继续保留在原有作品集中。`, `<div class="collection-choice-list">${state.collections.map(collection=>`<label class="collection-choice"><input type="radio" name="collection-target" value="${esc(collection.id)}" ${modal.targetId === collection.id ? 'checked' : ''}><span class="collection-choice-cover">${collection.cover_url ? `<img src="${esc(collection.cover_url)}" alt="">` : icon('folder')}</span><span class="collection-choice-info"><strong>${esc(collection.title)}</strong><small>${collection.photo_ids.length} 张照片${collection.place ? ` · ${esc(collection.place)}` : ''}</small></span></label>`).join('')}</div>${!state.collections.length ? '<p class="batch-hint">还没有作品集，先为这组照片创建一份吧。</p>' : ''}<button class="button secondary" data-action="create-collection-inline">${icon('plus')} 新建作品集</button>`, `<button class="button ghost" data-action="close-modal">取消</button><button class="button primary" data-action="save-assignment" ${!modal.targetId ? 'disabled' : ''}>${icon('plus')} 添加到作品集</button>`, 'edit-modal');
    }
  }
  function renderModal() {
    if (!state.modal) return;
    const type = state.modal.type;
    if (type === 'settings' && window.ShiguangSettings) {modalRoot.innerHTML=window.ShiguangSettings.renderModal(state.modal);return;}
    if (type === 'video-export' && window.ShiguangVideoExport) {modalRoot.innerHTML=window.ShiguangVideoExport.renderModal(state.modal);return;}
    if (type === 'atlas-edit' && window.ShiguangAtlas) {modalRoot.innerHTML=window.ShiguangAtlas.renderModal(state.modal);return;}
    if (type === 'lightbox') { renderLightbox(); return; }
    if (type === 'photo-export') { renderPhotoExport(); return; }
    if (type === 'import') { renderImportModal(); return; }
    if (['collection-create','collection-edit','collection-delete','existing-photos','collection-choose'].includes(type)) { renderCollectionModal(); return; }
    if (type === 'edit') {
      const photo = state.photos.find(item=>item.id===state.modal.id);
      if (!photo) {closeModal(); return;}
      modalRoot.innerHTML = modalFrame('编辑照片信息', '名称、地点和文字，随时都可以再修改。', `<form id="edit-photo-form"><div class="field"><label for="edit-title">作品名称</label><input id="edit-title" name="title" required maxlength="300" value="${esc(photo.title || photo.filename)}"></div><div class="form-grid"><div class="field"><label for="edit-country">国家</label><select id="edit-country" name="country">${countryOptions(photoCountry(photo))}</select></div><div class="field"><label for="edit-place">城市</label><input id="edit-place" name="city" maxlength="100" placeholder="例如：扬州" value="${esc(photoCity(photo))}"></div><div class="field"><label for="edit-date">拍摄时间</label><input id="edit-date" name="taken_at" type="datetime-local" step="1" required value="${esc((photo.taken_at || '').slice(0,19))}"></div></div><div class="field"><label for="edit-trip">所属旅程</label><input id="edit-trip" name="trip" maxlength="200" placeholder="同名旅程的照片会汇成一篇手记" value="${esc(photo.trip)}"></div><div class="field"><label for="edit-caption">照片背后的故事</label><textarea id="edit-caption" name="caption" rows="4" maxlength="10000" placeholder="记下当时的光线、声音，或者心情…">${esc(photo.caption)}</textarea></div><div class="note-callout">${icon('info')}<span>${photo.date_source === 'file' ? '当前时间来自文件修改时间，可以在这里校正。' : '更改仅保存在相册资料中，不会改写原图。'}</span></div></form>`, `<button class="button ghost" data-action="back-photo" data-id="${esc(photo.id)}">返回照片</button><button class="button primary" data-action="save-photo">${icon('check')} 保存修改</button>`, 'edit-modal');
    } else if (type === 'journal-edit') {
      const key = state.modal.key;
      const photos = state.photos.filter(photo=>journalKey(photo)===key);
      const record = journalRecord(key);
      modalRoot.innerHTML = modalFrame('旅行手记', '这篇手记会和这段旅程的照片一起保存在本地。', `<form id="edit-journal-form"><div class="field"><label for="journal-title">手记标题</label><input id="journal-title" name="title" required maxlength="200" value="${esc(journalTitle(key, photos))}"></div><div class="field"><label for="journal-note">旅行手记</label><textarea id="journal-note" name="note" rows="9" maxlength="50000" placeholder="出发的缘由、难忘的片刻、下一次还想去的地方…">${esc(record?.note || '')}</textarea></div></form>`, `<button class="button ghost" data-action="close-modal">取消</button><button class="button primary" data-action="save-journal">${icon('check')} 保存手记</button>`, 'edit-modal');
    } else if (type === 'journal-delete') {
      modalRoot.innerHTML=modalFrame('删除这段旅途记忆？',esc(state.modal.title),`<div class="note-callout">${icon('info')}<span>这段记忆及手记文字将被移除，照片和作品集会保留。</span></div>`,`<button class="button ghost" data-action="close-modal">取消</button><button class="button danger" data-action="confirm-delete-journal">${icon('trash')} 删除</button>`,'edit-modal');
    } else if (type === 'delete') {
      modalRoot.innerHTML = modalFrame('从相册中移除这张照片？', '将删除相册保存的照片副本及其文字记录。', `<div class="note-callout">${icon('info')}<span>你最初选择导入的文件不会受到影响。移除后可以重新导入，但相册中的编辑和收藏会丢失。</span></div>`, `<button class="button ghost" data-action="back-photo" data-id="${esc(state.modal.id)}">保留照片</button><button class="button danger" data-action="confirm-delete" data-id="${esc(state.modal.id)}">${icon('trash')} 移除照片</button>`, 'edit-modal');
    } else if (type === 'about') {
      modalRoot.innerHTML = modalFrame('使用与保存', '拾光 · 本地私人摄影相册', `<div class="detail-section"><h3>${icon('folder')} 照片保存在哪里</h3><p>导入的原图、预览图、地点、收藏和手记都保存在程序旁的 <strong>album-data</strong> 文件夹。关闭应用后仍然保留，下次双击「拾光相册.exe」打开即可。</p></div><div class="detail-section"><h3>${icon('download')} 备份与迁移</h3><p>在「设置 → 备份与迁移」中导出完整迁移包，保存程序、照片、导入的音乐和全部整理资料。完整解压 ZIP，打开其中的「拾光相册」文件夹，双击「拾光相册.exe」即可继续使用。</p><a class="button secondary small" href="/api/backup" download>${icon('download')} 导出完整迁移包</a><p class="field-hint">照片较多时，准备下载需要一些时间。请等下载完成后再关闭相册。</p></div><div class="detail-section"><h3>${icon('book')} 文字资料</h3><p>JSON 文字资料包含照片信息、作品集、手记、足迹、设置和曲库记录，不包含照片或音乐，不能代替完整迁移包。</p><a class="button secondary small" href="/api/export" download="拾光-相册文字资料.json">${icon('download')} 导出文字资料</a></div><div class="detail-section"><h3>${icon('pin')} 时间和地点</h3><p>自动读取照片拍摄时间和 GPS 坐标；不联网查询地名。地点名称可以在导入时批量填写，或在照片详情中修改。缺少拍摄时间时会使用文件修改时间。</p></div><div class="detail-section"><h3>${icon('lock')} 私人使用</h3><p>相册服务仅在本机运行，不上传照片，不需要账号。手机相册常见的 HEIC 和相机 RAW 文件，请先导出为 JPG、PNG 或其他支持的格式。</p></div>${window.ShiguangMusic?.renderCredits() || ''}`, `<button class="button primary" data-action="close-modal">知道了</button>`, 'edit-modal');
    }
  }
  function importFeedback() {
    if (!state.importing && !state.importResults.length) return '';
    const results = state.importResults;
    const added = results.filter(item=>item.status==='added').length;
    const duplicates = results.filter(item=>item.status==='duplicate').length;
    const errors = results.filter(item=>item.status==='error').length;
    return `<div class="import-progress"><div class="import-status"><span>${state.importing ? `正在批量整理 ${Math.min(results.length + 1,state.files.length)} / ${state.files.length} 张` : '本批照片整理完成'}</span><strong>${Math.round(state.importProgress)}%</strong></div><div class="progress-track"><div class="progress-fill" style="width:${state.importProgress}%"></div></div><p>${added} 张新照片${duplicates ? ` · ${duplicates} 张已有照片${state.modal?.collectionId ? '已关联到作品集' : '无需重复保存'}` : ''}${errors ? ` · ${errors} 张未导入` : ''}</p></div>${errors ? `<div class="import-results">${results.filter(item=>item.status==='error').map(item=>`<div class="result-item error">${icon('info')}<span><strong>${esc(item.name)}</strong><br>${esc(item.error)}</span></div>`).join('')}</div>` : ''}`;
  }
  function renderLightbox() {
    const photo = state.photos.find(item=>item.id===state.lightboxId);
    if (!photo) {closeModal(); return;}
    const photos = filteredPhotos();
    const index = photos.findIndex(item=>item.id===photo.id);
    modalRoot.innerHTML = `<div class="lightbox-backdrop"><section class="lightbox" role="dialog" aria-modal="true" aria-label="${esc(photo.title || photo.filename)}"><header class="lightbox-topbar"><span class="lightbox-title">${icon('photo')} ${esc(photo.title || photo.filename)}</span><div class="lightbox-actions"><button class="button export-photo-button" data-action="export-photo" data-id="${esc(photo.id)}">${icon('download')} 导出作品</button><button class="icon-button ${photo.favorite ? 'active' : ''}" data-action="favorite" data-id="${esc(photo.id)}" aria-label="${photo.favorite ? '取消收藏' : '收藏'}" aria-pressed="${Boolean(photo.favorite)}">${icon('heart')}</button><a class="icon-button" href="${esc(photo.url)}" download="${esc(photo.filename)}" aria-label="下载原图">${icon('download')}</a><button class="icon-button" data-action="close-modal" aria-label="关闭照片">${icon('close')}</button></div></header><div class="lightbox-body"><div class="lightbox-stage"><img id="lightbox-image" src="${esc(imageUrl(photo))}" data-fallback="${esc(photo.thumbnail_url)}" alt="${esc(photo.title || photo.filename)}">${photos.length > 1 ? `<button class="lightbox-nav prev" data-action="previous-photo" aria-label="上一张">${icon('left')}</button><button class="lightbox-nav next" data-action="next-photo" aria-label="下一张">${icon('right')}</button>` : ''}<span class="lightbox-counter">${index >= 0 ? index + 1 : 1} / ${photos.length || 1}</span></div><aside class="photo-details"><div class="detail-section"><span class="eyebrow">THE MOMENT</span><h2>${esc(photo.title || photo.filename)}</h2><span class="location-badge">${icon('pin')}${esc(photoLocation(photo))}</span>${photo.caption ? `<p class="caption-text">${esc(photo.caption)}</p>` : ''}</div><div class="detail-section"><h3>拍摄信息</h3><dl><div class="detail-row"><dt>拍摄时间</dt><dd>${dateText(photo.taken_at)}<br>${esc((photo.taken_at || '').slice(11,19))}</dd></div>${photo.date_source === 'file' ? '<div class="date-source-note">时间来自文件修改时间，可在编辑中校正。</div>' : ''}${photo.trip ? `<div class="detail-row"><dt>所属旅程</dt><dd>${esc(photo.trip)}</dd></div>` : ''}${photo.camera ? `<div class="detail-row"><dt>相机</dt><dd>${esc(photo.camera)}</dd></div>` : ''}${photo.lens ? `<div class="detail-row"><dt>镜头</dt><dd>${esc(photo.lens)}</dd></div>` : ''}<div class="detail-row"><dt>尺寸</dt><dd>${photo.width || '—'} × ${photo.height || '—'}</dd></div><div class="detail-row"><dt>文件大小</dt><dd>${sizeText(photo.size || 0)}</dd></div>${photo.lat != null && photo.lon != null ? `<div class="detail-row"><dt>拍摄坐标</dt><dd>${Number(photo.lat).toFixed(5)}, ${Number(photo.lon).toFixed(5)}</dd></div>` : ''}<div class="detail-row"><dt>文件名称</dt><dd>${esc(photo.filename)}</dd></div></dl></div><div class="detail-actions"><button class="button secondary" data-action="edit-photo" data-id="${esc(photo.id)}">${icon('edit')} 编辑照片信息</button><button class="button ghost remove-photo" data-action="delete-photo" data-id="${esc(photo.id)}">${icon('trash')} 从相册移除</button></div></aside></div></section></div>`;
    const exposure = [['光圈',photo.aperture],['快门',photo.shutter],['ISO',photo.iso],['焦距',photo.focal_length],['曝光补偿',photo.exposure_bias]];
    const rows = exposure.filter(([,value])=>value).map(([label,value])=>`<div class="detail-row"><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join('');
    const dimensions = [...modalRoot.querySelectorAll('.detail-row')].find(row=>row.querySelector('dt')?.textContent==='尺寸');
    if (dimensions) dimensions.insertAdjacentHTML('beforebegin',rows);
    const collection = currentCollection();
    const actions = modalRoot.querySelector('.detail-actions');
    if (actions) actions.insertAdjacentHTML('afterbegin', `${collection ? `<button class="button secondary" data-action="set-collection-cover" data-id="${esc(photo.id)}">${icon('photo')} ${collection.cover_photo_id === photo.id ? '已是作品集封面' : '设为作品集封面'}</button>` : ''}<button class="button secondary" data-action="assign-photo" data-id="${esc(photo.id)}">${icon('folder')} 添加到作品集</button>`);
    const memberships = state.collections.filter(item=>item.photo_ids.includes(photo.id));
    if (memberships.length) modalRoot.querySelector('.photo-details .detail-section')?.insertAdjacentHTML('beforeend', `<p class="batch-hint">所在作品集：${esc(memberships.map(item=>item.title).join('、'))}</p>`);
  }
  function openPhoto(id) { state.lightboxId = id; showModal('lightbox'); }
  function openPhotoExport(id) {
    if (!state.photos.some(photo=>photo.id===id)) throw new Error('这张照片暂时无法导出，请重新打开后重试。');
    state.lightboxId = id;
    showModal('photo-export',{id,previewToken:String(++exportPreviewSequence),previewStatus:'loading',returnModal:{type:'lightbox'}});
  }
  function framedPhotoUrl(id) { return `/api/photos/${encodeURIComponent(id)}/framed`; }
  function framedPreviewError() { return `${icon('info')}<p>暂时无法生成预览，请重试。</p><button class="button secondary small" data-action="retry-export-preview">重新加载</button>`; }
  function renderPhotoExport() {
    const current=state.modal,photo=state.photos.find(item=>item.id===current.id);
    if (!photo) { closeModal(true); return; }
    const ready=current.previewStatus==='ready',failed=current.previewStatus==='error';
    const previewUrl=`${framedPhotoUrl(photo.id)}?preview=1`;
    const body=`<div class="framed-preview-stage" id="framed-preview-stage" aria-busy="${!ready&&!failed}"><img id="framed-preview-image" src="${esc(previewUrl)}" data-export-preview="${esc(current.previewToken)}" data-photo-id="${esc(photo.id)}" alt="白边作品预览" ${ready?'':'hidden'}><div class="framed-preview-status" role="status" ${ready?'hidden':''}>${failed?framedPreviewError():'<span class="spinner"></span><p>正在生成白边作品…</p>'}</div></div>`;
    const footer=`<button class="button ghost" data-action="back-photo" data-id="${esc(photo.id)}">${icon('left')} 返回照片</button><a class="button primary framed-save ${ready?'':'is-disabled'}" href="${esc(framedPhotoUrl(photo.id))}" download data-framed-save aria-disabled="${!ready}" ${ready?'':'tabindex="-1"'}>${icon('download')} 保存图片</a>`;
    modalRoot.innerHTML=modalFrame('白边作品','相机与参数取自拍摄信息，缺失项自动省略。',body,footer,'photo-export-modal');
  }
  function updateFramedPreview(image,status) {
    const current=state.modal;
    if (current?.type!=='photo-export'||current.previewToken!==image.dataset.exportPreview||current.id!==image.dataset.photoId||!image.isConnected) return;
    const ready=status==='ready'&&image.naturalWidth>0;
    current.previewStatus=ready?'ready':'error';image.hidden=!ready;
    const stage=document.getElementById('framed-preview-stage');stage?.setAttribute('aria-busy','false');
    const feedback=modalRoot.querySelector('.framed-preview-status');
    if (feedback) {feedback.hidden=ready;if(!ready)feedback.innerHTML=framedPreviewError();}
    const save=modalRoot.querySelector('[data-framed-save]');
    if (save) {save.setAttribute('aria-disabled',String(!ready));save.classList.toggle('is-disabled',!ready);if(ready)save.removeAttribute('tabindex');else save.setAttribute('tabindex','-1');}
  }
  function navigatePhoto(delta) {
    const photos = filteredPhotos();
    if (photos.length < 2) return;
    const index = photos.findIndex(photo=>photo.id===state.lightboxId);
    state.lightboxId = photos[(index + delta + photos.length) % photos.length].id;
    renderLightbox();
  }
  function setFiles(files, fromFolder = false) {
    if (state.importing) return;
    if (state.modal?.type !== 'import') openImport();
    captureImportDraft();
    const incoming = [...files];
    const supported = incoming.filter(supportedPhoto);
    state.skippedFiles += incoming.length - supported.length;
    // File metadata cannot identify an image: different source folders may
    // contain distinct photos with identical names, sizes and timestamps.
    // Keep every selected file; the server deduplicates actual image bytes.
    state.files.push(...supported);
    if (fromFolder && incoming.length) {
      const folderName = incoming.find(file=>file.webkitRelativePath)?.webkitRelativePath.split('/')[0];
      if (folderName) {
        state.modal.suggestedTitle = folderName;
        if (!state.modal.collectionId && supported.length) state.modal.collectionId = '__folder__';
      }
    }
    state.importResults = [];
    state.importProgress = 0;
    state.importDone = false;
    renderModal();
    if (!supported.length && incoming.length) toast('这些文件不是支持的照片格式，请选择 JPG、PNG 或其他支持格式。','error');
  }
  async function importFiles() {
    if (state.importing || !state.files.length || state.importDone) return;
    captureImportDraft();
    const draft = state.modal;
    draft.city = String(draft.city ?? draft.place ?? '').trim(); draft.place = draft.city; draft.country = String(draft.country || '').trim().toUpperCase(); draft.trip = draft.trip.trim();
    if (draft.collectionId === '__create__') throw new Error('请先创建或选择作品集。');
    state.importing = true;
    state.importResults = [];
    state.importProgress = 0;
    renderModal();
    try {
      if (draft.collectionId === '__folder__') {
        const result = await api('/api/collections', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:draft.suggestedTitle,place:draft.place})});
        storeCollection(result.collection); draft.collectionId = result.collection.id; renderModal();
      }
    } catch(error) { state.importing=false;renderModal();throw error; }
    const batch = [...state.files];
    for (let i = 0; i < batch.length; i++) {
      const file = batch[i];
      try {
        if (file.size > 100 * 1024 * 1024) throw new Error('超过每张 100 MB 的大小限制。');
        if (!/\.(jpe?g|png|webp|gif|bmp|tiff?)$/i.test(file.name)) throw new Error('暂不支持此格式，请先导出为 JPG 或 PNG。');
        const query = new URLSearchParams({filename: file.name, modified: localISO(new Date(file.lastModified)), country:draft.country, city:draft.city, place:draft.city, trip:draft.trip});
        if (draft.collectionId) query.set('collection_id',draft.collectionId);
        const result = await api(`/api/import?${query}`, {method:'POST', headers:{'Content-Type': 'application/octet-stream'}, body:file});
        if (result.collection) storeCollection(result.collection);
        state.importResults.push({name:file.name, status: result.duplicate ? 'duplicate' : 'added'});
      } catch (error) { state.importResults.push({name:file.name, status:'error', error:error.message}); }
      state.importProgress = (i + 1) / batch.length * 100;
      const feedback = document.getElementById('import-feedback');
      if (feedback) feedback.innerHTML = importFeedback();
    }
    state.importing = false;
    state.importDone = true;
    if (draft.collectionId && collectionRecord(draft.collectionId)) openCollection(draft.collectionId,false);
    await refresh();
    renderModal();
    const added = state.importResults.filter(item=>item.status==='added').length;
    const duplicates = state.importResults.filter(item=>item.status==='duplicate').length;
    if (added || duplicates) toast(draft.collectionId ? `已整理到作品集：${added} 张新照片${duplicates ? `，复用 ${duplicates} 张已有照片` : ''}。` : `已导入 ${added} 张新照片${duplicates ? `，${duplicates} 张已有照片无需重复保存` : ''}。`);
    document.querySelector('#modal-root [data-action="close-modal"]:not(.close-modal)')?.focus();
  }
  async function updatePhoto(id, fields) {
    const result = await api(`/api/photos/${encodeURIComponent(id)}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(fields)});
    const updated = result.photo || result;
    const index = state.photos.findIndex(photo=>photo.id===id);
    if (index >= 0) state.photos[index] = updated;
    return updated;
  }
  async function favorite(id) {
    const photo = state.photos.find(item=>item.id===id);
    if (!photo) return;
    const favorite = !photo.favorite;
    await updatePhoto(id, {favorite});
    render();
    if (state.modal?.type === 'lightbox') renderLightbox();
    toast(favorite ? '已加入收藏。' : '已取消收藏。');
  }
  async function savePhoto() {
    const form = document.getElementById('edit-photo-form');
    if (!form.reportValidity()) return;
    const button = modalRoot.querySelector('[data-action="save-photo"]');
    button.disabled = true;
    try {
      const fields = Object.fromEntries(new FormData(form));
      for (const key of Object.keys(fields)) fields[key] = fields[key].trim();
      fields.country = String(fields.country || '').toUpperCase();
      fields.place = fields.city;
      const id = state.modal.id;
      const previous = state.photos.find(photo=>photo.id===id);
      if (fields.city.length>100 && fields.city===photoCity(previous)) { delete fields.city; delete fields.place; }
      if (fields.taken_at === previous?.taken_at || fields.taken_at === previous?.taken_at?.slice(0,16)) delete fields.taken_at;
      await updatePhoto(id, fields);
      render();
      openPhoto(id);
      toast('照片信息已保存。');
    } finally { if (button.isConnected) button.disabled = false; }
  }
  async function saveJournal() {
    const form = document.getElementById('edit-journal-form');
    if (!form.reportValidity()) return;
    const button = modalRoot.querySelector('[data-action="save-journal"]');
    button.disabled = true;
    try {
      const fields = Object.fromEntries(new FormData(form));
      fields.title = fields.title.trim();
      if (!fields.title) throw new Error('请填写手记标题。');
      const key = state.modal.key;
      const result = await api(`/api/journals/${encodeURIComponent(key)}`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)});
      const journal = result.journal || result;
      state.journals = [...state.journals.filter(item=>item.key!==key), journal];
      state.deletedJournalKeys.delete(key);
      if (state.scope?.key === key) state.scope.title = journal.title;
      closeModal();
      render();
      toast('这段旅途的故事，已经保存。');
    } finally { if (button.isConnected) button.disabled = false; }
  }
  async function deleteJournal(target) {
    const modal=state.modal;
    if(modal?.type!=='journal-delete'||modal.busy)return;
    modal.busy=true;target.disabled=true;
    try {
      const key=modal.key;
      await api(`/api/journals/${encodeURIComponent(key)}`,{method:'DELETE'});
      state.journals=state.journals.filter(item=>item.key!==key);
      state.deletedJournalKeys.add(key);
      if(state.scope?.type==='journal'&&state.scope.key===key){state.scope=null;state.view='journal';state.search='';clearSelection();}
      closeModal(true);render();toast('旅途记忆已删除。');
    } finally {modal.busy=false;if(target.isConnected)target.disabled=false;}
  }
  async function saveCollection() {
    const form = document.getElementById('collection-form');
    if (!form.reportValidity() || state.modal.busy) return;
    const modal = state.modal;
    const fields = Object.fromEntries(new FormData(form));
    for (const key of Object.keys(fields)) fields[key] = fields[key].trim();
    if (!fields.title) {document.getElementById('collection-title').focus();throw new Error('请填写作品集名称。');}
    if ('cover_photo_id' in fields && !fields.cover_photo_id) fields.cover_photo_id = null;
    const button = modalRoot.querySelector('[data-action="save-collection"]');
    button.disabled = true; modal.busy = true;
    try {
      const result = await api(modal.id ? `/api/collections/${encodeURIComponent(modal.id)}` : '/api/collections', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)});
      storeCollection(result.collection);
      if (modal.returnModal) {
        const returning = modal.returnModal;
        if (returning.type === 'import') returning.collectionId = result.collection.id;
        if (returning.type === 'collection-choose') returning.targetId = result.collection.id;
        state.modal = returning; render(); renderModal();
      } else {
        closeModal(true); openCollection(result.collection.id);
      }
      toast(modal.id ? '作品集已保存。' : '作品集已创建，可以批量加入照片了。');
    } finally {modal.busy=false;if(button.isConnected)button.disabled=false;}
  }
  async function addMembership(collectionId, photoIds) {
    const result = await api(`/api/collections/${encodeURIComponent(collectionId)}/photos`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({photo_ids:photoIds})});
    storeCollection(result.collection); return result;
  }
  async function saveExistingPhotos() {
    const modal = state.modal;
    if (modal.busy || !modal.selected.size) return;
    const button=document.getElementById('save-existing'); button.disabled=true;modal.busy=true;
    try {
      const result=await addMembership(modal.collectionId,[...modal.selected]);
      closeModal(true);render();toast(`已添加 ${result.added_count} 张照片，原图无需重复保存。`);
    } finally {modal.busy=false;if(button.isConnected)button.disabled=false;}
  }
  async function saveAssignment() {
    const modal=state.modal;
    if(modal.busy || !modal.targetId) return;
    const button=modalRoot.querySelector('[data-action="save-assignment"]');button.disabled=true;modal.busy=true;
    try {
      const result=await addMembership(modal.targetId,modal.photoIds);
      modal.busy=false;closeModal();clearSelection();render();
      if(state.modal?.type==='lightbox')renderLightbox();
      toast(result.added_count ? `已添加 ${result.added_count} 张照片到作品集。` : '所选照片已经在这份作品集中。');
    } finally {modal.busy=false;if(button.isConnected)button.disabled=false;}
  }
  async function action(name, target) {
    const id = target.dataset.id;
    if (name.startsWith('settings-') && window.ShiguangSettings) {await window.ShiguangSettings.handleAction(name,target);return;}
    if (name.startsWith('video-') && window.ShiguangVideoExport) {await window.ShiguangVideoExport.handleAction(name,target);return;}
    if (name === 'fullscreen-toggle' && window.ShiguangFullscreen) {await window.ShiguangFullscreen.handleToggle();return;}
    if (name === 'music-toggle' && window.ShiguangMusic) {window.ShiguangMusic.handleToggle();return;}
    if (name.startsWith('atlas-') && window.ShiguangAtlas) {await window.ShiguangAtlas.handleAction(name,target);return;}
    if (name === 'create-collection') {showModal('collection-create');return;}
    if (name === 'create-collection-inline') {
      if(state.modal?.type==='import')captureImportDraft();
      showModal('collection-create',{returnModal:state.modal,title:state.modal?.suggestedTitle || '',place:state.modal?.place || ''});return;
    }
    if (name === 'save-collection') {await saveCollection();return;}
    if (name === 'open-collection') {openCollection(id);return;}
    if (name === 'edit-collection') {showModal('collection-edit',{id});return;}
    if (name === 'delete-collection') {showModal('collection-delete',{id,returnModal:state.modal?.type==='collection-edit' ? state.modal : null});return;}
    if (name === 'confirm-delete-collection') {
      if(state.modal.busy)return;state.modal.busy=true;target.disabled=true;
      try {
        await api(`/api/collections/${encodeURIComponent(id)}`,{method:'DELETE'});
        state.collections=state.collections.filter(item=>item.id!==id);
        if(state.scope?.type==='collection'&&state.scope.key===id){state.scope=null;state.view='collections';clearSelection();}
        closeModal(true);render();toast('作品集已删除，照片仍保留在全部照片中。');
      } finally {if(state.modal)state.modal.busy=false;if(target.isConnected)target.disabled=false;}
      return;
    }
    if(name==='add-existing') {showModal('existing-photos',{collectionId:id || currentCollection()?.id,selected:new Set(),query:''});return;}
    if(name==='existing-select-all') {existingPickerPhotos().forEach(photo=>state.modal.selected.add(photo.id));updateExistingPicker();return;}
    if(name==='save-existing') {await saveExistingPhotos();return;}
    if(name==='toggle-selection') {state.selectionMode=!state.selectionMode;if(!state.selectionMode)state.selectedIds.clear();render();return;}
    if(name==='select-photo') {state.selectionMode=true;if(state.selectedIds.has(id))state.selectedIds.delete(id);else state.selectedIds.add(id);render();return;}
    if(name==='select-all') {filteredPhotos().forEach(photo=>state.selectedIds.add(photo.id));updateCollection();return;}
    if(name==='clear-selection') {state.selectedIds.clear();updateCollection();return;}
    if(name==='assign-selected' || name==='assign-photo') {
      const photoIds=name==='assign-photo'?[id]:[...state.selectedIds];
      if(photoIds.length)showModal('collection-choose',{photoIds,targetId:'',returnModal:name==='assign-photo'?{type:'lightbox'}:null});return;
    }
    if(name==='save-assignment') {await saveAssignment();return;}
    if(name==='remove-selected') {
      const collection=currentCollection();if(!collection||!state.selectedIds.size)return;
      target.disabled=true;
      try{const result=await api(`/api/collections/${encodeURIComponent(collection.id)}/photos`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({photo_ids:[...state.selectedIds]})});storeCollection(result.collection);clearSelection();render();toast(`已从作品集移出 ${result.removed_count} 张，全部照片中的原图仍保留。`);}finally{if(target.isConnected)target.disabled=false;}
      return;
    }
    if(name==='set-collection-cover') {
      const collection=currentCollection();if(!collection)return;target.disabled=true;
      try{const result=await api(`/api/collections/${encodeURIComponent(collection.id)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cover_photo_id:id})});storeCollection(result.collection);render();renderLightbox();toast('作品集封面已更新。');}finally{if(target.isConnected)target.disabled=false;}
      return;
    }
    if(name==='export-photo') {openPhotoExport(id || state.lightboxId);return;}
    if(name==='retry-export-preview') {
      if(state.modal?.type==='photo-export'){state.modal.previewToken=String(++exportPreviewSequence);state.modal.previewStatus='loading';renderPhotoExport();setTimeout(()=>modalRoot.querySelector('[data-action="back-photo"]')?.focus(),0);}
      return;
    }
    if(name==='choose-folder'){document.getElementById('photo-folder').click();return;}
    if(name==='clear-files'){captureImportDraft();state.files=[];state.importResults=[];state.importProgress=0;state.importDone=false;state.skippedFiles=0;renderModal();return;}
    if(name==='collections-home'){state.view='collections';state.scope=null;state.search='';state.layout='grid';clearSelection();render();return;}
    if(name==='browse-photos'){state.view='photos';state.scope=null;state.search='';state.layout='grid';clearSelection();render();return;}
    if (name === 'nav' || name === 'home') {
      state.view = name === 'home' ? 'collections' : target.dataset.view;
      state.scope = null; state.search = ''; state.layout = 'grid'; clearSelection();render();
    } else if (name === 'import') {
      openImport();
    } else if (name === 'choose-files') document.getElementById('photo-files').click();
    else if (name === 'start-import') await importFiles();
    else if (name === 'close-modal' || name === 'backdrop-close') closeModal();
    else if (name === 'layout-grid' || name === 'layout-timeline') {
      if (state.view === 'timeline' && name === 'layout-grid') state.view='photos';
      state.layout=name === 'layout-grid' ? 'grid' : 'timeline'; render();
    } else if (name === 'clear-search') {state.search=''; render();}
    else if (name === 'open-photo' || name === 'back-photo') openPhoto(id);
    else if (name === 'next-photo') navigatePhoto(1);
    else if (name === 'previous-photo') navigatePhoto(-1);
    else if (name === 'favorite') await favorite(id);
    else if (name === 'edit-photo') showModal('edit', {id});
    else if (name === 'save-photo') await savePhoto();
    else if (name === 'delete-photo') showModal('delete', {id});
    else if (name === 'confirm-delete') {
      target.disabled = true;
      try { await api(`/api/photos/${encodeURIComponent(id)}`, {method:'DELETE'}); closeModal(); await refresh(); toast('已移除相册中的照片副本。'); }
      finally {if (target.isConnected) target.disabled = false;}
    } else if (name === 'open-place') {
      openPlaceScope(target.dataset.key,target.dataset.country || '');
    } else if (name === 'open-journal') {
      const key=target.dataset.key;
      state.scope={type:'journal',key,title:journalTitle(key,state.photos.filter(photo=>journalKey(photo)===key))};
      state.view='photos'; state.search=''; state.layout='grid'; clearSelection();render();
    } else if (name === 'clear-scope') {state.view=state.scope.type === 'collection' ? 'collections' : ['place','country'].includes(state.scope.type) ? 'places' : 'journal'; state.scope=null; state.search='';state.layout='grid';clearSelection(); render();}
    else if (name === 'edit-journal') showModal('journal-edit', {key:state.scope.key});
    else if (name === 'save-journal') await saveJournal();
    else if (name === 'delete-journal') {
      const key=target.dataset.key;
      showModal('journal-delete',{key,title:journalTitle(key,state.photos.filter(photo=>journalKey(photo)===key))});
    }
    else if (name === 'confirm-delete-journal') await deleteJournal(target);
    else if (name === 'about') showModal('about');
    else if (name === 'retry') {state.loading=true;render();await refresh();}
  }
  document.addEventListener('click', event => {
    if(event.target.closest('a[data-framed-save][aria-disabled="true"]')){event.preventDefault();return;}
    const target = event.target.closest('[data-action]');
    if (!target || target.disabled) return;
    if (target.dataset.action === 'backdrop-close' && event.target !== target) return;
    event.preventDefault();
    Promise.resolve(action(target.dataset.action, target)).catch(error=>toast(error.message,'error'));
  });
  document.addEventListener('input', event => {
    if (event.target.id === 'photo-search') {state.search=event.target.value;updateCollection();}
    if (event.target.id === 'existing-search' && state.modal?.type==='existing-photos') {state.modal.query=event.target.value;updateExistingPicker();}
  });
  document.addEventListener('change', event => {
    if (event.target.id === 'photo-files') setFiles(event.target.files);
    if (event.target.id === 'photo-folder') setFiles(event.target.files,true);
    if (event.target.id === 'photo-sort') {state.sort=event.target.value;updateCollection();}
    if (['import-country','edit-country'].includes(event.target.id)) {
      event.target.dataset.countryEdited='true';
      if (state.modal?.type==='import') captureImportDraft();
    }
    if(event.target.dataset.existingPhoto && state.modal?.type==='existing-photos') {
      const id=event.target.dataset.existingPhoto;
      if(event.target.checked)state.modal.selected.add(id);else state.modal.selected.delete(id);
      document.getElementById('existing-selection-count').textContent=`已选择 ${state.modal.selected.size} 张`;
      document.getElementById('save-existing').disabled=!state.modal.selected.size;
      event.target.closest('.existing-photo-tile')?.classList.toggle('selected',event.target.checked);
    }
    if(event.target.name==='collection-target' && state.modal?.type==='collection-choose') {
      state.modal.targetId=event.target.value;
      modalRoot.querySelector('[data-action="save-assignment"]').disabled=false;
    }
    if(event.target.id==='import-collection' && state.modal?.type==='import') {
      const previous=state.modal.collectionId || '';
      captureImportDraft();
      if(event.target.value==='__create__') {
        state.modal.collectionId=previous;
        const returning=state.modal;
        showModal('collection-create',{returnModal:returning,title:returning.suggestedTitle || '',place:returning.place || ''});
      } else {
        renderModal();
      }
    }
  });
  document.addEventListener('submit', event=>{event.preventDefault();if(event.target.id==='collection-form')Promise.resolve(saveCollection()).catch(error=>toast(error.message,'error'));});
  document.addEventListener('keydown', event => {
    if (!state.modal) return;
    if (event.key === 'Escape') {event.preventDefault();closeModal();return;}
    if (state.modal.type === 'lightbox' && !/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) {
      if (event.key === 'ArrowLeft') {event.preventDefault();navigatePhoto(-1);}
      if (event.key === 'ArrowRight') {event.preventDefault();navigatePhoto(1);}
    }
    if (event.key === 'Tab') {
      const focusable=[...modalRoot.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]):not([type="file"]),select:not([disabled]),textarea:not([disabled])')].filter(el=>el.getClientRects().length);
      const first=focusable[0], last=focusable[focusable.length-1];
      if (!first) {event.preventDefault();return;}
      if (event.shiftKey && (document.activeElement===first || !modalRoot.contains(document.activeElement))) {event.preventDefault();last.focus();}
      else if (!event.shiftKey && (document.activeElement===last || !modalRoot.contains(document.activeElement))) {event.preventDefault();first.focus();}
    }
  });
  let dragDepth=0;
  const isFileDrag=event=>[...(event.dataTransfer?.types || [])].includes('Files');
  document.addEventListener('dragenter',event=>{
    if (!isFileDrag(event) || state.importing) return;
    event.preventDefault();dragDepth++;
    if (state.modal?.type==='import') document.getElementById('import-drop')?.classList.add('drag-over');
    else if (!state.modal) document.getElementById('global-drop')?.classList.add('visible');
  });
  document.addEventListener('dragover',event=>{if(isFileDrag(event)) {event.preventDefault();if(event.dataTransfer) event.dataTransfer.dropEffect=state.importing ? 'none' : 'copy';}});
  document.addEventListener('dragleave',event=>{if (!isFileDrag(event)) return;dragDepth=Math.max(0,dragDepth-1);if (!dragDepth) {document.getElementById('global-drop')?.classList.remove('visible');document.getElementById('import-drop')?.classList.remove('drag-over');}});
  document.addEventListener('drop',event=>{
    if (!isFileDrag(event)) return;
    event.preventDefault();dragDepth=0;
    document.getElementById('global-drop')?.classList.remove('visible');
    document.getElementById('import-drop')?.classList.remove('drag-over');
    if (state.importing) return;
    if (state.modal && state.modal.type!=='import') {toast('请先关闭当前窗口，再导入照片。','error');return;}
    if (event.dataTransfer.files.length) setFiles(event.dataTransfer.files);
  });
  document.addEventListener('load',event=>{if(event.target.id==='framed-preview-image')updateFramedPreview(event.target,'ready');},true);
  document.addEventListener('error',event=>{
    const image=event.target;
    if(image.id==='framed-preview-image'){updateFramedPreview(image,'error');return;}
    if (image.id==='lightbox-image' && image.dataset.fallback && image.src !== new URL(image.dataset.fallback,location.href).href) {image.src=image.dataset.fallback;image.dataset.fallback='';}
  },true);
  window.addEventListener('beforeunload',event=>{if(window.shiguangIsImporting()){event.preventDefault();event.returnValue='';}});
  render();
  void window.ShiguangFullscreen?.initialize();
  Promise.resolve(window.ShiguangSettings?.initialize()).catch(()=>{}).finally(()=>refresh());
})();
