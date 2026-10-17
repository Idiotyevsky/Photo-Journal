/* Keep the current editor intact while confirming a destructive action. */
(() => {
  'use strict';
  let pending=null;
  function ask({title='确认删除？',message='',label='删除'}={}) {
    if(pending)return Promise.resolve(false);
    const previous=document.activeElement,root=document.createElement('div');
    root.className='confirm-overlay';
    root.innerHTML='<section class="confirm-card" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-message"><h2 id="confirm-title"></h2><p id="confirm-message"></p><div><button class="button ghost" data-confirm="cancel">取消</button><button class="button danger" data-confirm="accept"></button></div></section>';
    root.querySelector('h2').textContent=title;root.querySelector('p').textContent=message;root.querySelector('[data-confirm="accept"]').textContent=label;
    const underlying=[document.getElementById('app'),document.getElementById('modal-root')].filter(Boolean).map(node=>[node,node.inert]);
    underlying.forEach(([node])=>{node.inert=true;});
    return new Promise(resolve=>{
      function finish(result){document.removeEventListener('keydown',onKey,true);root.remove();underlying.forEach(([node,inert])=>{node.inert=inert;});pending=null;if(previous?.isConnected)previous.focus();resolve(result);}
      function onKey(event){
        if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();finish(false);}
        if(event.key==='Tab'){event.preventDefault();const buttons=[...root.querySelectorAll('button')];buttons[document.activeElement===buttons[0]?1:0].focus();}
      }
      pending=finish;root.addEventListener('click',event=>{const action=event.target.closest('[data-confirm]')?.dataset.confirm;if(action)finish(action==='accept');else if(event.target===root)finish(false);});
      document.addEventListener('keydown',onKey,true);document.body.append(root);root.querySelector('[data-confirm="cancel"]').focus();
    });
  }
  window.ShiguangConfirm={ask,isOpen:()=>Boolean(pending)};
})();
