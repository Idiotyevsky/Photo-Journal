/* One local player shared by background listening and the settings library. */
(() => {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state={tracks:[],ready:false,loaded:false,enabled:false,current:null,generation:0,volume:.22,preview:false};
  let audio=null,request=null,reportError=()=>{};
  const note='<svg class="icon music-note" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V5l11-2v13M9 8l11-2"/><ellipse cx="6" cy="18" rx="3" ry="2.5"/><ellipse cx="17" cy="16" rx="3" ry="2.5"/></svg>';
  const current=()=>({id:state.current?.id||null,title:state.current?.title||'',playing:state.enabled,preview:state.preview});
  function attributes(){return {title:!state.ready?'音乐准备中':state.enabled?`静音 · ${state.current?.title||'背景音乐'}`:'随机播放背景音乐',classes:`icon-button music-toggle ${state.enabled?'is-playing':'is-muted'}`};}
  function renderButton(){const a=attributes();return `<button class="${a.classes}" data-action="music-toggle" aria-label="${esc(a.title)}" title="${esc(a.title)}" aria-pressed="${state.enabled}" ${!state.ready?'disabled aria-busy="true"':''}>${note}<span class="music-status-dot" aria-hidden="true"></span></button>`;}
  function updateButton(){
    const a=attributes();document.querySelectorAll('[data-action="music-toggle"]').forEach(button=>{button.className=a.classes;button.title=a.title;button.setAttribute('aria-label',a.title);button.setAttribute('aria-pressed',String(state.enabled));button.disabled=!state.ready;if(state.ready)button.removeAttribute('aria-busy');else button.setAttribute('aria-busy','true');});
    document.dispatchEvent(new CustomEvent('shiguangmusicchange',{detail:current()}));
  }
  function stop(){state.generation++;state.enabled=false;state.preview=false;if(audio){audio.muted=true;audio.pause();}updateButton();}
  function fail(generation){if(generation!==state.generation||!state.enabled)return;stop();reportError('这段音频暂时无法播放，可以在设置中试听其他曲目。');}
  function getAudio(){
    if(audio)return audio;audio=document.createElement('audio');audio.id='background-music';audio.preload='none';audio.hidden=true;audio.muted=true;audio.volume=state.volume;audio.setAttribute('aria-hidden','true');document.body.append(audio);
    audio.addEventListener('ended',()=>{if(state.preview)stop();else if(state.enabled)startRandom();});audio.addEventListener('error',()=>{if(audio.error)fail(state.generation);});return audio;
  }
  function start(track,preview=false){
    const generation=++state.generation,player=getAudio();state.enabled=true;state.current=track;state.preview=preview;player.pause();player.src=track.url;player.dataset.trackId=track.id;player.dataset.trackTitle=track.title;player.volume=state.volume;player.muted=false;updateButton();
    let playback;try{playback=player.play();}catch(_){fail(generation);return;}
    Promise.resolve(playback).then(()=>{if(generation!==state.generation&&!state.enabled){player.muted=true;player.pause();}}).catch(()=>fail(generation));
  }
  function startRandom(){const playable=state.tracks.filter(track=>track.enabled!==false);if(!playable.length){reportError('请先在设置中导入或启用音乐。');return;}const choices=playable.length>1?playable.filter(track=>track.id!==state.current?.id):playable;start(choices[Math.floor(Math.random()*choices.length)]);}
  function handleToggle(){if(!state.ready)return;if(state.enabled)stop();else startRandom();}
  function previewTrack(id){const track=state.tracks.find(track=>track.id===id);if(!track)return;if(state.enabled&&state.current?.id===id){stop();return;}start(track,true);}
  function setVolume(value){if(!Number.isFinite(Number(value)))return;state.volume=Math.max(0,Math.min(1,Number(value)));if(audio)audio.volume=state.volume;}
  function setLibrary(tracks){
    state.tracks=(Array.isArray(tracks)?tracks:[]).filter(track=>typeof track.id==='string'&&typeof track.title==='string'&&typeof track.url==='string'&&/^\/(?:audio|media\/music)\/[a-zA-Z0-9_-]+\.(?:mp3|wav|m4a|aac|ogg|flac)$/i.test(track.url));
    const updated=state.tracks.find(track=>track.id===state.current?.id);
    if(state.current&&!updated)stop();else if(updated){state.current=updated;if(updated.enabled===false&&state.enabled&&!state.preview)stop();}
    state.ready=true;state.loaded=true;updateButton();return state.tracks;
  }
  async function load(force=false){
    if(request)return request;if(state.loaded&&!force)return state.tracks;
    request=fetch('/api/music',{cache:'no-store'}).then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data.error||'曲库暂时无法打开。');return setLibrary(data.tracks);}).finally(()=>{request=null;});return request;
  }
  function renderCredits(){
    const tracks=state.tracks.filter(track=>track.origin==='builtin');if(!tracks.length)return '';
    return `<div class="detail-section music-credits"><h3>内置音乐署名</h3><p>Scott Buckley · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a></p><ul>${tracks.map(track=>{const title=track.attribution?.match(/^'([^']+)' by /)?.[1]||track.title;return `<li>${/^https:\/\/www\.scottbuckley\.com\.au\/library\//.test(track.source||'')?`<a href="${esc(track.source)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`:esc(title)} — ${esc(track.artist||'Scott Buckley')}</li>`;}).join('')}</ul></div>`;
  }
  window.ShiguangMusic={renderButton,handleToggle,renderCredits,configure:options=>{reportError=options?.onError||(()=>{});},load,setLibrary,setVolume,previewTrack,stop,current};
  load().catch(()=>{state.ready=true;updateButton();});window.addEventListener('pagehide',stop);
})();
