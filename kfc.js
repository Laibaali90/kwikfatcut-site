/* ==========================================================================
   Kwik Fat Cut shared script

   Replaces the IntersectionObserver snippet that was duplicated across 48
   files. Loaded with defer, so it never blocks rendering.

   Covers: FIX 2 (shield rotation), FIX 5 (mobile nav, current page),
           FIX 8 / FIX 12 (reveal fallbacks).
   ========================================================================== */
(function () {
  'use strict';

  /* ------------------------------------------------------------------
     0. Mark the document as JS-capable.
     kfc.css keeps .reveal blocks visible until this class lands, so a
     failed or blocked script can never leave a page looking empty.
     ------------------------------------------------------------------ */
  var root = document.documentElement;
  root.classList.add('js');

  var reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ------------------------------------------------------------------
     1. Scroll reveal
     ------------------------------------------------------------------ */
  function initReveal() {
    var els = document.querySelectorAll('.reveal');
    if (!els.length) return;

    if (reduceMotion || !('IntersectionObserver' in window)) {
      for (var i = 0; i < els.length; i++) els[i].classList.add('in');
      return;
    }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry, i) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        setTimeout(function () { el.classList.add('in'); }, i * 65);
        io.unobserve(el);
      });
    }, { threshold: 0.07, rootMargin: '0px 0px -5% 0px' });

    for (var j = 0; j < els.length; j++) io.observe(els[j]);

    /* Safety net. If anything stops the observer firing, reveal what is
       already on screen rather than leave the page looking blank. */
    setTimeout(function () {
      var stuck = document.querySelectorAll('.reveal:not(.in)');
      for (var k = 0; k < stuck.length; k++) {
        if (stuck[k].getBoundingClientRect().top < window.innerHeight) {
          stuck[k].classList.add('in');
        }
      }
    }, 2500);
  }

  /* ------------------------------------------------------------------
     2. Current page indicator
     ------------------------------------------------------------------ */
  function initCurrentPage() {
    var here = window.location.pathname.split('/').pop() || 'index.html';
    var home = ['', 'index.html', 'kwik-fat-cut-homepage.html'];

    var links = document.querySelectorAll('.nav-links a, .footer-nav a');
    for (var i = 0; i < links.length; i++) {
      var link = links[i];
      if (link.classList.contains('nav-btn')) continue;

      var target = (link.getAttribute('href') || '').split('#')[0].split('/').pop();
      var match = target === here ||
        (home.indexOf(target) > -1 && home.indexOf(here) > -1);

      if (match) {
        link.classList.add('active');
        link.setAttribute('aria-current', 'page');
      } else {
        link.classList.remove('active');
        link.removeAttribute('aria-current');
      }
    }
  }

  /* ------------------------------------------------------------------
     3. Mobile navigation
     ------------------------------------------------------------------ */
  function initMobileNav() {
    var toggle = document.querySelector('.nav-toggle');
    var links = document.querySelector('.nav-links');
    if (!toggle || !links) return;

    function setOpen(open) {
      links.classList.toggle('open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    toggle.addEventListener('click', function () {
      setOpen(toggle.getAttribute('aria-expanded') !== 'true');
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') setOpen(false);
    });

    /* Reset when the viewport stops being a phone. */
    if (window.matchMedia) {
      var wide = window.matchMedia('(min-width: 961px)');
      var onChange = function (e) { if (e.matches) setOpen(false); };
      if (wide.addEventListener) wide.addEventListener('change', onChange);
      else if (wide.addListener) wide.addListener(onChange);
    }
  }

  /* ------------------------------------------------------------------
     4. Day / night logo shield  (FIX 2)
     Yellow sunburst shield in daylight, dark red shield after dark.
     Re-checked on the hour so a long-lived tab stays correct.
     ------------------------------------------------------------------ */
  function applyShield() {
    var hour = new Date().getHours();
    root.setAttribute('data-shield', hour >= 6 && hour < 18 ? 'day' : 'night');
  }

  function initShield() {
    applyShield();
    var now = new Date();
    var msToNextHour =
      (60 - now.getMinutes()) * 60000 - now.getSeconds() * 1000 + 1000;
    setTimeout(function () {
      applyShield();
      setInterval(applyShield, 3600000);
    }, msToNextHour);
  }

  /* ------------------------------------------------------------------
     5. Share fallback
     Several pages define their own shareKFC with button feedback. Those
     win; this only fills in for pages that have none.
     ------------------------------------------------------------------ */
  if (typeof window.shareKFC !== 'function') {
    window.shareKFC = function () {
      var url = window.location.href;
      if (navigator.share) {
        navigator.share({ title: document.title, url: url }).catch(function () {});
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(url);
      }
    };
  }

  /* ------------------------------------------------------------------
     Boot
     ------------------------------------------------------------------ */
  function init() {
    initShield();
    initCurrentPage();
    initMobileNav();
    initReveal();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
