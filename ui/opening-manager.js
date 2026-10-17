/* Local opening-line management; updates stay inside the existing settings panel. */
(() => {
  'use strict';
  let host=null,version=0;
  const model={lines:null,loading:false,error:'',draft:'',query:'',pending:null,saving:false};
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const active=()=>host?.getState().modal?.type==='settings'&&host.getState().modal.tab==='opening';
  const redraw=()=>{if(active())host.redraw();};
  function configure(context){host=context;}
  function rows(){
    const query=model.query.trim().toLocaleLowerCase();
    const lines=[...(model.lines||[])].sort((a,b)=>(a.origin==='custom'?0:1)-(b.origin==='custom'?0:1)).filter(line=>!query||line.text.toLocaleLowerCase().includes(query));
    return lines.map((line,index)=>`<article class="opening-line" data-line-id="${esc(line.id)}"><span class="opening-line-number">${String(index+1).padStart(2,'0')}</span><div class="opening-line-copy"><p>${esc(line.text)}</p>${line.origin==='custom'?'<span class="opening-line-origin">自己的句子</span>':''}</div><button class="icon-button opening-remove" data-action="settings-opening-remove" data-id="${esc(line.id)}" aria-label="删除这句开场白" title="删除开场白">${host.icon('trash')}</button>${model.pending===line.id?`<div class="opening-line-confirm"><span>删除这句开场白？</span><button class="button ghost small" data-action="settings-opening-keep">保留</button><button class="button danger small" data-action="settings-opening-delete" data-id="${esc(line.id)}">删除</button></div>`:''}</article>`).join('')||(model.loading?'<div class="loading-state"><span class="spinner"></span></div>':`<p class="opening-empty">${model.query?'没有找到这句开场白。':'还没有开场白，写下一句喜欢的话。'}</p>`);
  }
  function render(){return `<section class="opening-manager" id="opening-manager"><div class="opening-manager-heading"><h3>开场白</h3><span class="opening-count"></span></div><form id="opening-add-form"><label for="opening-draft">写下一句喜欢的话</label><textarea id="opening-draft" maxlength="120" rows="2" placeholder="让喜欢的句子，陪你翻开相册。">${esc(model.draft)}</textarea><div class="opening-add-footer"><output id="opening-length">${model.draft.length} / 120</output><button class="button secondary small" type="submit">${host.icon('plus')} 添加开场白</button></div></form><label class="visually-hidden" for="opening-search">查找开场白</label><input type="search" id="opening-search" placeholder="查找库中的开场白" value="${esc(model.query)}"><div class="opening-error" role="alert" hidden></div><button class="inline-link opening-retry" data-action="settings-opening-retry" hidden>重新加载</button><div class="opening-list" aria-live="polite">${rows()}</div></section>`;}
  function sync(){
    const root=document.getElementById('opening-manager');if(!root)return;
    const busy=host.isBusy()||model.saving,locked=busy||model.loading;
    root.querySelector('.opening-count').textContent=model.loading?'正在加载…':`${model.lines?.length||0} 句`;
    const draft=root.querySelector('#opening-draft'),search=root.querySelector('#opening-search');
    if(draft.value!==model.draft)draft.value=model.draft;if(search.value!==model.query)search.value=model.query;
    draft.disabled=locked;search.disabled=locked;root.querySelector('#opening-length').textContent=`${model.draft.length} / 120`;
    const submit=root.querySelector('[type="submit"]');submit.disabled=locked||model.lines===null||!model.draft.trim();submit.textContent=model.saving?'正在保存…':'添加开场白';
    const error=root.querySelector('.opening-error');error.hidden=!model.error;error.textContent=model.error;
    root.querySelector('.opening-retry').hidden=!model.error||model.saving;
    const list=root.querySelector('.opening-list'),signature=JSON.stringify([model.lines,model.query,model.pending,model.loading]);
    if(list.dataset.signature!==signature){const scroll=list.scrollTop;list.innerHTML=rows();list.dataset.signature=signature;list.scrollTop=scroll;}
    list.setAttribute('aria-busy',String(model.loading||model.saving));
    root.querySelectorAll('[data-action]').forEach(button=>{button.disabled=locked;});
  }
  async function load(){
    if(model.saving)return;const request=++version;model.loading=true;model.error='';redraw();
    try{const result=await host.api('/api/opening-lines');if(request!==version)return;model.lines=result.lines;window.ShiguangDaily?.setLines(result.lines);}
    catch(error){if(request===version)model.error=error.message||'暂时无法加载开场白。';}
    finally{if(request===version){model.loading=false;redraw();}}
  }
  async function mutate(path,options,adding=false){
    const current=host.getState().modal;if(!active()||host.isBusy()||model.loading||model.saving)return;
    model.saving=true;current.busy=true;model.error='';redraw();
    try{const result=await host.api(path,options);model.lines=result.lines;model.pending=null;version++;window.ShiguangDaily?.setLines(result.lines);if(adding){model.draft='';model.query='';}host.toast(adding?'开场白已添加。':'开场白已删除。');}
    catch(error){model.error=error.message||'暂时未能保存，请重试。';}
    finally{model.saving=false;current.busy=false;redraw();}
  }
  async function handleAction(action,target){
    if(!active()||host.isBusy()||model.loading||model.saving)return true;
    if(action==='settings-opening-remove'){model.pending=target.dataset.id;redraw();document.querySelector('.opening-line-confirm [data-action="settings-opening-keep"]')?.focus({preventScroll:true});}
    if(action==='settings-opening-keep'){model.pending=null;redraw();}
    if(action==='settings-opening-delete'&&model.pending===target.dataset.id)await mutate('/api/opening-lines/'+encodeURIComponent(target.dataset.id),{method:'DELETE'});
    if(action==='settings-opening-retry')await load();
    return true;
  }
  document.addEventListener('input',event=>{if(!active()||host.isBusy())return;if(event.target.id==='opening-draft')model.draft=event.target.value;if(event.target.id==='opening-search'){model.query=event.target.value;model.pending=null;}if(['opening-draft','opening-search'].includes(event.target.id))sync();});
  document.addEventListener('submit',event=>{if(event.target.id!=='opening-add-form')return;event.preventDefault();const text=model.draft.trim();if(text)void mutate('/api/opening-lines',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})},true);});
  window.ShiguangOpeningManager={configure,render,sync,load,handleAction,onClose:()=>{model.pending=null;}};
})();
