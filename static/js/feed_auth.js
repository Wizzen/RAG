(() => {
  'use strict';
  window.FeedAuthForm=class {
    constructor(root) {
      this.root=root;this.type=root.querySelector('[data-auth-type]');
      this.list=root.querySelector('[data-auth-header-list]');this.summary=null;
      this.type.addEventListener('change',()=>this.render());
      root.querySelector('[data-auth-add]').addEventListener('click',()=>this.add());this.add();this.render();
    }
    add() {
      if(this.list.children.length>=10)return;
      const row=document.createElement('div');row.className='feed-auth-header';row.dataset.authHeaderRow='';
      const name=document.createElement('input'),value=document.createElement('input'),remove=document.createElement('button');
      name.dataset.authHeaderName='';name.placeholder='请求头名称，例如 Authorization';name.setAttribute('aria-label','请求头名称');name.autocomplete='off';
      value.dataset.authHeaderValue='';value.placeholder='请求头值，例如 ApiKey abc123';value.type='password';value.setAttribute('aria-label','请求头值');value.autocomplete='new-password';
      remove.type='button';remove.className='btn sm outline';remove.textContent='移除';remove.addEventListener('click',()=>row.remove());
      row.append(name,value,remove);this.list.append(row);
    }
    render() {
      for(const panel of this.root.querySelectorAll('[data-auth-panel]'))panel.hidden=!panel.dataset.authPanel.split(' ').includes(this.type.value);
      this.root.querySelector('[data-auth-saved]').hidden=this.type.value!=='saved';
    }
    read() {
      const input=selector=>this.root.querySelector(selector).value;
      switch(this.type.value){
        case 'bearer':return {type:'bearer',token:input('[data-auth-token]')};
        case 'api_key':return {type:'api_key',header_name:input('[data-auth-key-name]'),value:input('[data-auth-key-value]')};
        case 'basic':case 'login':return {type:this.type.value,username:input('[data-auth-username]'),password:input('[data-auth-password]')};
        case 'headers':return {type:'headers',headers:[...this.list.children].map(row=>({name:row.querySelector('[data-auth-header-name]').value,value:row.querySelector('[data-auth-header-value]').value}))};
        default:return {type:this.type.value};
      }
    }
    load(summary) {
      this.summary=summary;
      for(const input of this.root.querySelectorAll('input'))input.value=input.matches('[data-auth-key-name]')?'X-API-Key':'';
      this.list.replaceChildren();this.add();
      const saved=this.type.querySelector('[value=saved]');saved.hidden=!summary?.configured;
      this.type.value=summary?.configured?'saved':'none';
      this.root.querySelector('[data-auth-saved]').textContent=summary?.configured?`已保存 ${summary.label}${summary.headers.length?'（'+summary.headers.join('、')+'）':''}，凭据不回显。可以直接复用，或切换认证方式后填写新值。`:'';
      this.render();
    }
  };
})();
