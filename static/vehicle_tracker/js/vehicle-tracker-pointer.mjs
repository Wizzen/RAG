// Track the furthest distance, not just the release point: a rotate-and-return
// gesture must not become a selection when it finishes at its starting point.
export class PointerSelection {
  constructor(threshold=5) {this.threshold=threshold;this.down=null;}
  start(e) {if(e.button===0)this.down={x:e.clientX,y:e.clientY,pointerId:e.pointerId,max:0};}
  move(e) {if(this.down?.pointerId===e.pointerId)this.down.max=Math.max(this.down.max,Math.hypot(e.clientX-this.down.x,e.clientY-this.down.y));}
  end(e) {
    const d=this.down;this.down=null;
    return !!d&&e.button===0&&e.pointerId===d.pointerId&&d.max<=this.threshold&&Math.hypot(e.clientX-d.x,e.clientY-d.y)<=this.threshold;
  }
  cancel() {this.down=null;}
}
