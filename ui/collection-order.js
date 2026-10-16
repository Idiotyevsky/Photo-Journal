/* Portfolio order is a saved sequence, independent of dates and other albums. */
(() => {
  'use strict';
  const TYPE='collection-order',DRAG_TYPE='application/x-shiguang-photo';
  let host=null,drag=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icon=name=>host?.icon(name)||'';
  const active=()=>{const modal=host?.getState().modal;return modal?.type===TYPE?modal:null;};
  const changed=modal=>modal.photoIds.some((id,index)=>id!==modal.originalIds[index]);
  function configure(context){host=context;}
  function open(id){
    const collection=host.getState().collections.find(item=>item.id===id);
    if(!collection)throw new Error('这份作品集暂时无法打开，请刷新后重试。');
    if(collection.photo_ids.length>5000)throw new Error('一次最多编排 5000 张照片。');
    onClose();
    host.showModal(TYPE,{collectionId:id,title:collection.title,photoIds:[...collection.photo_ids],originalIds:[...collection.photo_ids],busy:false,error:''});
  }
  function renderModal(modal){
    const photos=new Map(host.getState().photos.map(photo=>[photo.id,photo]));
    const body=`<div class="order-summary"><span>${modal.photoIds.length} 张照片</span><span>拖动调整顺序</span></div>${modal.error?`<p class="order-error" role="alert">${esc(modal.error)}</p>`:''}<div class="order-grid" role="list" aria-label="作品集照片顺序">${modal.photoIds.map((id,index)=>{
      const photo=photos.get(id),src=String(photo?.thumbnail_url||'');
      const image=/^\/media\/previews\/[a-f0-9]{32}\.jpg$/.test(src)?`<img src="${esc(src)}" alt="第 ${index+1} 张照片" draggable="false" loading="lazy">`:icon('photo');
      return `<article class="order-photo" role="listitem" data-order-photo="${esc(id)}" draggable="${!modal.busy}" tabindex="0" aria-label="第 ${index+1} 张照片，可拖动调整顺序"><div class="order-image">${image}</div><div class="order-card-footer"><span class="order-number">${String(index+1).padStart(2,'0')}</span><span class="order-grip" aria-hidden="true">${icon('grid')}</span><div class="order-moves"><button class="icon-button" data-action="order-move" data-id="${esc(id)}" data-delta="-1" title="向前移动" aria-label="向前移动第 ${index+1} 张照片" ${modal.busy||index===0?'disabled':''}>${icon('left')}</button><button class="icon-button" data-action="order-move" data-id="${esc(id)}" data-delta="1" title="向后移动" aria-label="向后移动第 ${index+1} 张照片" ${modal.busy||index===modal.photoIds.length-1?'disabled':''}>${icon('right')}</button></div></div></article>`;
    }).join('')}</div>`;
    return host.modalFrame('编排照片',esc(modal.title),body,`<button class="button ghost" data-action="close-modal" ${modal.busy?'disabled':''}>取消</button><button class="button primary" data-action="order-save" ${modal.busy||!changed(modal)?'disabled':''}>${icon('check')} ${modal.busy?'正在保存…':'保存顺序'}</button>`,'collection-order-modal');
  }
  function clearHighlight(){document.querySelectorAll('.order-photo.is-drop-target,.order-photo.is-dragging').forEach(item=>{item.classList.remove('is-drop-target','is-dragging');});}
  function onClose(){drag=null;clearHighlight();}
  function move(id,index){
    const modal=active();if(!modal||modal.busy||!Number.isInteger(index)||index<0||index>=modal.photoIds.length)return;
    const previous=modal.photoIds.indexOf(id);if(previous<0||previous===index)return;
    modal.photoIds.splice(previous,1);modal.photoIds.splice(index,0,id);modal.error='';
    host.renderModal();
    setTimeout(()=>document.querySelector(`[data-order-photo="${id}"]`)?.focus(),0);
  }
  async function save(){
    const modal=active();if(!modal||modal.busy||!changed(modal))return;
    onClose();modal.busy=true;modal.error='';host.renderModal();
    try{
      const result=await host.api(`/api/collections/${encodeURIComponent(modal.collectionId)}/order`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({photo_ids:modal.photoIds,expected_photo_ids:modal.originalIds})});
      if(active()!==modal)return;
      host.storeCollection(result.collection);host.closeModal(true);host.redraw();host.toast('作品集顺序已保存。');
    }catch(error){if(active()===modal)modal.error=error.message||'顺序暂未保存，请重试。';}
    finally{modal.busy=false;if(active()===modal)host.renderModal();}
  }
  async function handleAction(action,button){
    if(action==='order-open'){open(button.dataset.id);return true;}
    const modal=active();if(!modal||modal.busy||button.disabled)return true;
    if(action==='order-save')await save();
    else if(action==='order-move'){const delta=Number(button.dataset.delta);if(delta===-1||delta===1)move(button.dataset.id,modal.photoIds.indexOf(button.dataset.id)+delta);}
    return true;
  }
  document.addEventListener('dragstart',event=>{
    const modal=active(),card=event.target.closest?.('[data-order-photo]');
    if(!modal||modal.busy||!card||!event.dataTransfer||!modal.photoIds.includes(card.dataset.orderPhoto))return;
    drag={modal,id:card.dataset.orderPhoto};event.dataTransfer.effectAllowed='move';event.dataTransfer.setData(DRAG_TYPE,drag.id);card.classList.add('is-dragging');
  });
  document.addEventListener('dragover',event=>{
    const modal=active(),card=event.target.closest?.('[data-order-photo]');
    if(!drag||drag.modal!==modal||modal.busy||!card||!modal.photoIds.includes(card.dataset.orderPhoto))return;
    event.preventDefault();event.stopPropagation();event.dataTransfer.dropEffect='move';
    document.querySelectorAll('.order-photo.is-drop-target').forEach(item=>item.classList.remove('is-drop-target'));
    if(card.dataset.orderPhoto!==drag.id)card.classList.add('is-drop-target');
  });
  document.addEventListener('drop',event=>{
    const modal=active(),card=event.target.closest?.('[data-order-photo]');
    if(!drag||drag.modal!==modal||modal.busy||!card||event.dataTransfer?.getData(DRAG_TYPE)!==drag.id)return;
    event.preventDefault();event.stopPropagation();
    const id=drag.id,index=modal.photoIds.indexOf(card.dataset.orderPhoto);onClose();move(id,index);
  });
  document.addEventListener('dragend',onClose);
  window.ShiguangCollectionOrder={configure,renderModal,handleAction,onClose};
})();
