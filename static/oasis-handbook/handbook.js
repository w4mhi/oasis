// OASIS Handbook — shared JS
// Theming is handled by the suite-standard server-injected theme.js
// (sun/moon toggle, `oasis_theme` key, [data-theme="light"]). The inline
// #theme-toggle next to HOME suppresses theme.js's floating button; theme.js
// provides window.toggleTheme and syncs the #theme-glyph on load.

// Active sidebar link
(function () {
  var cur = location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.sidebar nav a').forEach(function (a) {
    if (a.getAttribute('href') === cur) a.classList.add('active');
  });
})();

// Lightbox
(function () {
  var lb = document.createElement('div');
  lb.className = 'lb';
  lb.innerHTML = '<img alt="" />';
  document.body.appendChild(lb);
  var img = lb.querySelector('img');
  document.querySelectorAll('.figure img, .hero img').forEach(function (el) {
    el.addEventListener('click', function () { img.src = el.src; lb.classList.add('open'); document.body.style.overflow = 'hidden'; });
  });
  function close() { lb.classList.remove('open'); document.body.style.overflow = ''; img.src = ''; }
  lb.addEventListener('click', close);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
})();

// Build + scroll-spy the right TOC from h2/h3
(function () {
  var toc = document.querySelector('.toc ul');
  if (!toc) return;
  var heads = Array.prototype.slice.call(document.querySelectorAll('.content h2, .content h3'));
  var links = [];
  heads.forEach(function (h) {
    if (!h.id) h.id = h.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    var li = document.createElement('li');
    var a = document.createElement('a');
    a.href = '#' + h.id;
    a.textContent = h.textContent;
    if (h.tagName === 'H3') a.style.paddingLeft = '1.5rem';
    li.appendChild(a); toc.appendChild(li); links.push(a);
  });
  var byId = {};
  links.forEach(function (a) { byId[a.getAttribute('href').slice(1)] = a; });
  var obs = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) {
        links.forEach(function (l) { l.classList.remove('active'); });
        var a = byId[e.target.id];
        if (a) a.classList.add('active');
      }
    });
  }, { rootMargin: '-10% 0px -75% 0px', threshold: 0 });
  heads.forEach(function (h) { obs.observe(h); });
})();
