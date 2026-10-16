/* GD search stays online; imported audio joins the portable local library. */
(() => {
  'use strict';
  let host=null,request=null,player=null,generation=0;
  const state={open:false,query:'',source:'netease',searchedQuery:'',page:1,tracks:[],hasMore:false,searched:false,loading:false,error:'',playing:null,importing:null,imported:new Map()};
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const active=()=>host?.getState().modal?.type==='settings'&&host.getState().modal.tab==='audio';
  const redraw=()=>{if(active())host.redraw();};
  const imported=token=>state.imported.has(token)&&(!host.hasTrack||host.hasTrack(state.imported.get(token)));
  const post=(action,value,options={})=>host.api('/api/music/online/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value),...options});
  function configure(context){host=context;}
  function stopPreview(){
    if(player){player.pause();player.removeAttribute('src');player.load();player.remove();player=null;}
    state.playing=null;
  }
  function cancelRequest(){request?.abort();request=null;generation++;state.loading=false;}
  function onClose(){cancelRequest();stopPreview();state.open=false;}
  function stop(){cancelRequest();stopPreview();redraw();}
  function render(){
    if(!state.open)return '';
    const frozen=host.isBusy(),disabled=frozen||state.loading?'disabled':'';
    return `<section class="online-music-panel" aria-label="在线导入音乐"><div class="online-music-heading"><strong>在线选曲</strong><span>音乐来源：<a href="https://music.gdstudio.xyz/" target="_blank" rel="noopener noreferrer">GD音乐台</a></span></div><form id="online-music-search" class="online-music-search"><label class="visually-hidden" for="online-music-query">搜索歌名、歌手或专辑</label><input id="online-music-query" type="search" placeholder="歌名、歌手或专辑" maxlength="80" value="${esc(state.query)}" ${disabled}><label class="visually-hidden" for="online-music-source">音乐源</label><select id="online-music-source" ${disabled}>${[['netease','网易云'],['joox','JOOX'],['bilibili','哔哩哔哩']].map(([value,label])=>`<option value="${value}" ${state.source===value?'selected':''}>${label}</option>`).join('')}</select><button class="button secondary small" type="submit" ${disabled}>${state.loading?'搜索中…':'搜索'}</button></form>${state.error?`<p class="online-music-error" role="alert">${esc(state.error)}</p>`:''}<div class="online-music-results" aria-live="polite" aria-busy="${state.loading}">${state.tracks.map(track=>`<article class="online-music-track"><div class="online-music-copy"><strong>${esc(track.title)}</strong><span>${esc([track.artist,track.album].filter(Boolean).join(' · '))}</span></div><div class="online-music-actions"><button class="button ghost small" data-action="settings-online-preview" data-token="${esc(track.token)}" aria-pressed="${state.playing===track.token}" ${frozen?'disabled':''}>${state.playing===track.token?'停止':'试听'}</button><button class="button secondary small" data-action="settings-online-import" data-token="${esc(track.token)}" ${frozen||imported(track.token)?'disabled':''}>${state.importing===track.token?'导入中…':imported(track.token)?'已导入':'导入'}</button></div></article>`).join('')}${state.searched&&!state.loading&&!state.tracks.length&&!state.error?'<p class="online-music-empty">没有找到曲目，试试其他关键词或音乐源。</p>':''}</div>${state.searched&&(state.page>1||state.hasMore)?`<div class="online-music-pages"><button class="inline-link" data-action="settings-online-previous" ${disabled||state.page<=1?'disabled':''}>上一页</button><span>第 ${state.page} 页</span><button class="inline-link" data-action="settings-online-next" ${disabled||!state.hasMore?'disabled':''}>下一页</button></div>`:''}</section>`;
  }
  async function search(page=1){
    if(!active()||host.isBusy()||state.loading)return;
    const query=document.getElementById('online-music-query')?.value.trim()||state.query.trim();
    if(!query){state.error='先输入想找的歌名、歌手或专辑。';redraw();return;}
    if(query!==state.searchedQuery)page=1;
    stopPreview();state.query=query;state.source=document.getElementById('online-music-source')?.value||state.source;
    state.loading=true;state.error='';const controller=new AbortController();request=controller;const version=++generation;redraw();
    try{
      const result=await post('search',{query,source:state.source,page},{signal:controller.signal});
      if(version!==generation||!active())return;
      state.tracks=Array.isArray(result.tracks)?result.tracks:[];state.page=page;state.hasMore=Boolean(result.has_more);state.searched=true;state.searchedQuery=query;
    }catch(error){if(version===generation&&!controller.signal.aborted)state.error=error.message||'暂时无法搜索，请稍后重试。';}
    finally{if(version===generation){state.loading=false;request=null;redraw();}}
  }
  async function preview(token){
    if(state.playing===token){cancelRequest();stopPreview();redraw();return;}
    cancelRequest();stopPreview();window.ShiguangMusic?.stop();state.error='';state.playing=token;const version=++generation;redraw();
    try{
      const result=await post('preview',{token});if(version!==generation||!active())return;
      const audio=document.createElement('audio');player=audio;audio.hidden=true;audio.preload='none';audio.src=result.url;audio.volume=host.volume();document.body.append(audio);
      const fail=()=>{if(player!==audio)return;stopPreview();state.error='这首音乐暂时无法试听，可更换音乐源或选择其他曲目。';redraw();};
      audio.addEventListener('error',fail);audio.addEventListener('ended',()=>{if(player===audio){stopPreview();redraw();}});
      try{await audio.play();}catch(_error){fail();}
    }catch(error){if(version===generation){stopPreview();state.error=error.message;redraw();}}
  }
  async function importTrack(token){
    const track=state.tracks.find(item=>item.token===token);if(!track)return;
    cancelRequest();stopPreview();state.importing=token;state.error='';host.setImporting(true,`正在保存《${track.title}》到本地曲库…`);redraw();
    try{
      const result=await post('import',{token});state.imported.set(token,result.track.id);
      try{await host.imported(result);}catch(_error){host.toast('音乐已保存，请重新打开设置刷新曲库。');return;}
      host.toast(result.restored?'已恢复到本地曲库。':result.duplicate?'这首音乐已在曲库中。':'已导入到本地曲库。');
    }catch(error){state.error=error.message||'导入未完成，请稍后重试。';}
    finally{state.importing=null;host.setImporting(false,'');redraw();}
  }
  async function handleAction(action,target){
    if(!active()||host.isBusy())return true;
    if(action==='settings-online-toggle'){cancelRequest();stopPreview();state.open=!state.open;state.error='';redraw();if(state.open)document.getElementById('online-music-query')?.focus();}
    else if(action==='settings-online-preview')await preview(target.dataset.token);
    else if(action==='settings-online-import')await importTrack(target.dataset.token);
    else if(action==='settings-online-next'&&!state.loading&&state.hasMore)await search(state.page+1);
    else if(action==='settings-online-previous'&&!state.loading&&state.page>1)await search(state.page-1);
    return true;
  }
  document.addEventListener('submit',event=>{if(event.target.id==='online-music-search'){event.preventDefault();void search();}});
  document.addEventListener('input',event=>{if(event.target.id==='online-music-query')state.query=event.target.value;});
  document.addEventListener('change',event=>{if(event.target.id==='online-music-source'){cancelRequest();stopPreview();state.source=event.target.value;state.tracks=[];state.searched=false;state.page=1;state.error='';redraw();}});
  window.ShiguangOnlineMusic={configure,render,handleAction,onClose,stop,volumeChanged:value=>{if(player)player.volume=value;}};
})();
