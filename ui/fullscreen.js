/* Native window fullscreen, with a standard browser preview fallback. */
(() => {
  'use strict';
  const options={onError:null,isModalOpen:null};
  const desktopFlag=new URLSearchParams(window.location?.search||'').get('desktop')==='1';
  const state={initialized:false,nativeExpected:desktopFlag||Boolean(window.pywebview?.platform),mode:'waiting',fullscreen:false,busy:false,available:false};
  let connection=null;
  const nativeApi=()=>window.pywebview?.api;
  const hasNativeApi=()=>typeof nativeApi()?.get_fullscreen==='function'&&typeof nativeApi()?.set_fullscreen==='function';
  const label=()=>state.fullscreen?'退出全屏（Esc）':'全屏（F11）';
  function icon(){
    const path=state.fullscreen?'M3 8h5V3m8 0v5h5M3 16h5v5m8 0v-5h5':'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5';
    return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
  }
  function renderButton(){
    return `<button class="icon-button fullscreen-button ${state.fullscreen?'is-active':''}" type="button" data-action="fullscreen-toggle" aria-label="${label()}" title="${label()}" aria-pressed="${state.fullscreen}" aria-busy="${state.busy||state.mode==='waiting'}" ${state.busy||!state.available?'disabled':''}>${icon()}</button>`;
  }
  function updateButtons(){
    document.querySelectorAll('[data-action="fullscreen-toggle"]').forEach(button=>{
      button.setAttribute('aria-label',label());button.setAttribute('title',label());
      button.setAttribute('aria-pressed',String(state.fullscreen));button.setAttribute('aria-busy',String(state.busy||state.mode==='waiting'));
      button.disabled=state.busy||!state.available;button.classList.toggle('is-active',state.fullscreen);
      button.innerHTML=icon();
    });
  }
  function reportError(){
    try{if(typeof options.onError==='function')options.onError('全屏切换没有完成，请稍后再试。');}catch(_error){}
  }
  function confirmed(result){
    if(!result||typeof result.fullscreen!=='boolean')throw new Error('Invalid native fullscreen response');
    return result.fullscreen;
  }
  function syncBrowser(){
    if(state.nativeExpected)return;
    state.mode='dom';state.fullscreen=Boolean(document.fullscreenElement);
    state.available=typeof document.exitFullscreen==='function'&&(state.fullscreen||(document.fullscreenEnabled!==false&&typeof document.documentElement?.requestFullscreen==='function'));
    updateButtons();
  }
  function connectNative(){
    if(connection)return connection;
    state.nativeExpected=true;state.mode='waiting';state.available=false;
    if(!hasNativeApi()){state.mode='unavailable';updateButtons();reportError();return Promise.resolve(false);}
    state.busy=true;updateButtons();
    connection=Promise.resolve().then(async()=>{
      try{
        state.fullscreen=confirmed(await nativeApi().get_fullscreen());
        state.mode='native';state.available=true;
        return true;
      }catch(_error){state.mode='unavailable';state.available=false;reportError();return false;}
      finally{state.busy=false;connection=null;updateButtons();}
    });
    return connection;
  }
  function onNativeReady(){
    state.nativeExpected=true;
    if(state.mode!=='native')void connectNative();
  }
  function onNativeChange(event){
    if(!state.nativeExpected||!hasNativeApi()||typeof event.detail?.fullscreen!=='boolean')return;
    state.fullscreen=event.detail.fullscreen;
    if(state.mode!=='waiting'){state.mode='native';state.available=true;}
    updateButtons();
  }
  function modalOpen(){
    if(typeof options.isModalOpen==='function')return Boolean(options.isModalOpen());
    return Boolean(document.querySelector('#modal-root [role="dialog"][aria-modal="true"], dialog[open]'));
  }
  function onKeyDown(event){
    if(event.defaultPrevented||event.isComposing||event.keyCode===229)return;
    if(event.key==='F11'){
      event.preventDefault();event.stopImmediatePropagation();
      if(!event.repeat)void handleToggle();
    }else if(event.key==='Escape'&&state.fullscreen&&!modalOpen()){
      event.preventDefault();event.stopImmediatePropagation();
      if(!event.repeat)void handleToggle();
    }
  }
  function initialize(){
    if(state.initialized)return connection||Promise.resolve(state.available);
    state.initialized=true;
    window.addEventListener('pywebviewready',onNativeReady);
    window.addEventListener('shiguangfullscreenchange',onNativeChange);
    document.addEventListener('fullscreenchange',syncBrowser);
    document.addEventListener('keydown',onKeyDown,true);
    state.nativeExpected=state.nativeExpected||Boolean(window.pywebview?.platform);
    if(state.nativeExpected){
      if(hasNativeApi())return connectNative();
      updateButtons();return Promise.resolve(false);
    }
    syncBrowser();return Promise.resolve(state.available);
  }
  async function handleToggle(){
    if(!state.initialized)initialize();
    if(state.busy||!state.available)return false;
    state.busy=true;updateButtons();
    try{
      if(state.mode==='native'){
        state.fullscreen=confirmed(await nativeApi().set_fullscreen(!state.fullscreen));
      }else if(state.mode==='dom'){
        if(document.fullscreenElement)await document.exitFullscreen();
        else await document.documentElement.requestFullscreen();
        syncBrowser();
      }else return false;
      return true;
    }catch(_error){
      if(state.mode==='native'){
        try{state.fullscreen=confirmed(await nativeApi().get_fullscreen());}catch(_readError){}
      }else syncBrowser();
      reportError();return false;
    }finally{state.busy=false;updateButtons();}
  }
  function configure(configuration={}){
    if('onError' in configuration)options.onError=configuration.onError;
    if('isModalOpen' in configuration)options.isModalOpen=configuration.isModalOpen;
  }
  window.ShiguangFullscreen={configure,initialize,renderButton,handleToggle};
})();
