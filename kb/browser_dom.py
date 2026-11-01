from __future__ import annotations

"""提供给采集模型的精简 DOM：真实元素引用、可重用选择器、表格和内容区域。"""

OBSERVE_SCRIPT = r'''(supplied,options) => {
  const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
  const visible=e=>e instanceof Element && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden' && e.getAttribute('aria-hidden')!=='true';
  const drop=new Set(['SCRIPT','STYLE','SVG','PATH','USE','NOSCRIPT','LINK','META','TEMPLATE']);
  const classes=e=>[...e.classList].filter(c=>!/^ng-|^active$|^hover$|^focus$|^selected$/.test(c)).slice(0,3);
  const css=e=>{
    if(e.id && !/^ng-|^mat-/.test(e.id))return '#'+CSS.escape(e.id);
    const cs=classes(e);if(cs.length)return cs.map(c=>'.'+CSS.escape(c)).join('');
    if(e.tagName==='A'&&e.getAttribute('href'))return 'a[href='+JSON.stringify(e.getAttribute('href'))+']';
    if(e.getAttribute('name'))return e.tagName.toLowerCase()+'[name='+JSON.stringify(e.getAttribute('name'))+']';
    return e.tagName.toLowerCase();
  };
  let root=supplied;
  if(options.auto){
    const candidates=[...document.querySelectorAll('main,[role="main"],router-outlet+*,[class*="finder-list"],[class*="detail-content"],[class*="page-content"],article')];
    root=candidates.find(e=>visible(e)&&norm(e.innerText).length>(e.matches('main,[role="main"],article')?20:60))||supplied;
  }
  if(options.expand&&root.childElementCount<2&&norm(root.innerText).length<180){
    let parent=root.parentElement;
    for(let i=0;i<2&&parent&&parent!==document.body;i++,parent=parent.parentElement){
      if(norm(parent.innerText).length>norm(root.innerText).length&&norm(parent.innerText).length<3500)root=parent;
    }
  }
  const all=[root,...root.querySelectorAll('*')].filter(e=>!drop.has(e.tagName)&&visible(e));
  const prior=window.__fosObserveCounter||0;let counter=prior;
  const elements=[],groups=new Map();
  for(const e of all){
    const text=norm(e.innerText||e.getAttribute('aria-label')||e.getAttribute('placeholder'));
    const tag=e.tagName.toLowerCase(),role=e.getAttribute('role')||'';
    const actionable=/^(a|button|input|select|textarea)$/.test(tag)||/button|link|checkbox|tab|option/.test(role)||e.hasAttribute('onclick')||e.hasAttribute('clicktag')||e.hasAttribute('tabindex');
    const pointer=getComputedStyle(e).cursor==='pointer' && text.length>1&&text.length<150&&e.childElementCount<3;
    const heading=/^h[1-6]$/.test(tag)||/heading/.test(role);
    if((actionable||pointer||heading)&&elements.length<200){
      const dataOnly=tag==='a'&&![...e.getClientRects()].some(rect=>rect.width>0&&rect.height>0);
      let ref=dataOnly?null:e.getAttribute('data-fos-ref');if(!dataOnly&&!ref){ref='e'+(++counter);e.setAttribute('data-fos-ref',ref);}
      const item={ref,tag,role:dataOnly?'data_link':role||(heading?'heading':actionable?tag:'clickable'),text:text.slice(0,160),selector:css(e),
        ...(dataOnly?{interaction:'只读取href；该链接没有可点击区域，请直接goto其实际地址。'}:{})};
      for(const name of ['href','name','type','placeholder','aria-label','data-id','data-testid'])if(e.hasAttribute(name))item[name]=e.getAttribute(name);
      if(tag==='select')item.options=[...e.options].slice(0,20).map(o=>({label:norm(o.text),value:o.value}));
      if((tag==='input'||tag==='select')&&!/password/i.test(e.type||'')&&!/pass|secret|token/i.test(e.name||''))item.value=e.value;
      elements.push(item);
    }
    if(text.length>3&&text.length<4000&&classes(e).some(c=>/card|item|row|detail|hour|time|table|title|content/.test(c))){
      const selector=css(e);if(!groups.has(selector))groups.set(selector,{selector,count:0,sample:text.slice(0,120),tag});groups.get(selector).count++;
    }
  }
  window.__fosObserveCounter=counter;
  let nodes=0;
  const escape=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
  const serialize=(node,depth=0)=>{
    if(nodes++>2500||depth>20)return '';
    if(node.nodeType===Node.TEXT_NODE)return escape(norm(node.textContent)).slice(0,240);
    if(node.nodeType!==Node.ELEMENT_NODE||drop.has(node.tagName)||!visible(node))return '';
    const tag=node.tagName.toLowerCase();
    if(['header','footer','nav'].includes(tag)&&root!==node)return '';
    if(tag==='img')return node.alt?'[图片:'+escape(node.alt).slice(0,100)+']':'';
    const attrs=[];
    for(const name of ['id','href','name','type','role','aria-label','datetime','data-id','data-testid','data-fos-ref']){
      const value=node.getAttribute(name);if(value&&!(name==='type'&&value==='hidden'))attrs.push(name+'="'+escape(value).slice(0,180)+'"');
    }
    const cs=classes(node);if(cs.length)attrs.push('class="'+escape(cs.join(' '))+'"');
    const content=[...node.childNodes].map(child=>serialize(child,depth+1)).filter(Boolean).join(' ');
    if(!content&&!/^(a|button|input|select|textarea|br)$/.test(tag))return '';
    if(attrs.length===0&&['div','span'].includes(tag))return content;
    return '<'+tag+(attrs.length?' '+attrs.join(' '):'')+'>'+content+'</'+tag+'>';
  };
  const html=serialize(root).slice(0,40000);
  const tables=[...root.querySelectorAll('table')].filter(visible).slice(0,5).map(t=>({selector:css(t),rows:[...t.rows].slice(0,8).map(r=>[...r.cells].map(c=>norm(c.innerText)))}));
  const structures=[...groups.values()].sort((a,b)=>(b.count>1)-(a.count>1)||b.count-a.count).slice(0,30);
  return {url:location.href,title:document.title,root_selector:css(root),text:norm(root.innerText).slice(0,20000),html,elements,structures,tables,
    observation_type:'semantic_dom',instructions:'元素编号可用于当前页操作，如 selector=@e12；采集脚本请用真实 selector，不能保存瞬时编号。'};
}'''
