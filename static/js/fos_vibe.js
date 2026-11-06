/* FOS·RAG 动效层：侧栏滑块、卡片聚光/倾斜、数字滚动、按钮涟漪、页头尘埃、Toast 星屑。
   样式在 fos_fx.css；prefers-reduced-motion: reduce 时只移除入场标记、不挂任何动效。 */
(function () {
  'use strict';
  var html = document.documentElement;
  var mq = function (q) { return window.matchMedia && matchMedia(q).matches; };
  var reduce = mq('(prefers-reduced-motion: reduce)');
  var fine = mq('(hover: hover) and (pointer: fine)');

  setTimeout(function () { html.classList.remove('fx-enter'); }, 1700);
  if (reduce) return;

  /* ── 侧栏：跟随悬停的滑块，颜色随目标导航项的色相变化 ── */
  (function () {
    var nav = document.getElementById('nav');
    if (!nav || !fine) return;
    var g = document.createElement('span');
    g.className = 'nav-glider';
    g.setAttribute('aria-hidden', 'true');
    nav.insertBefore(g, nav.firstChild);
    nav.classList.add('has-glider');
    var shown = false;
    nav.addEventListener('pointerover', function (e) {
      var it = e.target.closest('.nav-item');
      if (!it || !nav.contains(it)) return;
      if (!shown) { g.style.transition = 'none'; }
      g.style.transform = 'translateY(' + it.offsetTop + 'px)';
      g.style.height = it.offsetHeight + 'px';
      g.dataset.tone = it.dataset.tone || 'blue';
      if (!shown) { void g.offsetWidth; g.style.transition = ''; shown = true; }
      g.classList.add('on');
    });
    nav.addEventListener('pointerleave', function () { g.classList.remove('on'); shown = false; });
  })();

  /* ── 卡片：指针聚光（--mx/--my）与轻微倾斜（--rx/--ry），rAF 节流 ── */
  (function () {
    if (!fine) return;
    var GLOW = '.kb-card,.stat-card,.card,.fos-tag,.home-feats .f,.dash-act-row';
    var TILT = '.kb-card,.stat-card,.home-feats .f';
    var cur = null, ev = null, raf = 0;
    function clear(el) { el.style.removeProperty('--rx'); el.style.removeProperty('--ry'); }
    function tick() {
      raf = 0;
      var t = ev.target, el = t && t.closest ? t.closest(GLOW) : null;
      if (el !== cur) { if (cur) clear(cur); cur = el; }
      if (!el) return;
      var r = el.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
      el.style.setProperty('--mx', x.toFixed(0) + 'px');
      el.style.setProperty('--my', y.toFixed(0) + 'px');
      if (el.matches(TILT)) {
        el.style.setProperty('--ry', ((x / r.width - .5) * 6).toFixed(2) + 'deg');
        el.style.setProperty('--rx', ((.5 - y / r.height) * 6).toFixed(2) + 'deg');
      }
    }
    document.addEventListener('pointermove', function (e) {
      if (e.pointerType !== 'mouse') return;
      ev = e;
      if (!raf) raf = requestAnimationFrame(tick);
    }, {passive: true});
    document.addEventListener('mouseout', function (e) {
      if (!e.relatedTarget && cur) { clear(cur); cur = null; }
    });
  })();

  /* ── 数字滚动：纯整数统计值进入视口时从 0 滚到目标值 ── */
  (function () {
    if (!('IntersectionObserver' in window)) return;
    var els = document.querySelectorAll('.stat-card .num,.kb-stats .stat .v,.home-stats .n,[data-count-up]');
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        io.unobserve(en.target);
        run(en.target);
      });
    }, {threshold: .3});
    els.forEach(function (el) {
      var raw = el.textContent.trim();
      if (!/^\d[\d,]*$/.test(raw)) return;
      var n = parseInt(raw.replace(/,/g, ''), 10);
      if (!(n > 1)) return;
      el.dataset.fxTarget = n;
      el.dataset.fxComma = raw.indexOf(',') >= 0 ? '1' : '';
      io.observe(el);
    });
    function fmt(v, comma) { return comma ? v.toLocaleString('en-US') : String(v); }
    function run(el) {
      var n = +el.dataset.fxTarget, comma = el.dataset.fxComma;
      var dur = Math.min(1700, 700 + Math.log10(n) * 300);
      var delay = html.classList.contains('fx-enter') ? 260 : 0, t0 = 0;
      el.textContent = '0';
      setTimeout(function () {
        requestAnimationFrame(function step(now) {
          if (!t0) t0 = now;
          var p = Math.min(1, (now - t0) / dur);
          el.textContent = fmt(Math.round(n * (1 - Math.pow(2, -10 * p))), comma);
          if (p < 1) requestAnimationFrame(step);
          else el.textContent = fmt(n, comma);
        });
      }, delay);
    }
  })();

  /* ── 涟漪：按钮与侧栏导航 ── */
  document.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) return;
    var b = e.target.closest('.btn,.fos-sidebar .nav-item');
    if (!b || b.disabled || b.tagName === 'INPUT') return;
    var r = b.getBoundingClientRect(), s = Math.max(r.width, r.height) * 2.2;
    var w = document.createElement('span'), dot = document.createElement('i');
    w.className = 'fx-ripple';
    w.setAttribute('aria-hidden', 'true');
    dot.style.cssText = 'width:' + s + 'px;height:' + s + 'px;left:' + (e.clientX - r.left - s / 2) + 'px;top:' + (e.clientY - r.top - s / 2) + 'px';
    w.appendChild(dot);
    b.appendChild(w);
    setTimeout(function () { w.remove(); }, 700);
  });

  /* ── 星屑迸发：成功类 Toast 出现时从图标处散开 ── */
  var SPARK = ['#60A5FA', '#A78BFA', '#34D399', '#FBBF24', '#F472B6'];
  function burst(x, y) {
    if (!Element.prototype.animate) return;
    for (var i = 0; i < 14; i++) {
      var p = document.createElement('i'), a = Math.random() * Math.PI * 2, d = 24 + Math.random() * 46, s = 3 + Math.random() * 4;
      p.className = 'fx-spark';
      p.style.cssText = 'left:' + x + 'px;top:' + y + 'px;width:' + s + 'px;height:' + s + 'px;background:' + SPARK[i % SPARK.length];
      document.body.appendChild(p);
      p.animate([
        {transform: 'translate(-50%,-50%) scale(1)', opacity: 1},
        {transform: 'translate(calc(-50% + ' + (Math.cos(a) * d).toFixed(1) + 'px),calc(-50% + ' + (Math.sin(a) * d).toFixed(1) + 'px)) scale(0)', opacity: 0}
      ], {duration: 600 + Math.random() * 350, easing: 'cubic-bezier(.2,.8,.2,1)'}).onfinish = (function (el) { return function () { el.remove(); }; })(p);
    }
  }
  window.fxBurst = burst;
  if (window.fosToast) {
    var toast = window.fosToast;
    window.fosToast = function (text, type) {
      toast.apply(this, arguments);
      if ((type || 'ok') !== 'ok') return;
      var host = document.getElementById('toastHost'), t = host && host.lastElementChild;
      if (t) setTimeout(function () { var r = t.getBoundingClientRect(); burst(r.left + 23, r.top + 22); }, 160);
    };
  }

  /* ── 页头尘埃：随风漂移的光点，指针靠近时被推开并连线 ── */
  function hexRgb(h) {
    h = (h || '').trim().replace('#', '');
    if (h.length === 3) h = h.replace(/./g, '$&$&');
    var n = parseInt(h, 16);
    return isNaN(n) ? '37,99,235' : [(n >> 16) & 255, (n >> 8) & 255, n & 255].join(',');
  }
  function headDust(head) {
    var cv = document.createElement('canvas');
    cv.className = 'fx-dust';
    cv.setAttribute('aria-hidden', 'true');
    head.insertBefore(cv, head.firstChild);
    var ctx = cv.getContext('2d');
    if (!ctx) return;
    var W = 0, H = 0, parts = [], mx = -1e4, my = -1e4, hovering = false;
    var running = false, visible = true, last = 0, lastDraw = 0, rgbLight = '37,99,235', rgbDark = '147,197,253';
    var cs = getComputedStyle(head);
    if (cs.getPropertyValue('--t2')) { rgbLight = hexRgb(cs.getPropertyValue('--t2')); rgbDark = hexRgb(cs.getPropertyValue('--t1')); }
    function mk(anyX) {
      return {x: anyX ? Math.random() * W : -8, y: Math.random() * H, ox: 0, oy: 0,
        vx: 5 + Math.random() * 13, vy: -(1 + Math.random() * 4), r: .5 + Math.random() * 1.8,
        a: .18 + Math.random() * .5, ph: Math.random() * 6.283, sp: .6 + Math.random() * 1.6};
    }
    function resize() {
      var r = head.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      W = r.width; H = r.height;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var n = Math.round(Math.min(90, Math.max(26, W * H / 4200)));
      while (parts.length < n) parts.push(mk(true));
      parts.length = n;
    }
    function frame(now) {
      if (!running) return;
      requestAnimationFrame(frame);
      if (!hovering && now - lastDraw < 32) return;
      var dt = Math.min(.05, (now - (last || now)) / 1000);
      last = now; lastDraw = now;
      var rgb = html.dataset.theme === 'dark' ? rgbDark : rgbLight, R = 110, t = now / 1000;
      ctx.clearRect(0, 0, W, H);
      ctx.lineWidth = .6;
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        p.x += p.vx * dt;
        p.y += p.vy * dt + Math.sin(t * p.sp + p.ph) * 4 * dt;
        if (p.x > W + 8) { p.x = -8; p.y = Math.random() * H; }
        if (p.y < -8) p.y = H + 8;
        var dx = p.x - mx, dy = p.y - my, d = Math.sqrt(dx * dx + dy * dy), tx = 0, ty = 0;
        if (d < R && d > .01) { var f = 1 - d / R; f = f * f * 46; tx = dx / d * f; ty = dy / d * f; }
        var k = Math.min(1, dt * 7);
        p.ox += (tx - p.ox) * k; p.oy += (ty - p.oy) * k;
        var x = p.x + p.ox, y = p.y + p.oy, a = p.a * (.6 + .4 * Math.sin(t * p.sp * 2 + p.ph));
        ctx.fillStyle = 'rgba(' + rgb + ',' + a.toFixed(3) + ')';
        ctx.beginPath(); ctx.arc(x, y, p.r, 0, 6.283); ctx.fill();
        if (d < R * 1.3) {
          ctx.strokeStyle = 'rgba(' + rgb + ',' + ((1 - d / (R * 1.3)) * .26).toFixed(3) + ')';
          ctx.beginPath(); ctx.moveTo(mx, my); ctx.lineTo(x, y); ctx.stroke();
        }
      }
    }
    function sync() {
      var want = visible && !document.hidden;
      if (want && !running) { running = true; last = 0; requestAnimationFrame(frame); }
      else if (!want) running = false;
    }
    resize();
    if ('ResizeObserver' in window) new ResizeObserver(resize).observe(head);
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (es) { visible = es[0].isIntersecting; sync(); }).observe(head);
    }
    document.addEventListener('visibilitychange', sync);
    head.addEventListener('pointermove', function (e) {
      var r = head.getBoundingClientRect();
      mx = e.clientX - r.left; my = e.clientY - r.top; hovering = true;
    }, {passive: true});
    head.addEventListener('pointerleave', function () { mx = my = -1e4; hovering = false; });
    sync();
  }
  document.querySelectorAll('.fos-container .page-head').forEach(headDust);
})();
