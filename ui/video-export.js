/* Collection slideshows: local previews, soundtrack listening, and export jobs. */
(() => {
  'use strict';
  const TYPE='video-export',POLL_MS=1200;
  const DEFAULTS={resolution:'1080p',seconds_per_photo:5,order:'collection',music_id:null,volume:.5};
  const SECONDS=[3,4,5,6,8,10],WORKING=new Set(['preparing','encoding']);
  const STATUSES=new Set(['preparing','encoding','completed','cancelled','failed']);
  const model={drafts:new Map(),jobs:new Map()};
  let host=null,session=0,previewSequence=0,pollTimer=null,pollController=null,audio=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icon=name=>host?.icon(name)||'';
  const active=()=>{const current=host?.getState().modal;return current?.type===TYPE&&!current.closed?current:null;};
  const isCurrent=current=>active()===current&&current.session===session;
  const working=current=>WORKING.has(current.job?.status);
  const complete=current=>current.job?.status==='completed';
  const duration=current=>Math.max(0,current.photoCount*current.options.seconds_per_photo-Math.max(0,current.photoCount-1)*.8);
  const time=value=>{const seconds=Math.max(0,Math.round(Number(value)||0));return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;};
  const jobPath=id=>`/api/video-jobs/${encodeURIComponent(id)}`;
  const previewUrl=current=>`/api/collections/${encodeURIComponent(current.collectionId)}/video-preview?order=${current.options.order}`;
  const resultUrl=value=>typeof value==='string'&&/^\/api\/video-jobs\/[A-Za-z0-9_-]+\/file(?:\?download=1)?$/.test(value)?value:'';
  function configure(context){host=context;}
  function redraw(current){if(isCurrent(current))host.renderModal();}
  function refreshOptions(current){
    if(!isCurrent(current))return;
    const fields=document.getElementById('video-export-fields');
    if(fields)fields.innerHTML=formMarkup(current);
    syncCreateButton(current);
  }
  function remember(current){model.drafts.set(current.collectionId,{...current.options});}
  function limitMessage(current){
    if(!current.photoCount)return '请先为作品集添加照片。';
    if(current.photoCount>300)return '一次最多导出 300 张照片，请减少作品集中的照片。';
    if(duration(current)>1800)return '当前视频超过 30 分钟，请缩短每张照片的停留时间。';
    return '';
  }
  function canCreate(current){return !current.busy&&!current.tracksLoading&&!working(current)&&!complete(current)&&current.previewStatus==='ready'&&!limitMessage(current);}
  function stopResultVideo(clear=false){
    const player=document.getElementById('video-export-result');
    if(player){player.pause();if(clear){player.removeAttribute('src');player.load();}}
  }
  function updateListenButton(current){
    if(!isCurrent(current))return;
    const button=document.getElementById('video-listen');
    if(button){button.disabled=!current.options.music_id||current.tracksLoading||current.busy||working(current)||complete(current);button.innerHTML=`${icon(current.audioPlaying?'close':'music')} ${current.audioPlaying?'停止试听':'试听配乐'}`;button.setAttribute('aria-pressed',String(Boolean(current.audioPlaying)));}
  }
  function stopAudio(){
    const player=audio;audio=null;
    if(player){player.pause();player.removeAttribute('src');player.load();player.remove();}
    const current=active();if(current){current.audioPlaying=false;updateListenButton(current);}
  }
  function clearPolling(){
    if(pollTimer!==null){clearTimeout(pollTimer);pollTimer=null;}
    if(pollController){pollController.abort();pollController=null;}
  }
  function onClose(){
    const current=active();if(current)remember(current);
    stopAudio();stopResultVideo(true);clearPolling();
    if(current)current.closed=true;
    session++;
  }
  function normalizedJob(value){
    if(!value||typeof value.id!=='string'||!value.id||!STATUSES.has(value.status))throw new Error('暂时无法读取视频导出状态，请重试。');
    return {...value,progress:Math.max(0,Math.min(100,Number(value.progress)||0)),video_url:resultUrl(value.video_url),download_url:resultUrl(value.download_url)};
  }
  function applyJob(current,value){
    const job=normalizedJob(value);current.job=job;current.pollError='';
    model.jobs.set(current.collectionId,job);
    return job;
  }
  function schedulePoll(current){
    if(pollTimer!==null)clearTimeout(pollTimer);
    pollTimer=null;
    if(isCurrent(current)&&working(current)&&!current.pollError)pollTimer=setTimeout(()=>{pollTimer=null;void queryJob(current);},POLL_MS);
  }
  async function queryJob(current){
    if(!isCurrent(current)||!current.job||pollController)return;
    const controller=new AbortController();pollController=controller;
    try{
      const result=await host.api(jobPath(current.job.id),{signal:controller.signal});
      if(!isCurrent(current)||controller.signal.aborted)return;
      const previous=current.job.status;applyJob(current,result.job);
      if(WORKING.has(previous)&&working(current)){
        const progress=document.getElementById('video-export-job-status');if(progress)progress.innerHTML=jobMarkup(current);
      }else redraw(current);
      schedulePoll(current);
    }catch(error){
      if(isCurrent(current)&&!controller.signal.aborted){current.pollError=error.message||'暂时无法读取进度，请重新查询。';redraw(current);}
    }finally{if(pollController===controller)pollController=null;}
  }
  async function loadTracks(current){
    current.tracksLoading=true;current.tracksError='';
    refreshOptions(current);
    try{
      const result=await host.api('/api/music');if(!isCurrent(current))return;
      current.tracks=(Array.isArray(result.tracks)?result.tracks:[]).filter(track=>track.origin==='imported'&&/^[a-f0-9]{32}$/.test(String(track.id))&&/^\/media\/music\/[a-f0-9]{32}\.(?:mp3|wav|m4a|aac|ogg|flac)$/i.test(String(track.url)));
      if(current.options.music_id&&!current.tracks.some(track=>track.id===current.options.music_id)&&!working(current)&&!complete(current)){current.options.music_id=null;remember(current);}
    }catch(error){if(isCurrent(current))current.tracksError=error.message||'曲库暂时无法打开，可先选择无配乐。';}
    finally{if(isCurrent(current)){current.tracksLoading=false;refreshOptions(current);}}
  }
  async function open(collectionId){
    const state=host.getState(),collection=state.collections.find(item=>item.id===collectionId);
    if(!collection)throw new Error('这份作品集暂时无法打开，请刷新后重试。');
    onClose();
    const ids=new Set(collection.photo_ids||[]),photoCount=state.photos.filter(photo=>ids.has(photo.id)).length;
    const options={...DEFAULTS,...model.drafts.get(collectionId)};
    const current={collectionId,title:collection.title,photoCount,options,tracks:[],tracksLoading:true,tracksError:'',error:'',pollError:'',audioPlaying:false,job:model.jobs.get(collectionId)||null,previewToken:String(++previewSequence),previewStatus:'loading',session:++session};
    host.showModal(TYPE,current);
    const shown=active();
    await Promise.allSettled([loadTracks(shown),shown.job?queryJob(shown):Promise.resolve()]);
  }
  function previewError(){return `${icon('info')}<p>暂时无法生成预览。</p><button class="button secondary small" data-action="video-preview-retry">重新加载</button>`;}
  function stageMarkup(current){
    if(complete(current)&&current.job.video_url)return `<div class="video-export-stage is-result"><video id="video-export-result" src="${esc(current.job.video_url)}" controls playsinline preload="metadata" aria-label="作品集视频预览"></video></div>`;
    const ready=current.previewStatus==='ready',failed=current.previewStatus==='error';
    return `<div class="video-export-stage" id="video-export-stage" aria-busy="${!ready&&!failed}"><img id="video-export-still" src="${esc(previewUrl(current))}" data-video-preview="${esc(current.previewToken)}" alt="作品集视频的实际画面预览" ${ready?'':'hidden'}><div class="video-export-preview-status" role="status" ${ready?'hidden':''}>${failed?previewError():'<span class="spinner"></span><p>正在生成画面预览…</p>'}</div></div>`;
  }
  function formMarkup(current){
    const frozen=current.busy||working(current)||complete(current),disabled=frozen?'disabled':'',selected=current.options;
    const known=current.tracks.some(track=>track.id===selected.music_id);
    return `<form id="video-export-form" class="video-export-form"><div class="form-grid"><div class="field"><label for="video-resolution">画面清晰度</label><select id="video-resolution" data-video-option="resolution" ${disabled}><option value="1080p" ${selected.resolution==='1080p'?'selected':''}>1080p · 高清</option><option value="720p" ${selected.resolution==='720p'?'selected':''}>720p · 轻巧</option></select></div><div class="field"><label for="video-seconds">每张照片停留</label><select id="video-seconds" data-video-option="seconds_per_photo" ${disabled}>${SECONDS.map(seconds=>`<option value="${seconds}" ${selected.seconds_per_photo===seconds?'selected':''}>${seconds} 秒</option>`).join('')}</select></div><div class="field video-export-full"><label for="video-order">照片顺序</label><select id="video-order" data-video-option="order" ${disabled}><option value="collection" ${selected.order==='collection'?'selected':''}>按作品集编排</option><option value="oldest" ${selected.order==='oldest'?'selected':''}>按拍摄时间 · 由早及晚</option><option value="newest" ${selected.order==='newest'?'selected':''}>按拍摄时间 · 由近及远</option></select></div><div class="field video-export-full"><label for="video-music">视频配乐</label><div class="video-music-row"><select id="video-music" data-video-option="music_id" ${frozen||current.tracksLoading?'disabled':''}><option value="" ${!selected.music_id?'selected':''}>无配乐</option>${selected.music_id&&!known&&(working(current)||complete(current))?`<option value="${esc(selected.music_id)}" selected>导出时选择的配乐</option>`:''}${current.tracks.map(track=>`<option value="${esc(track.id)}" ${selected.music_id===track.id?'selected':''}>${esc(track.title)}</option>`).join('')}</select><button class="button secondary small" id="video-listen" data-action="video-listen" type="button" aria-pressed="${Boolean(current.audioPlaying)}" ${!selected.music_id||current.tracksLoading||frozen?'disabled':''}>${icon(current.audioPlaying?'close':'music')} ${current.audioPlaying?'停止试听':'试听配乐'}</button></div>${current.tracksLoading?'<p class="field-hint">正在读取导入的音乐…</p>':current.tracksError?`<p class="video-export-error">${esc(current.tracksError)}</p><button class="inline-link" type="button" data-action="video-tracks-retry" ${disabled}>重新读取曲库</button>`:!current.tracks.length?`<p class="field-hint">还没有导入音乐，可选择无配乐。</p><button class="inline-link" type="button" data-action="video-import-music" ${disabled}>去设置导入音乐 ${icon('right')}</button>`:''}</div><div class="field video-export-full"><label for="video-volume">配乐音量 <output id="video-volume-value">${Math.round(selected.volume*100)}%</output></label><input id="video-volume" data-video-option="volume" type="range" min="0" max="100" step="1" value="${Math.round(selected.volume*100)}" ${frozen||!selected.music_id?'disabled':''}></div></div></form>`;
  }
  function jobMarkup(current){
    const job=current.job;
    if(!job)return '';
    if(complete(current))return `<div class="video-export-job is-completed" role="status">${icon('check')}<div><strong>视频已完成</strong><p>完成后请保存视频。</p></div></div>`;
    if(working(current))return `<div class="video-export-job" role="status"><div class="video-job-heading"><span>${esc(job.message||'正在制作视频…')}</span><strong>${Math.floor(job.progress)}%</strong></div><progress class="video-export-progress" max="100" value="${job.progress}" aria-label="视频导出进度">${Math.floor(job.progress)}%</progress><p class="field-hint">可以关闭此弹窗继续浏览，稍后重新打开查看进度。</p></div>`;
    return `<div class="video-export-job ${job.status==='failed'?'is-error':''}" role="status"><p>${esc(job.status==='cancelled'?'已取消本次导出，可以调整设置后重新制作。':job.error||job.message||'视频未能完成，请重试。')}</p></div>`;
  }
  function footerMarkup(current){
    let controls='';
    if(working(current))controls=`<button class="button secondary" data-action="video-cancel" ${current.busy?'disabled':''}>取消导出</button>`;
    else if(complete(current))controls=`<button class="button secondary" data-action="video-new" ${current.busy?'disabled':''}>重新制作</button>${current.job.download_url?`<a class="button primary" href="${esc(current.job.download_url)}" download>${icon('download')} 下载 MP4</a>`:''}`;
    else controls=`<button class="button primary" id="video-create" data-action="video-create" ${canCreate(current)?'':'disabled'}>${icon('video')} ${current.busy?'正在提交…':current.job?'重试导出':'开始导出'}</button>`;
    return `<button class="button ghost" data-action="close-modal" ${current.busy?'disabled':''}>关闭</button><div>${controls}</div>`;
  }
  function renderModal(current){
    const body=`<div class="video-export-layout"><div class="video-export-preview">${stageMarkup(current)}<p class="video-export-error" id="video-export-playback-error" role="alert"></p><div class="video-export-summary"><strong>${esc(current.title)}</strong><span id="video-export-estimate">${current.photoCount} 张照片 · 预计 ${time(duration(current))}</span></div><div id="video-export-job-status">${jobMarkup(current)}</div>${current.pollError?`<div class="video-export-error" role="alert"><p>${esc(current.pollError)}</p><button class="inline-link" data-action="video-poll-retry">重新查询进度</button></div>`:''}</div><div class="video-export-options"><div id="video-export-fields">${formMarkup(current)}</div><p class="video-export-limit" id="video-export-limit" role="status">${esc(limitMessage(current))}</p>${current.error?`<p class="video-export-error" role="alert">${esc(current.error)}</p>`:''}</div></div>`;
    return host.modalFrame('导出视频','16:9 · 照片居中 · 柔雾质感背景 · 淡入淡出',body,footerMarkup(current),'video-export-modal');
  }
  function syncCreateButton(current){
    const button=document.getElementById('video-create');if(button)button.disabled=!canCreate(current);
  }
  function updatePreview(image,success){
    const current=active();if(!current||current.previewToken!==image.dataset.videoPreview||!image.isConnected||complete(current))return;
    const ready=success&&image.naturalWidth>0;current.previewStatus=ready?'ready':'error';image.hidden=!ready;
    document.getElementById('video-export-stage')?.setAttribute('aria-busy','false');
    const feedback=document.querySelector('.video-export-preview-status');if(feedback){feedback.hidden=ready;if(!ready)feedback.innerHTML=previewError();}
    syncCreateButton(current);
  }
  function captureOptions(current){
    document.querySelectorAll('#video-export-form [data-video-option]').forEach(field=>setOption(current,field.dataset.videoOption,field.value));
    remember(current);
  }
  function setOption(current,key,value){
    if(key==='resolution')current.options.resolution=value==='720p'?'720p':'1080p';
    if(key==='seconds_per_photo')current.options.seconds_per_photo=SECONDS.includes(Number(value))?Number(value):5;
    if(key==='order')current.options.order=['collection','oldest','newest'].includes(value)?value:'collection';
    if(key==='music_id')current.options.music_id=value||null;
    if(key==='volume')current.options.volume=Math.max(0,Math.min(1,(Number(value)||0)/100));
  }
  function optionsChanged(event){
    const current=active(),field=event.target,key=field.dataset?.videoOption;
    if(!current||!key||current.busy||working(current)||complete(current))return;
    const old=current.options[key];setOption(current,key,field.value);remember(current);
    if(key==='volume'){
      const label=document.getElementById('video-volume-value');if(label)label.textContent=`${Math.round(current.options.volume*100)}%`;
      if(audio)audio.volume=current.options.volume;
    }else if(key==='music_id'&&old!==current.options.music_id){
      stopAudio();const volume=document.getElementById('video-volume');if(volume)volume.disabled=!current.options.music_id;updateListenButton(current);
    }else if(key==='order'&&old!==current.options.order){
      stopAudio();current.previewToken=String(++previewSequence);current.previewStatus='loading';redraw(current);
    }
    const estimate=document.getElementById('video-export-estimate');if(estimate)estimate.textContent=`${current.photoCount} 张照片 · 预计 ${time(duration(current))}`;
    const warning=document.getElementById('video-export-limit');if(warning)warning.textContent=limitMessage(current);
    syncCreateButton(current);
  }
  async function listen(current){
    if(current.audioPlaying){stopAudio();return;}
    const track=current.tracks.find(item=>item.id===current.options.music_id);if(!track)return;
    window.ShiguangMusic?.stop();stopResultVideo();stopAudio();
    const player=document.createElement('audio');audio=player;player.hidden=true;player.preload='none';player.src=track.url;player.volume=current.options.volume;document.body.append(player);current.audioPlaying=true;updateListenButton(current);
    const fail=()=>{if(audio!==player||!isCurrent(current))return;stopAudio();host.toast('这首音乐暂时无法试听，可以换一首或选择无配乐。','error');};
    player.addEventListener('ended',()=>{if(audio===player)stopAudio();});player.addEventListener('error',fail);
    try{await player.play();if(audio!==player||!isCurrent(current))player.pause();}catch(_error){fail();}
  }
  async function createJob(current){
    captureOptions(current);if(!canCreate(current))return;
    stopAudio();stopResultVideo(true);clearPolling();current.busy=true;current.error='';redraw(current);
    try{
      const result=await host.api(`/api/collections/${encodeURIComponent(current.collectionId)}/video`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(current.options)});
      if(!isCurrent(current))return;applyJob(current,result.job);
    }catch(error){if(isCurrent(current))current.error=error.message||'暂时无法开始导出，请重试。';}
    finally{current.busy=false;if(isCurrent(current)){redraw(current);schedulePoll(current);}}
  }
  async function cancelJob(current){
    if(!current.job||!working(current))return;
    clearPolling();current.busy=true;current.error='';redraw(current);
    try{
      const result=await host.api(jobPath(current.job.id),{method:'DELETE'});
      if(!isCurrent(current))return;
      if(result.job)applyJob(current,result.job);
      else {const latest=await host.api(jobPath(current.job.id));if(isCurrent(current))applyJob(current,latest.job);}
    }catch(error){if(isCurrent(current))current.error=error.message||'暂时无法取消，请重试。';}
    finally{current.busy=false;if(isCurrent(current)){redraw(current);schedulePoll(current);}}
  }
  async function handleAction(action,element){
    if(!action.startsWith('video-'))return false;
    if(action==='video-open'){await open(element.dataset.id);return true;}
    const current=active();if(!current||current.busy)return true;
    if(action==='video-create')await createJob(current);
    else if(action==='video-cancel')await cancelJob(current);
    else if(action==='video-listen'&&!working(current)&&!complete(current))await listen(current);
    else if(action==='video-tracks-retry')await loadTracks(current);
    else if(action==='video-preview-retry'){current.previewToken=String(++previewSequence);current.previewStatus='loading';redraw(current);}
    else if(action==='video-poll-retry'){current.pollError='';await queryJob(current);}
    else if(action==='video-new'){
      stopAudio();stopResultVideo(true);clearPolling();current.job=null;model.jobs.delete(current.collectionId);current.error='';current.pollError='';
      if(current.options.music_id&&!current.tracks.some(track=>track.id===current.options.music_id))current.options.music_id=null;
      current.previewToken=String(++previewSequence);current.previewStatus='loading';remember(current);redraw(current);
    }else if(action==='video-import-music'&&!working(current)&&!complete(current)){
      remember(current);host.closeModal(true);
      await window.ShiguangSettings?.handleAction('settings-open',{dataset:{}});
      await window.ShiguangSettings?.handleAction('settings-tab',{dataset:{tab:'audio'}});
    }
    return true;
  }
  document.addEventListener('input',optionsChanged);
  document.addEventListener('change',optionsChanged);
  document.addEventListener('submit',event=>{
    if(event.target.id!=='video-export-form')return;
    event.preventDefault();const current=active();if(current&&!current.busy)void createJob(current);
  });
  document.addEventListener('load',event=>{if(event.target.id==='video-export-still')updatePreview(event.target,true);},true);
  document.addEventListener('error',event=>{
    if(event.target.id==='video-export-still')updatePreview(event.target,false);
    if(event.target.id==='video-export-result'&&event.target===document.getElementById('video-export-result')&&event.target.isConnected&&active()){active().error='视频暂时无法播放，可以下载 MP4 后查看。';const notice=document.getElementById('video-export-playback-error');if(notice)notice.textContent=active().error;}
  },true);
  document.addEventListener('play',event=>{if(event.target.id==='video-export-result'&&event.target===document.getElementById('video-export-result')&&event.target.isConnected&&active()){window.ShiguangMusic?.stop();stopAudio();}},true);
  window.ShiguangVideoExport={configure,renderModal,handleAction,onClose};
})();
