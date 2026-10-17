/* Persistent appearance and a local, user-managed listening library. */
(() => {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const THEMES=['classic','italian'];
  const model={theme:'classic',music_volume:.22,tracks:[],loaded:false,promise:null,importing:false,progress:'',pendingRemove:null};
  let host=null,volumeQueue=Promise.resolve(),trackQueue=Promise.resolve();
  const active=()=>host?.getState().modal?.type==='settings'?host.getState().modal:null;
  const icon=name=>host?.icon(name)||'';
  const playIcon=playing=>`<svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${playing?'<path d="M7 5h4v14H7zm6 0h4v14h-4z"/>':'<path d="m8 4 13 8-13 8z"/>'}</svg>`;
  function applyTheme(theme){model.theme=THEMES.includes(theme)?theme:'classic';document.documentElement.dataset.theme=model.theme;const meta=document.querySelector('meta[name="theme-color"]');if(meta)meta.content=model.theme==='italian'?'#f3e9d7':'#f5f3ef';}
  function configure(context){host=context;window.ShiguangOpeningManager?.configure({getState:host.getState,api:host.api,icon:host.icon,toast:host.toast,redraw:refresh,isBusy:()=>model.importing||Boolean(active()?.busy)});window.ShiguangOnlineMusic?.configure({getState:host.getState,api:host.api,redraw:refresh,toast:host.toast,isBusy:()=>model.importing||Boolean(active()?.busy),hasTrack:id=>model.tracks.some(track=>track.id===id),volume:()=>model.music_volume,setImporting:(value,message)=>{model.importing=value;model.progress=message;if(active())active().busy=value;},imported:async()=>{model.tracks=await window.ShiguangMusic.load(true);}});}
  async function initialize(force=false){
    if(model.promise)return model.promise;if(model.loaded&&!force)return;
    model.promise=Promise.all([host.api('/api/settings'),window.ShiguangMusic.load(force)]).then(([settings,tracks])=>{applyTheme(settings.theme);model.music_volume=typeof settings.music_volume==='number'?settings.music_volume:.22;window.ShiguangMusic.setVolume(model.music_volume);model.tracks=tracks;model.loaded=true;}).finally(()=>{model.promise=null;});return model.promise;
  }
  async function open(){host.showModal('settings',{tab:'appearance',loading:!model.loaded,error:''});const current=active();try{await initialize();}catch(error){if(active()===current)current.error=error.message;}finally{if(active()===current){current.loading=false;refresh();}}}
  function previewCard(theme,title,subtitle,image){return `<button type="button" class="theme-card ${model.theme===theme?'is-selected':''}" data-theme="${theme}" data-action="settings-theme" aria-pressed="${model.theme===theme}" ${active()?.busy?'disabled':''}><span class="theme-preview theme-preview-${theme}"><span class="theme-preview-sidebar"></span><span class="theme-preview-copy"><span></span><span></span><span></span></span><img src="${image}" alt="" loading="eager"></span><span class="theme-info"><strong>${title}</strong><span>${subtitle}</span><span class="theme-selected-mark">${model.theme===theme?icon('check'):''}</span></span></button>`;}
  function trackRow(track){
    const current=window.ShiguangMusic.current(),playing=current.playing&&current.id===track.id,disabled=model.importing||active()?.busy?'disabled':'';
    const meta=track.origin==='builtin'?['内置',track.artist||'Scott Buckley']:[track.artist||'已导入',String(track.original_name||'').split('.').pop().toUpperCase(),track.bytes?`${(track.bytes/1024/1024).toFixed(1)} MB`:''];
    return `<div class="settings-track ${track.enabled===false?'is-disabled':''}" data-track-id="${esc(track.id)}"><label class="track-enabled"><input type="checkbox" data-settings-enabled="${esc(track.id)}" ${track.enabled!==false?'checked':''} aria-label="加入随机播放：${esc(track.title)}" ${disabled}></label><div class="track-copy"><input class="track-title" data-settings-title="${esc(track.id)}" aria-label="曲名：${esc(track.title)}" value="${esc(track.title)}" maxlength="120" ${disabled}><span class="track-meta">${esc(meta.filter(Boolean).join(' · '))}</span></div><div class="track-actions"><button class="icon-button track-preview" data-action="settings-preview" data-id="${esc(track.id)}" aria-label="${playing?'停止试听':'试听'}：${esc(track.title)}" aria-pressed="${playing}" ${disabled}>${playIcon(playing)}</button>${track.origin==='imported'?`<button class="icon-button track-remove" data-action="settings-remove" data-id="${esc(track.id)}" aria-label="移除：${esc(track.title)}" ${disabled}>${icon('trash')}</button>`:''}</div>${model.pendingRemove===track.id?`<div class="settings-track-confirm"><span>从曲库移除这首音乐？</span><button class="button ghost small" data-action="settings-remove-cancel">保留</button><button class="button danger small" data-action="settings-remove-confirm" data-id="${esc(track.id)}">移除</button></div>`:''}</div>`;
  }
  function audioPanel(){return `<div class="settings-audio-toolbar"><div><input type="file" id="settings-audio-files" class="visually-hidden" multiple accept=".mp3,.wav,.m4a,.aac,.ogg,.flac" ${model.importing?'disabled':''}><button class="button secondary" data-action="settings-import" ${model.importing?'disabled':''}>${icon('plus')} 本地导入</button><button class="button secondary" data-action="settings-online-toggle" ${model.importing?'disabled':''}>在线导入</button></div><label class="settings-volume" for="settings-volume">音量 <input id="settings-volume" type="range" min="0" max="100" value="${Math.round(model.music_volume*100)}" aria-label="背景音乐音量" ${model.importing?'disabled':''}><output id="settings-volume-value">${Math.round(model.music_volume*100)}%</output></label></div><div id="settings-online-music">${window.ShiguangOnlineMusic?.render()||''}</div><p class="settings-audio-hint">勾选的曲目参与随机播放</p><div class="settings-import-status" role="status">${esc(model.progress)}</div><div class="settings-audio-list">${model.tracks.length?model.tracks.map(trackRow).join(''):'<p class="settings-audio-empty">导入几首喜欢的音乐。</p>'}</div>${window.ShiguangMusic.renderCredits()}`;}
  function backupPanel(current){
    const disabled=current.busy||model.importing;
    return `<section class="settings-backup-panel"><p class="settings-backup-summary">将程序和相册资料一起保存，换一台电脑继续使用。</p><ul class="settings-backup-contents"><li>照片原图与预览</li><li>导入的音乐</li><li>作品集、手记与足迹</li><li>收藏与偏好设置</li></ul><div class="settings-backup-actions"><a class="button primary settings-backup-download ${disabled?'is-disabled':''}" href="/api/backup" download aria-disabled="${Boolean(disabled)}" ${disabled?'tabindex="-1"':''}>${icon('download')} 导出完整迁移包</a></div><p class="settings-backup-hint">照片较多时，准备下载需要一些时间。请等下载完成后再关闭相册。</p><div class="settings-backup-restore"><h3>在另一台电脑打开</h3><p>完整解压 ZIP，打开其中的「拾光相册」文件夹，双击「拾光相册.exe」。</p></div></section>`;
  }
  function dataPanel(){
    return `<section class="settings-data-panel"><span class="eyebrow">A FRESH START</span><h3>一键初始化</h3><p>将相册恢复到初次打开的状态。</p><ul><li>清除相册中的照片副本、预览、收藏与编辑记录</li><li>清除作品集、旅行手记、足迹和导入的音乐</li><li>清除自定义开场白，恢复默认句库与设置</li></ul><p>保留程序、主题、内置地图和内置音乐。最初导入的源文件，以及已经另存的作品、视频和备份不受影响。</p><a class="inline-link settings-backup-download" href="/api/backup" download>先导出完整迁移包 ${icon('download')}</a><div class="settings-initialize-box"><label for="initialize-confirmation">此操作无法撤销。输入「清空相册」后继续。</label><input id="initialize-confirmation" type="text" autocomplete="off" placeholder="清空相册" maxlength="4"><button class="button danger" data-action="settings-initialize" disabled>${icon('trash')} 一键初始化</button><p id="initialize-status" role="status"></p></div></section>`;
  }
  async function initializeAlbum(){
    const current=active(),field=document.getElementById('initialize-confirmation');
    if(!current||current.busy||current.tab!=='data'||field?.value!=='清空相册')return;
    current.busy=true;refresh();
    try {
      if(!await window.ShiguangConfirm?.ask({title:'确认清空整个相册？',message:'照片副本、导入音乐、作品集、手记、足迹和个人设置都将清除。清除后无法撤销，请确认已保存需要的备份。',label:'清空并初始化'}))return;
      current.initializing=true;
      const status=document.getElementById('initialize-status');if(status)status.textContent='正在初始化，请稍候…';
      await Promise.all([volumeQueue.catch(()=>{}),trackQueue.catch(()=>{})]);
      window.ShiguangOnlineMusic?.onClose();window.ShiguangMusic.stop();
      const result=await host.api('/api/initialize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation:'清空相册'})});
      if(!result.initialized)throw new Error('初始化尚未完成，请重试。');
      try{localStorage.clear();sessionStorage.clear();}catch(_error){}
      if(result.cleanup_pending){if(status)status.textContent='资料已初始化，仍有部分缓存待清理。';host.toast('资料已初始化，仍有部分缓存待清理。');}
      window.location.reload();
    } catch(error) {
      const status=document.getElementById('initialize-status');if(status)status.textContent=error.message;host.toast(error.message,'error');
    } finally {current.initializing=false;current.busy=false;if(active()===current){refresh();field?.focus();}}
  }
  function panelMarkup(current){
    return current.tab==='data'?dataPanel():current.tab==='opening'?window.ShiguangOpeningManager.render():current.tab==='backup'?backupPanel(current):current.loading?'<div class="loading-state"><span class="spinner"></span></div>':current.error?`<div class="settings-error"><p>${esc(current.error)}</p><button class="button secondary" data-action="settings-retry">重新加载</button></div>`:current.tab==='audio'?audioPanel():`<div class="settings-theme-grid">${previewCard('classic','经典','暖白 · 蓝灰 · 旅行画册','/assets/european-sketch.png')}${previewCard('italian','意大利油画','暖金 · 陶土 · 托斯卡纳','/assets/italian-oil-vignette.png')}</div>`;
  }
  const panelKey=current=>['opening','data'].includes(current.tab)?current.tab+':ready':`${current.tab}:${current.loading?'loading':current.error?'error':'ready'}`;
  function renderModal(current){
    return host.modalFrame('设置','',`<div class="settings-tabs" role="group" aria-label="设置分类"><button class="settings-tab ${current.tab==='appearance'?'active':''}" data-action="settings-tab" data-tab="appearance" aria-pressed="${current.tab==='appearance'}" ${current.busy?'disabled':''}>外观主题</button><button class="settings-tab ${current.tab==='audio'?'active':''}" data-action="settings-tab" data-tab="audio" aria-pressed="${current.tab==='audio'}" ${current.busy?'disabled':''}>背景音乐</button><button class="settings-tab ${current.tab==='opening'?'active':''}" data-action="settings-tab" data-tab="opening" aria-pressed="${current.tab==='opening'}" ${current.busy?'disabled':''}>开场白管理</button><button class="settings-tab ${current.tab==='backup'?'active':''}" data-action="settings-tab" data-tab="backup" aria-pressed="${current.tab==='backup'}" ${current.busy?'disabled':''}>备份与迁移</button><button class="settings-tab ${current.tab==='data'?'active':''}" data-action="settings-tab" data-tab="data" aria-pressed="${current.tab==='data'}" ${current.busy?'disabled':''}>数据管理</button></div><div id="settings-panel" data-panel="${panelKey(current)}">${panelMarkup(current)}</div>`,`<button class="button primary" data-action="close-modal" ${current.busy?'disabled':''}>完成</button>`,`settings-modal${['opening','data'].includes(current.tab)?' opening-settings':''}`);
  }
  function refresh(){
    const current=active();if(!current)return;
    const panel=document.getElementById('settings-panel');
    if(!panel){host.renderModal();return;}
    const modal=panel.closest('.settings-modal'),scroll=modal.scrollTop,key=panelKey(current),changed=panel.dataset.panel!==key;
    modal.classList.toggle('opening-settings',['opening','data'].includes(current.tab));
    if(changed){panel.innerHTML=panelMarkup(current);panel.dataset.panel=key;}
    modal.querySelectorAll('.settings-tab').forEach(button=>{
      const selected=button.dataset.tab===current.tab;button.classList.toggle('active',selected);button.setAttribute('aria-pressed',String(selected));button.disabled=Boolean(current.busy);
    });
    modal.querySelectorAll('[data-action="close-modal"]').forEach(button=>{button.disabled=Boolean(current.busy);});
    if(current.tab==='opening')window.ShiguangOpeningManager?.sync();
    if(current.tab==='data'){const field=panel.querySelector('#initialize-confirmation');field.disabled=Boolean(current.busy);panel.querySelector('[data-action="settings-initialize"]').disabled=Boolean(current.busy)||field.value!=='清空相册';}
    if(current.tab==='audio'&&!current.loading&&!current.error){syncAudioPanel(panel);window.ShiguangOnlineMusic?.update();}
    if(current.tab==='appearance')modal.querySelectorAll('.theme-card').forEach(card=>{
      const selected=card.dataset.theme===model.theme;card.classList.toggle('is-selected',selected);card.disabled=Boolean(current.busy);
      if(card.getAttribute('aria-pressed')!==String(selected)){
        card.setAttribute('aria-pressed',String(selected));card.querySelector('.theme-selected-mark').innerHTML=selected?icon('check'):'';
      }
    });
    modal.scrollTop=changed?0:scroll;
  }
  function syncAudioPanel(panel){
    const busy=model.importing||Boolean(active()?.busy);
    panel.querySelectorAll('#settings-audio-files,[data-action="settings-import"],[data-action="settings-online-toggle"],#settings-volume').forEach(field=>{field.disabled=busy;});
    panel.querySelector('.settings-import-status').textContent=model.progress;
    const list=panel.querySelector('.settings-audio-list'),rows=new Map([...list.querySelectorAll('[data-track-id]')].map(row=>[row.dataset.trackId,row]));
    const ids=new Set(model.tracks.map(track=>track.id));
    rows.forEach((row,id)=>{if(!ids.has(id))row.remove();});
    if(model.tracks.length)list.querySelector('.settings-audio-empty')?.remove();
    else if(!list.querySelector('.settings-audio-empty'))list.innerHTML='<p class="settings-audio-empty">导入几首喜欢的音乐。</p>';
    model.tracks.forEach((track,index)=>{
      let row=rows.get(track.id);
      if(!row){const fragment=document.createElement('div');fragment.innerHTML=trackRow(track);row=fragment.firstElementChild;}
      if(list.children[index]!==row)list.insertBefore(row,list.children[index]||null);
      const confirmation=row.querySelector('.settings-track-confirm');
      if(model.pendingRemove===track.id&&!confirmation){const fragment=document.createElement('div');fragment.innerHTML=trackRow(track);row.append(fragment.querySelector('.settings-track-confirm'));}
      else if(model.pendingRemove!==track.id)confirmation?.remove();
      row.querySelectorAll('input,button').forEach(field=>{field.disabled=busy;});
    });
    syncTrackRows();
  }
  async function setTheme(theme){
    const current=active();if(!current||current.busy||!THEMES.includes(theme))return;const previous=model.theme;current.busy=true;applyTheme(theme);refresh();
    try{const result=await host.api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({theme})});applyTheme(result.theme);}catch(error){applyTheme(previous);throw error;}finally{current.busy=false;if(active()===current)refresh();}
  }
  function syncTrackRows(){
    const current=window.ShiguangMusic.current();document.querySelectorAll('.settings-track[data-track-id]').forEach(row=>{const track=model.tracks.find(track=>track.id===row.dataset.trackId),button=row.querySelector('.track-preview');if(!track||!button)return;const playing=current.playing&&current.id===track.id;if(button.dataset.playing!==String(playing)){button.innerHTML=playIcon(playing);button.dataset.playing=String(playing);}button.setAttribute('aria-pressed',String(playing));button.setAttribute('aria-label',`${playing?'停止试听':'试听'}：${track.title}`);});
  }
  async function performTrackUpdate(id,change){
    const track=model.tracks.find(track=>track.id===id);if(!track)return;const result=await host.api(`/api/music/${encodeURIComponent(id)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(change)});const updated=result.track||result;Object.assign(track,updated);window.ShiguangMusic.setLibrary(model.tracks);
    const row=[...document.querySelectorAll('.settings-track[data-track-id]')].find(row=>row.dataset.trackId===id);if(row){row.classList.toggle('is-disabled',track.enabled===false);const checkbox=row.querySelector('[data-settings-enabled]');if(checkbox){checkbox.checked=track.enabled!==false;checkbox.setAttribute('aria-label','加入随机播放：'+track.title);}const input=row.querySelector('[data-settings-title]');if(input){input.value=track.title;input.setAttribute('aria-label','曲名：'+track.title);}row.querySelector('.track-remove')?.setAttribute('aria-label','移除：'+track.title);syncTrackRows();}
  }
  function updateTrack(id,change){const pending=trackQueue.catch(()=>{}).then(()=>performTrackUpdate(id,change));trackQueue=pending;return pending;}
  async function removeTrack(id){
    const current=active();if(!current||current.busy)return;current.busy=true;refresh();try{await trackQueue.catch(()=>{});await host.api(`/api/music/${encodeURIComponent(id)}/remove`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});model.tracks=await window.ShiguangMusic.load(true);model.pendingRemove=null;}finally{current.busy=false;if(active()===current)refresh();}
  }
  async function importFiles(files){
    const current=active();if(!current||current.busy||!files.length)return;model.importing=true;current.busy=true;let added=0,duplicates=0;const errors=[];
    window.ShiguangOnlineMusic?.onClose();
    try{for(let index=0;index<files.length;index++){const file=files[index];model.progress=`正在导入 ${index+1} / ${files.length} · ${file.name}`;refresh();try{if(!/\.(mp3|wav|m4a|aac|ogg|flac)$/i.test(file.name))throw new Error('支持 MP3、WAV、M4A、AAC、OGG、FLAC。');if(file.size>100*1024*1024)throw new Error('单首音频请不超过 100 MB。');const params=new URLSearchParams({filename:file.name});const result=await host.api('/api/music/import?'+params,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});if(result.duplicate)duplicates++;else added++;}catch(error){errors.push(`${file.name}：${error.message}`);}}
      model.tracks=await window.ShiguangMusic.load(true);model.progress='';host.toast(`已导入 ${added} 首音乐${duplicates?`，${duplicates} 首已在曲库中`:''}${errors.length?`，${errors.length} 首未能导入`:''}`);if(errors.length)host.toast(errors[0],'error');
    }finally{model.importing=false;current.busy=false;model.progress='';const picker=document.getElementById('settings-audio-files');if(picker)picker.value='';if(active()===current)refresh();}
  }
  async function handleAction(action,target){
    if(action.startsWith('settings-opening-'))return window.ShiguangOpeningManager?.handleAction(action,target)??true;
    if(action.startsWith('settings-online-'))return window.ShiguangOnlineMusic?.handleAction(action,target)??true;
    if(action==='settings-open'){await open();return true;}if(!action.startsWith('settings-'))return false;const current=active();if(!current||current.busy)return true;
    if(action==='settings-tab'){const tab=['audio','backup','opening','data'].includes(target.dataset.tab)?target.dataset.tab:'appearance';if(current.tab===tab)return true;window.ShiguangOnlineMusic?.onClose();window.ShiguangOpeningManager?.onClose();current.tab=tab;model.pendingRemove=null;refresh();if(tab==='opening')void window.ShiguangOpeningManager?.load();}
    if(action==='settings-initialize')await initializeAlbum();
    if(action==='settings-theme')await setTheme(target.dataset.theme);
    if(action==='settings-preview'){window.ShiguangOnlineMusic?.stop();window.ShiguangMusic.previewTrack(target.dataset.id);}
    if(action==='settings-import')document.getElementById('settings-audio-files')?.click();
    if(action==='settings-remove'){model.pendingRemove=target.dataset.id;refresh();}
    if(action==='settings-remove-cancel'){model.pendingRemove=null;refresh();}
    if(action==='settings-remove-confirm'&&model.pendingRemove===target.dataset.id)await removeTrack(target.dataset.id);
    if(action==='settings-retry'){current.error='';current.loading=true;refresh();try{await initialize(true);}catch(error){current.error=error.message;}finally{current.loading=false;if(active()===current)refresh();}}
    return true;
  }
  document.addEventListener('input',event=>{if(event.target.id==='initialize-confirmation'){const button=document.querySelector('[data-action="settings-initialize"]');if(button)button.disabled=Boolean(active()?.busy)||event.target.value!=='清空相册';}if(event.target.id==='settings-volume'){model.music_volume=Number(event.target.value)/100;window.ShiguangMusic.setVolume(model.music_volume);window.ShiguangOnlineMusic?.volumeChanged(model.music_volume);const output=document.getElementById('settings-volume-value');if(output)output.value=`${event.target.value}%`;}});
  document.addEventListener('click',event=>{
    const download=event.target.closest?.('.settings-backup-download');
    if(download&&(active()?.busy||model.importing||download.getAttribute('aria-disabled')==='true')){event.preventDefault();event.stopImmediatePropagation();}
  },true);
  document.addEventListener('change',event=>{
    const field=event.target;if(!active())return;
    if(field.id==='settings-audio-files')importFiles([...field.files]).catch(error=>host.toast(error.message,'error'));
    if(field.id==='settings-volume'){const value=Number(field.value)/100;volumeQueue=volumeQueue.catch(()=>{}).then(()=>host.api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({music_volume:value})})).catch(error=>host.toast(error.message,'error'));}
    if(field.dataset.settingsEnabled){const id=field.dataset.settingsEnabled;updateTrack(id,{enabled:field.checked}).catch(error=>{field.checked=model.tracks.find(track=>track.id===id)?.enabled!==false;host.toast(error.message,'error');});}
    if(field.dataset.settingsTitle){const id=field.dataset.settingsTitle,title=field.value.trim();if(!title){field.value=model.tracks.find(track=>track.id===id)?.title||'';return;}updateTrack(id,{title}).catch(error=>{field.value=model.tracks.find(track=>track.id===id)?.title||'';host.toast(error.message,'error');});}
  });
  document.addEventListener('shiguangmusicchange',syncTrackRows);
  window.ShiguangSettings={configure,initialize,renderModal,handleAction,isImporting:()=>model.importing||Boolean(active()?.initializing),onClose:()=>{window.ShiguangOnlineMusic?.onClose();model.pendingRemove=null;window.ShiguangOpeningManager?.onClose();if(window.ShiguangMusic.current().preview)window.ShiguangMusic.stop();}};
})();
