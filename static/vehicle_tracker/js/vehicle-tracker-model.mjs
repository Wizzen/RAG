import * as THREE from 'three';
import {OrbitControls} from '/static/vehicle_tracker/vendor/three/OrbitControls.js';
import {PART_MAP,COACHES,CARRIERS,BRACKETS,within} from './vehicle-tracker-core.mjs';
import {PointerSelection} from './vehicle-tracker-pointer.mjs';

// Schematic geometry in metres. Coach layout follows SDLDWARMV0002 (pick-up coach) and the
// wheel carrier follows SDLDWARMV0822: gauge 900, rail Ø141.3, load wheels Ø230 at 390 pitch,
// guide / upstop wheels Ø150, carrier pivots 1200 apart. The track itself is not modelled.
// Origin: rail centre height; C1 (front coach) points to +X; left is −Z facing the direction of travel.
const PITCH=2.85, HALF_GAUGE=.45, CARRIER_X=.6, RAIL_R=.0707;
const LOAD_R=.115, SMALL_R=.075, LOAD_Y=RAIL_R+LOAD_R, UPSTOP_Y=-(RAIL_R+SMALL_R), GUIDE_Z=RAIL_R+SMALL_R;
const HUB_Y=1.55, TUB_Y=1.3, TUB_R=.56, TUB_L=2.36, STAND_X=1.24;
// Hidden by 透视车底 so that pins and spindles inside the assemblies can be picked.
const SHELL=new Set(['body','chassis','frame','towbar','dog']);
const AXIS={x:[0,0,Math.PI/2],y:null,z:[Math.PI/2,0,0]};
export const COLORS={pivotPin:0xe2a62b,spindle:0x7fd3e6};
const C={wood:[0x8b6236,0x86603a,0x8f6638,0x84603c,0x8a6440],iron:0x2b2f33,steel:0xb9c4cc,frame:0x8fa3b1,yoke:0xa7b6c1,urethane:0xe58a1f,hub:0xd2d8dc,bearing:0xe0c43a,chassis:0x3f4a54,seat:0x6b3d22};
const HIGHLIGHT=new THREE.Color(0x3ed3b4),HOVER=new THREE.Color(0x1f8f7d),MARK=new THREE.Color(0xe11d48);
const reducedMotion=()=>window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

