/* A small photo context menu, with a native ID-only file location action. */
(() => {
  'use strict';
  let host=null,menu=null,returnFocus=null,busy=false;
  function configure(context){host=context;}
  function close(restore=true){
    menu?.remove();menu=null;
    const target=returnFocus;returnFocus=null;
    if(restore&&target?.isConnected)target.focus();
  }
  async function reveal(photoId){
    if(busy)return;
    busy=true;close();
    try{
      const api=window.pywebview?.api;
      if(typeof api?.reveal_photo!=='function')throw new Error('请在桌面相册中打开文件位置。');
      const result=await api.reveal_photo(photoId);
      if(result?.ok!==true)throw new Error(result?.error||'暂时无法定位这张照片，请稍后重试。');
    }catch(error){host?.toast(error.message||'文件管理器暂时无法打开。','error');}
    finally{busy=false;}
  }
  function open(image,x,y){
    const photo=host?.currentPhoto(),dialog=image.closest('.lightbox');
    if(!photo||!dialog||busy)return;
    close(false);returnFocus=image;
    menu=document.createElement('div');
    menu.className='photo-context-menu';menu.setAttribute('role','menu');menu.setAttribute('aria-label','照片操作');
    const button=document.createElement('button');
    button.type='button';button.setAttribute('role','menuitem');
    button.innerHTML=`${host.icon('folder')}<span>在文件管理器中显示</span>`;
    button.addEventListener('click',()=>void reveal(photo.id));
    menu.append(button);dialog.append(menu);
    const bounds=menu.getBoundingClientRect(),margin=8;
    menu.style.left=`${Math.max(margin,Math.min(x,window.innerWidth-bounds.width-margin))}px`;
    menu.style.top=`${Math.max(margin,Math.min(y,window.innerHeight-bounds.height-margin))}px`;
    button.focus();
  }
  document.addEventListener('contextmenu',event=>{
    if(event.target.id!=='lightbox-image'||!host?.currentPhoto())return;
    event.preventDefault();
    const bounds=event.target.getBoundingClientRect();
    open(event.target,event.clientX||bounds.left+bounds.width/2,event.clientY||bounds.top+bounds.height/2);
  });
  document.addEventListener('pointerdown',event=>{if(menu&&!menu.contains(event.target))close(false);},true);
  document.addEventListener('keydown',event=>{
    if(menu?.isConnected){
      if(['Escape','Tab','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
        event.preventDefault();event.stopImmediatePropagation();
        if(event.key==='Escape'||event.key==='Tab')close();
        else menu.querySelector('button')?.focus();
      }
    }else if(event.target.id==='lightbox-image'&&(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10'))){
      event.preventDefault();event.stopImmediatePropagation();
      const bounds=event.target.getBoundingClientRect();
      open(event.target,bounds.left+bounds.width/2,bounds.top+bounds.height/2);
    }
  },true);
  document.addEventListener('scroll',()=>close(false),true);
  window.addEventListener('resize',()=>close(false));
  window.addEventListener('blur',()=>close(false));
  window.ShiguangPhotoMenu={configure,close};
})();
