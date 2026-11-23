/* AI 运维控制台 · 可拖动分隔条（终端 / AI 面板高度调节）
 * 独立脚本，不改动 app.js。拖动时用 flex-basis 像素控制终端高度，
 * AI 面板 flex:1 自动占满剩余；app.js 的 ResizeObserver 会在终端尺寸变化时自动 fit。
 */
(function () {
  'use strict';

  function init() {
    var sp = document.getElementById('splitter');
    var term = document.getElementById('termPanel');
    var ai = document.querySelector('.ai-panel');
    var main = document.querySelector('.main');
    if (!sp || !term || !ai || !main) return;

    var dragging = false, startY = 0, startTermH = 0, mainH = 0;
    // AI 面板至少要容得下底部控制区 + 一点结果，避免把输入区挤到页面下方看不到
    var MIN_TERM = 100, MIN_AI = 460;

    function yOf(e) {
      return (e.touches && e.touches[0]) ? e.touches[0].clientY : e.clientY;
    }

    function down(e) {
      dragging = true;
      startY = yOf(e);
      startTermH = term.getBoundingClientRect().height;
      mainH = main.getBoundingClientRect().height;
      sp.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'row-resize';
      if (e.cancelable) e.preventDefault();
    }

    function move(e) {
      if (!dragging) return;
      var dy = yOf(e) - startY;
      var maxTerm = mainH - MIN_AI - sp.offsetHeight - 2;
      var h = Math.max(MIN_TERM, Math.min(maxTerm, startTermH + dy));
      term.style.flex = '0 0 ' + h + 'px';
      ai.style.flex = '1 1 0';
      if (e.cancelable) e.preventDefault();
    }

    function up() {
      if (!dragging) return;
      dragging = false;
      sp.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      // 通知终端按新尺寸重新适配
      window.dispatchEvent(new Event('resize'));
      if (typeof window.AiOpsFitAll === 'function') {
        try { window.AiOpsFitAll(); } catch (e) { /* ignore */ }
      }
    }

    sp.addEventListener('mousedown', down);
    sp.addEventListener('touchstart', down, { passive: false });
    window.addEventListener('mousemove', move);
    window.addEventListener('touchmove', move, { passive: false });
    window.addEventListener('mouseup', up);
    window.addEventListener('touchend', up);

    // 双击分隔条：还原默认比例
    sp.addEventListener('dblclick', function () {
      term.style.flex = '';
      ai.style.flex = '';
      window.dispatchEvent(new Event('resize'));
    });
  }

  function boot() { init(); }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