export class VehicleModel {
  constructor(host,{select,hover,xrayChanged,focusRequest}) {
    this.host=host;this.onSelect=select;this.onHover=hover;this.onXrayChanged=xrayChanged;this.onFocusRequest=focusRequest;
    this.selected='TRAIN';this.hovered=null;this.marked=new Set();this.meshes=[];this.groups=new Map();this.mode='perspective';
    this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
    this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.renderer.setClearColor(0xf2f5f7);host.append(this.renderer.domElement);
    this.scene=new THREE.Scene();
    this.camera=new THREE.PerspectiveCamera(36,1,.02,400);
    this.controls=new OrbitControls(this.camera,this.renderer.domElement);
    this.controls.enableDamping=true;this.controls.dampingFactor=.1;this.controls.minDistance=.15;this.controls.maxDistance=120;
    this.controls.maxPolarAngle=Math.PI*.93;
    this.controls.mouseButtons={LEFT:THREE.MOUSE.ROTATE,MIDDLE:THREE.MOUSE.DOLLY,RIGHT:THREE.MOUSE.PAN};
    this.controls.addEventListener('change',()=>this.requestRender());
    this.controls.addEventListener('start',()=>{this.flight=null;});
    this.scene.add(new THREE.HemisphereLight(0xffffff,0x9aabb6,2.3));
    const key=new THREE.DirectionalLight(0xffffff,3);key.position.set(-10,26,18);this.scene.add(key);
    const fill=new THREE.DirectionalLight(0xd5e6f2,1.4);fill.position.set(14,-3,-12);this.scene.add(fill);
    this.grid=new THREE.GridHelper(40,80,0xc4d0d8,0xdce4e9);this.grid.position.y=-.3;this.scene.add(this.grid);
    this.build();
    this.outline=new THREE.Box3Helper(new THREE.Box3(),0x10a48e);this.outline.visible=false;this.scene.add(this.outline);
    this.ray=new THREE.Raycaster();this.pointer=new THREE.Vector2();
    const el=this.renderer.domElement,gesture=new PointerSelection();
    el.addEventListener('pointerdown',e=>gesture.start(e));
    el.addEventListener('pointermove',e=>{
      gesture.move(e);
      if(e.buttons){this.setHover(null);this.onHover(null);return;}
      const hit=this.hit(e);this.setHover(hit);this.onHover(hit?PART_MAP.get(hit):null,e);
      el.style.cursor=hit?'pointer':'grab';
    });
    el.addEventListener('pointerup',e=>{
      const click=gesture.end(e);
      this.lastWasClick=click;
      if(click && e.button===0) {const hit=this.hit(e);if(hit)this.onSelect(hit);}
    });
    el.addEventListener('pointercancel',()=>{gesture.cancel();this.lastWasClick=false;this.setHover(null);this.onHover(null);});
    el.addEventListener('pointerleave',()=>{this.setHover(null);this.onHover(null);});
    el.addEventListener('dblclick',e=>{
      if(!this.lastWasClick)return;
      const hit=this.hit(e);
      if(hit){this.onSelect(hit);this.focus(hit);}else this.onFocusRequest?.();
    });
    el.addEventListener('contextmenu',e=>e.preventDefault());
    el.addEventListener('webglcontextlost',e=>{e.preventDefault();this.onHover(null);});
    el.addEventListener('webglcontextrestored',()=>this.requestRender());
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(host);
    this.themeObserver=new MutationObserver(()=>this.theme());this.themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    this.theme();this.resize();this.orientation(this.mode,{animate:false});
  }
  build() {
    const geometries=new Map(),cached=(key,make)=>{if(!geometries.has(key))geometries.set(key,make());return geometries.get(key);};
    const box=(w,h,d)=>cached(`b${w},${h},${d}`,()=>new THREE.BoxGeometry(w,h,d));
    const cyl=(r,len,seg=24)=>cached(`c${r},${len},${seg}`,()=>new THREE.CylinderGeometry(r,r,len,seg));
    const tube=(r,len,seg=40)=>cached(`t${r},${len}`,()=>new THREE.CylinderGeometry(r,r,len,seg,1,true));
    // Urethane tyre on a hub: TorusGeometry lies in the XY plane, so its axis is Z.
    const tyre=(r,w)=>cached(`y${r},${w}`,()=>new THREE.TorusGeometry(r-w*.38,w*.42,14,40));
    const group=(id,parent,x=0,y=0,z=0)=>{const g=new THREE.Group();g.position.set(x,y,z);parent.add(g);this.groups.set(id,g);return g;};
    const mesh=(g,id,geometry,color,[x,y,z],{rot=null,metal=.35,rough=.5,pick=true,side=THREE.FrontSide}={})=>{
      const m=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color,metalness:metal,roughness:rough,side}));
      m.position.set(x,y,z);if(rot)m.rotation.set(...rot);g.add(m);
      m.userData.partId=id;m.userData.baseColor=m.material.color.clone();m.userData.pick=pick;this.meshes.push(m);
      return m;
    };
    const kit={group,mesh,box,cyl,tube,tyre};
    const root=group('TRAIN',this.scene);
    for(const coach of COACHES) {
      const c=`C${coach.no}`,car=group(c,root,(3-coach.no)*PITCH,0,0);
      this.coachBody(c,car,coach,kit);
      for(const wc of Object.keys(CARRIERS)) this.carrier(`${c}-${wc}`,car,wc,kit);
    }
  }
  coachBody(c,car,coach,{group,mesh,box,cyl,tube}) {
    const wood=C.wood[coach.no-1];
    // Seat Compartment Unit (SDLDWARMV0007): barrel-shaped tub hung from the pivot hubs, free to swing sideways.
    const scu=group(`${c}-SCU`,car),id=`${c}-SCU`;
    const open=.95,start=Math.PI/2+open,arc=Math.PI*2-open*2;
    const shell=new THREE.CylinderGeometry(TUB_R,TUB_R,TUB_L,48,1,true,start,arc);
    mesh(scu,id,shell,wood,[0,TUB_Y,0],{rot:AXIS.x,metal:.05,rough:.8,side:THREE.DoubleSide});
    const endCap=new THREE.CircleGeometry(TUB_R,48,start,arc);
    for(const x of [-TUB_L/2,TUB_L/2]) mesh(scu,id,endCap,0x6f4c2c,[x,TUB_Y,0],{rot:[0,Math.PI/2,0],metal:.05,rough:.8,side:THREE.DoubleSide});
    const band=new THREE.CylinderGeometry(TUB_R+.012,TUB_R+.012,.06,48,1,true,start,arc);
    for(const x of [-1.12,-.4,.4,1.12]) mesh(scu,id,band,C.iron,[x,TUB_Y,0],{rot:AXIS.x,metal:.6,rough:.45,side:THREE.DoubleSide});
    for(const x of [-.42,.5]) {
      mesh(scu,id,box(.42,.08,.92),C.seat,[x,1.02,0],{metal:.05,rough:.7});
      mesh(scu,id,box(.08,.5,.92),C.seat,[x-.23,1.25,0],{metal:.05,rough:.7});
      mesh(scu,id,box(.04,.04,.86),0xe8ecef,[x+.2,1.28,0],{metal:.4,rough:.35});
      for(const z of [-.43,.43]) mesh(scu,id,box(.04,.2,.04),C.iron,[x+.2,1.19,z],{metal:.5});
    }
    const canvas=document.createElement('canvas');canvas.width=128;canvas.height=64;
    const ctx=canvas.getContext('2d');ctx.fillStyle='#2f2216';ctx.fillRect(0,0,128,64);ctx.fillStyle='#f3d9a4';ctx.font='bold 40px sans-serif';ctx.textAlign='center';ctx.fillText(`C${coach.no}`,64,47);
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;
    for(const z of [-1,1]) {
      const plate=new THREE.Mesh(new THREE.PlaneGeometry(.42,.21),new THREE.MeshBasicMaterial({map:texture}));
      plate.position.set(0,TUB_Y-.05,z*(TUB_R+.02));plate.rotation.y=z>0?0:Math.PI;
      plate.userData={partId:id,baseColor:new THREE.Color(0xffffff),pick:true};scu.add(plate);this.meshes.push(plate);
    }
    for(const [end,x] of [['F',STAND_X],['R',-STAND_X]]) mesh(group(`${c}-SCU-SP${end}`,scu),`${c}-SCU-SP${end}`,cyl(.05,.22),COLORS.spindle,[x,HUB_Y,0],{rot:AXIS.x,metal:.85,rough:.25});
    // FRP lower section (SDLDWARMV0053) with the four decorative mine-cart wheels.
    const frp=group(`${c}-FRP`,car),fid=`${c}-FRP`;
    mesh(frp,fid,box(2.62,.26,1.18),wood,[0,.6,0],{metal:.05,rough:.8});
    for(const x of [-1.31,1.31]) mesh(frp,fid,box(.04,.27,1.2),C.iron,[x,.6,0],{metal:.55,rough:.45});
    for(const x of [-CARRIER_X,CARRIER_X]) for(const z of [-1,1]) {
      mesh(frp,fid,cyl(.25,.08,36),0x1f2225,[x,.5,z*.72],{rot:AXIS.z,metal:.3,rough:.7});
      mesh(frp,fid,cyl(.17,.09,30),0x7a5836,[x,.5,z*.72],{rot:AXIS.z,metal:.1,rough:.75});
      mesh(frp,fid,cyl(.055,.13,16),0x3a3f44,[x,.5,z*.72],{rot:AXIS.z,metal:.5,rough:.4});
    }
    // Chassis (SDLDWARMV0248): spine, end frames and the A-frame stands carrying the SCU pivot hubs.
    const ch=group(`${c}-CH`,car),cid=`${c}-CH`;
    mesh(ch,cid,box(2.5,.09,.3),C.chassis,[0,.4,0],{metal:.55});
    for(const x of [-STAND_X,STAND_X]) {
      mesh(ch,cid,box(.16,.08,.7),C.chassis,[x,.76,0],{metal:.55});
      for(const z of [-1,1]) mesh(ch,cid,box(.09,.86,.09),C.iron,[x,1.15,z*.14],{rot:[-z*.2,0,0],metal:.55});
      mesh(ch,cid,cyl(.15,.14,32),C.iron,[x+Math.sign(x)*.02,HUB_Y,0],{rot:AXIS.x,metal:.55,rough:.4});
    }
    for(const [axle,x,zh] of [['FA',CARRIER_X,'front'],['MA',-CARRIER_X,'main']]) {
      const g=group(`${c}-CH-${axle}`,ch),aid=`${c}-CH-${axle}`;
      mesh(g,aid,box(.22,.11,1.02),C.chassis,[x,.37,0],{metal:.55});
      for(const z of [-HALF_GAUGE,HALF_GAUGE]) mesh(g,aid,cyl(.17,.11,32),C.chassis,[x,.37,z],{metal:.55});
      if(zh==='main') mesh(group(`${c}-CH-MA-BS`,g),`${c}-CH-MA-BS`,cyl(.045,.34),COLORS.spindle,[x,.37,0],{rot:AXIS.x,metal:.85,rough:.25});
    }
    // Brake fin (SDLDWARMV0490): vertical blade on the centreline between the carriers.
    mesh(group(`${c}-CH-FIN`,ch),`${c}-CH-FIN`,box(.9,.26,.022),0xc6ced4,[-.12,.2,0],{metal:.75,rough:.25});
    // Towbar (SDLDWARMV0510) at the coach front: rear spindle on this coach, front spindle in the coach ahead.
    const tb=group(`${c}-TB`,car);
    mesh(tb,`${c}-TB`,box(.56,.07,.11),0x55626c,[1.38,.42,0],{metal:.6});
    mesh(tb,`${c}-TB`,box(.09,.12,.2),0x55626c,[1.62,.42,0],{metal:.6});
    for(const [end,x] of [['F',1.62],['R',1.12]]) mesh(group(`${c}-TB-SP${end}`,tb),`${c}-TB-SP${end}`,cyl(.035,.2),COLORS.spindle,[x,.42,0],{metal:.85,rough:.25});
    if(coach.dog) {
      // Chain / ARB dog (SDLDWARMV0826 / 0827) under the chassis, ahead of the brake fin.
      const dog=group(`${c}-DOG`,car);
      mesh(dog,`${c}-DOG`,box(.3,.09,.18),0x5d4b3c,[.55,.29,0],{metal:.5});
      mesh(dog,`${c}-DOG`,box(.08,.16,.06),0x5d4b3c,[.6,.17,0],{rot:[0,0,-.35],metal:.5});
      for(const [n,x] of [[1,.46],[2,.64]]) mesh(group(`${c}-DOG-AX${n}`,dog),`${c}-DOG-AX${n}`,cyl(.022,.24),COLORS.spindle,[x,.29,0],{rot:AXIS.z,metal:.85,rough:.25});
    }
  }
  carrier(w,car,wc,{group,mesh,box,cyl,tyre}) {
    // s = outward side; the C-frame wraps round the outside of the rail (SDLDWARMV0822 section A-A).
    const s=wc[0]==='L'?-1:1,end=wc[1]==='F'?1:-1;
    const g=group(w,car,end*CARRIER_X,0,s*HALF_GAUGE);
    mesh(group(`${w}-SB`,g),`${w}-SB`,cyl(.15,.03,40),C.bearing,[0,.3,0],{metal:.6,rough:.35});
    mesh(group(`${w}-SS`,g),`${w}-SS`,box(.2,.02,.2),C.steel,[0,.275,0],{metal:.7,rough:.3});
    mesh(group(`${w}-YK`,g),`${w}-YK`,box(.12,.1,.13),C.yoke,[0,.205,0],{metal:.6,rough:.35});
    mesh(group(`${w}-PP`,g),`${w}-PP`,cyl(.026,.33),COLORS.pivotPin,[0,.205,s*.08],{rot:AXIS.z,metal:.8,rough:.3});
    const cf=group(`${w}-CF`,g),cfId=`${w}-CF`;
    mesh(cf,cfId,box(.12,.07,.19),C.frame,[0,.205,s*.18],{metal:.55});
    mesh(cf,cfId,box(.13,.5,.045),C.frame,[0,-.01,s*.265],{metal:.55});
    mesh(cf,cfId,box(.36,.05,.045),C.frame,[0,0,s*.24],{metal:.55});
    mesh(cf,cfId,box(.12,.045,.3),C.frame,[0,-.235,s*.115],{metal:.55});
    mesh(cf,cfId,box(.12,.04,.1),C.frame,[0,-.24,0],{metal:.55});
    const stw=group(`${w}-STW`,g);
    mesh(stw,`${w}-STW`,box(.06,.05,.2),C.steel,[0,.24,-s*.16],{metal:.7,rough:.3});
    mesh(stw,`${w}-STW`,cyl(.034,.05,24),0xf0d21c,[0,.24,-s*.28],{rot:AXIS.z,metal:.3,rough:.5});
    mesh(group(`${w}-CR`,g),`${w}-CR`,cyl(.04,.035,24),C.iron,[0,.1,s*.305],{rot:AXIS.z,metal:.4,rough:.5});
    for(const [b,{group:kind}] of Object.entries(BRACKETS)) {
      const id=`${w}-${b}`,e=b[1]==='F'?1:-1,bracket=group(id,g);
      const wheel=group(`${id}-W`,bracket),spindle=group(`${id}-SP`,bracket),pin=group(`${id}-PP`,bracket);
      if(kind==='load') {
        const x=e*.195;
        mesh(wheel,`${id}-W`,tyre(LOAD_R,.085),C.urethane,[x,LOAD_Y,0],{metal:.05,rough:.65});
        mesh(wheel,`${id}-W`,cyl(.078,.07,30),C.hub,[x,LOAD_Y,0],{rot:AXIS.z,metal:.75,rough:.3});
        mesh(spindle,`${id}-SP`,cyl(.02,.16),COLORS.spindle,[x,LOAD_Y,0],{rot:AXIS.z,metal:.85,rough:.25});
        for(const z of [-.058,.058]) mesh(bracket,id,box(.15,.07,.016),C.frame,[e*.13,.205,z],{rot:[0,0,e*-.12],metal:.55});
        mesh(pin,`${id}-PP`,cyl(.02,.15),COLORS.pivotPin,[e*.075,.215,0],{rot:AXIS.z,metal:.8,rough:.3});
      } else if(kind==='guide') {
        const x=e*.173,z=s*GUIDE_Z;
        mesh(wheel,`${id}-W`,tyre(SMALL_R,.06),C.urethane,[x,0,z],{rot:[Math.PI/2,0,0],metal:.05,rough:.65});
        mesh(wheel,`${id}-W`,cyl(.05,.05,24),C.hub,[x,0,z],{metal:.75,rough:.3});
        mesh(spindle,`${id}-SP`,cyl(.017,.13),COLORS.spindle,[x,0,z],{metal:.85,rough:.25});
        for(const y of [-.045,.045]) mesh(bracket,id,box(.12,.014,.08),C.frame,[e*.12,y,s*.19],{metal:.55});
        mesh(pin,`${id}-PP`,cyl(.016,.13),COLORS.pivotPin,[e*.07,0,s*.225],{metal:.8,rough:.3});
      } else {
        const x=e*.185;
        mesh(wheel,`${id}-W`,tyre(SMALL_R,.06),C.urethane,[x,UPSTOP_Y,0],{metal:.05,rough:.65});
        mesh(wheel,`${id}-W`,cyl(.05,.05,24),C.hub,[x,UPSTOP_Y,0],{rot:AXIS.z,metal:.75,rough:.3});
        mesh(spindle,`${id}-SP`,cyl(.017,.13),COLORS.spindle,[x,UPSTOP_Y,0],{rot:AXIS.z,metal:.85,rough:.25});
        for(const z of [-.045,.045]) mesh(bracket,id,box(.14,.06,.014),C.frame,[e*.12,-.165,z],{rot:[0,0,e*.25],metal:.55});
        mesh(pin,`${id}-PP`,cyl(.016,.13),COLORS.pivotPin,[e*.075,-.195,0],{rot:AXIS.z,metal:.8,rough:.3});
      }
    }
  }
  hit(e) {
    const r=this.renderer.domElement.getBoundingClientRect();if(!r.width||!r.height)return null;
    this.pointer.set(((e.clientX-r.left)/r.width)*2-1,-((e.clientY-r.top)/r.height)*2+1);
    this.ray.setFromCamera(this.pointer,this.camera);
    return this.ray.intersectObjects(this.meshes.filter(m=>m.visible&&m.userData.pick&&!m.userData.ghost),false)[0]?.object.userData.partId||null;
  }
  theme() {
    const dark=document.documentElement.dataset.theme==='dark';this.renderer.setClearColor(dark?0x15191f:0xf5f5f7);
    this.grid.material.opacity=dark?.12:.25;this.grid.material.transparent=true;this.requestRender();
  }
  resize() {
    const w=this.host.clientWidth,h=this.host.clientHeight;if(w<1||h<1)return;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));this.renderer.setSize(w,h,false);this.camera.aspect=w/h;this.camera.updateProjectionMatrix();this.requestRender();
  }
  requestRender() {
    if(this.frame)return;
    this.frame=requestAnimationFrame(now=>{
      this.frame=null;
      let moving=false;
      if(this.flight) {
        const f=this.flight,t=Math.min(1,(now-f.start)/f.duration),k=1-Math.pow(1-t,3);
        this.controls.target.lerpVectors(f.fromTarget,f.toTarget,k);this.camera.position.lerpVectors(f.fromPosition,f.toPosition,k);
        if(t>=1)this.flight=null;moving=true;
      }
      const changed=this.controls.update();this.renderer.render(this.scene,this.camera);
      if(changed||moving)this.requestRender();
    });
  }
  paint() {
    const id=this.selected;
    for(const mesh of this.meshes) {
      const partId=mesh.userData.partId,active=id!=='TRAIN'&&within(partId,id),material=mesh.material;
      material.color.copy(mesh.userData.baseColor);
      const marked=this.marked.has(partId);
      if(marked)material.color.lerp(MARK,.85);
      else if(active)material.color.lerp(HIGHLIGHT,partId===id?.55:.16);
      else if(this.hovered&&partId===this.hovered)material.color.lerp(HOVER,.35);
      const shell=SHELL.has(PART_MAP.get(partId).type)&&partId!==id;
      const ghost=!marked&&!active&&(this.marked.size>0||this.isolated&&id!=='TRAIN');
      mesh.userData.ghost=ghost;
      mesh.visible=!(this.xray&&shell);
      material.transparent=ghost;material.opacity=ghost?(this.marked.size?.16:.1):1;material.depthWrite=!ghost;
    }
    this.requestRender();
  }
  select(id) {
    this.selected=id;this.paint();
    this.outline.visible=id!=='TRAIN';if(this.outline.visible)this.outline.box.setFromObject(this.groups.get(id));
  }
  /** Tint every part that carries one drawing number (e.g. all 40 road-wheel pivot pins). */
  mark(ids=[]) {
    this.marked=new Set(ids);
    if(this.marked.size&&!this.xray&&[...this.marked].some(id=>PART_MAP.get(id)?.carrier)){this.setXray(true);this.onXrayChanged?.(true);}
    this.paint();
  }
  setHover(id) {if(id===this.hovered)return;this.hovered=id;this.paint();}
  /** Fly the camera so the part fills the view; small parts keep their wheel carrier in frame. */
  focus(id=this.selected,{animate=true}={}) {
    const ids=Array.isArray(id)?id.filter(i=>this.groups.has(i)):[id];if(!ids.length)return;
    const part=ids.length===1?PART_MAP.get(ids[0]):{};
    if((part.carrier||['bracket','wheel','pin','bearing'].includes(part.type))&&!this.xray){this.setXray(true);this.onXrayChanged?.(true);}
    const box=new THREE.Box3();for(const i of ids)box.expandByObject(this.groups.get(i));const center=box.getCenter(new THREE.Vector3());
    let direction=this.camera.position.clone().sub(this.controls.target).normalize();
    if(this.pendingDirection){direction=this.pendingDirection;this.pendingDirection=null;}
    if(direction.lengthSq()<.1)direction.set(.8,.65,1).normalize();
    // Wheel carriers sit under the body: look from their own (outboard) side and from just above rail height.
    if(part.carrier){const s=part.carrier[0]==='L'?-1:1;direction=new THREE.Vector3(direction.x,Math.min(Math.max(direction.y,.18),.45),s*Math.max(Math.abs(direction.z),.6)).normalize();}
    const probe=this.camera.clone();probe.position.copy(center).addScaledVector(direction,10);probe.lookAt(center);
    const inverse=probe.quaternion.clone().invert(),tanV=Math.tan(this.camera.fov*Math.PI/360),tanH=tanV*this.camera.aspect;
    let distance=.85;
    for(const x of [box.min.x,box.max.x])for(const y of [box.min.y,box.max.y])for(const z of [box.min.z,box.max.z]) {
      const p=new THREE.Vector3(x,y,z).sub(center).applyQuaternion(inverse);
      distance=Math.max(distance,p.z+Math.abs(p.x)/tanH,p.z+Math.abs(p.y)/tanV);
    }
    const toPosition=center.clone().addScaledVector(direction,distance*1.25);
    if(!animate||reducedMotion()) {this.flight=null;this.controls.target.copy(center);this.camera.position.copy(toPosition);this.controls.update();this.requestRender();return;}
    this.flight={start:performance.now(),duration:520,fromTarget:this.controls.target.clone(),toTarget:center,fromPosition:this.camera.position.clone(),toPosition};
    this.requestRender();
  }
  fit() {this.orientation(this.mode);}
  orientation(mode,{animate=true}={}) {
    this.mode=mode;
    const dir={perspective:[.72,.55,1],top:[.001,1,.001],side:[0,.1,1],front:[1,.08,0]}[mode]||[.72,.55,1];
    this.pendingDirection=new THREE.Vector3(...dir).normalize();this.focus('TRAIN',{animate});
  }
  zoom(factor) {this.flight=null;this.camera.position.sub(this.controls.target).multiplyScalar(factor).add(this.controls.target);this.controls.update();this.requestRender();}
  setXray(on) {this.xray=on;this.paint();}
  isolate(on) {this.isolated=on;this.paint();}
}
