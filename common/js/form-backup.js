/*
 * form-backup.js — "save to server" client helper for OASIS forms + net log.
 *
 * Backs the server-side backup store (POST /api/forms/save, GET /api/forms/list,
 * restore by fetching /static/<kind>/saved/<name>). ICS forms and the net-check-in
 * log otherwise live only in browser localStorage; this lets an operator persist a
 * snapshot onto the device so a cleared cache or a swapped tablet doesn't lose
 * operational records.
 *
 * Self-contained: the restore picker and the toast inject their own inline-styled
 * DOM, so any page can wire save/restore with a few lines and no extra CSS. Classic
 * <script src="/common/js/form-backup.js">; pure helpers (stamp/slug/filenameFor)
 * are also requireable by node --test.
 */
(function (root, factory) {
  var api = factory();
  root.OasisFormBackup = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // ── Pure helpers (node-testable) ───────────────────────────────────────────
  function stamp(d) {
    d = d || new Date();
    var z = function (n) { return String(n).padStart(2, '0'); };
    return '' + d.getFullYear() + z(d.getMonth() + 1) + z(d.getDate()) +
      '-' + z(d.getHours()) + z(d.getMinutes());
  }
  function slug(s, fallback) {
    s = (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
    return s || (fallback || 'snapshot');
  }
  function filenameFor(kind, label) {
    return kind + '-' + slug(label, 'snapshot') + '-' + stamp() + '.json';
  }
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function _kb(bytes) {
    if (bytes == null) return '';
    return bytes < 1024 ? bytes + ' B' : (bytes / 1024).toFixed(1) + ' KB';
  }
  function _when(mtime) {
    if (!mtime) return '';
    var d = new Date(mtime * 1000);
    if (isNaN(d)) return '';
    var z = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '-' + z(d.getMonth() + 1) + '-' + z(d.getDate()) +
      ' ' + z(d.getHours()) + ':' + z(d.getMinutes());
  }

  // ── Server calls ───────────────────────────────────────────────────────────
  // Every call below reads the response body on EVERY path, ok or not. An unread
  // fetch() body pins a 2 MiB /dev/shm data pipe until Chromium collects the
  // Response, and on a form left open through an incident that is as good as
  // forever -- the same leak that cost a kiosk ~500 MB/h once already. Reading is
  // what drains the pipe, so the throw-on-failure paths drain first and throw
  // after; whether the parse succeeded is beside the point.
  async function save(kind, filename, contentStr) {
    var res = await fetch('/api/forms/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-OASIS-Request': '1' },
      body: JSON.stringify({ kind: kind, filename: filename, content: contentStr })
    });
    var j = await res.json().catch(function () { return {}; });
    if (!res.ok || !j.ok) throw new Error(j.error || ('HTTP ' + res.status));
    return j; // { ok, saved }
  }
  // ext: 'json' (default, form snapshots) or 'csv' (exports). Both live in the
  // same designated folder; the extension is what separates the two pickers.
  async function list(kind, ext) {
    var url = '/api/forms/list?kind=' + encodeURIComponent(kind) +
      (ext ? '&ext=' + encodeURIComponent(ext) : '');
    var res = await fetch(url, { cache: 'no-store' });
    var j = await res.json().catch(function () { return {}; });
    if (!res.ok || !j.ok) throw new Error(j.error || ('HTTP ' + res.status));
    return j.files || [];
  }
  function _savedUrl(kind, name) {
    return '/static/' + encodeURIComponent(kind) + '/saved/' + encodeURIComponent(name);
  }
  async function load(kind, name) {
    var res = await fetch(_savedUrl(kind, name), { cache: 'no-store' });
    if (!res.ok) { await res.text().catch(function () {}); throw new Error('HTTP ' + res.status); }
    return await res.json();
  }
  // Raw-text sibling of load(), for the CSV exports the JSON parser would choke on.
  async function loadText(kind, name) {
    var res = await fetch(_savedUrl(kind, name), { cache: 'no-store' });
    if (!res.ok) { await res.text().catch(function () {}); throw new Error('HTTP ' + res.status); }
    return await res.text();
  }
  // Directory listing anywhere inside the OASIS tree. The endpoint is hard-scoped
  // to SUITE_ROOT server-side, so there is no path to the operator's own machine.
  async function browse(path) {
    var res = await fetch('/api/browse?path=' + encodeURIComponent(path || ''),
      { cache: 'no-store' });
    var j = await res.json().catch(function () { return {}; });
    if (!res.ok || !j.ok) throw new Error(j.error || ('HTTP ' + res.status));
    return j;
  }
  // Fetch any file inside the OASIS tree as text (server-side read, not a upload).
  async function readTree(path) {
    var res = await fetch('/' + String(path || '').replace(/^\/+/, ''), { cache: 'no-store' });
    if (!res.ok) { await res.text().catch(function () {}); throw new Error('HTTP ' + res.status); }
    return await res.text();
  }

  // ── Self-contained toast ───────────────────────────────────────────────────
  function toast(msg, isErr) {
    var el = document.createElement('div');
    el.textContent = msg;
    el.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);' +
      'z-index:100000;padding:9px 16px;border-radius:6px;font:14px system-ui,sans-serif;' +
      'color:#fff;box-shadow:0 4px 16px rgba(0,0,0,.4);max-width:80vw;' +
      'background:' + (isErr ? 'var(--red,#c0392b)' : 'var(--green,#2f9e44)') + ';';
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, isErr ? 6000 : 3500);
  }

  // ── Shared overlay shell ───────────────────────────────────────────────────
  // Returns { close, listEl, footEl }. Every picker in this file is this shell
  // plus a body renderer, so the pages need no CSS of their own.
  var _ROW = 'display:flex;width:100%;text-align:left;gap:10px;align-items:baseline;' +
    'background:none;border:0;border-bottom:1px solid var(--border-dim,rgba(255,255,255,.06));' +
    'color:inherit;padding:9px 8px;cursor:pointer;font:inherit;';
  var _DIM = 'color:var(--text-dim,#8b949e);white-space:nowrap;';

  function _shell(title) {
    var ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.6);' +
      'display:flex;align-items:center;justify-content:center;';
    var box = document.createElement('div');
    box.style.cssText = 'background:var(--panel,#161b22);color:var(--text,#e6edf3);' +
      'border:1px solid var(--border,#30363d);border-radius:8px;width:min(560px,92vw);' +
      'max-height:80vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.5);' +
      'font:14px system-ui,sans-serif;';
    box.innerHTML =
      '<div style="padding:12px 16px;border-bottom:1px solid var(--border,#30363d);' +
      'display:flex;align-items:center;gap:8px;"><strong data-fb-title style="flex:1;">' +
      _esc(title) + '</strong>' +
      '<button data-fb-close style="background:none;border:1px solid var(--border,#30363d);' +
      'color:inherit;border-radius:5px;padding:2px 9px;cursor:pointer;">✕</button></div>' +
      '<div data-fb-list style="overflow-y:auto;padding:8px;flex:1;">Loading…</div>' +
      '<div data-fb-foot style="padding:10px 12px;border-top:1px solid var(--border,#30363d);' +
      'display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;"></div>';
    ov.appendChild(box);
    document.body.appendChild(ov);
    var close = function () { ov.remove(); };
    ov.addEventListener('click', function (e) { if (e.target === ov) close(); });
    box.querySelector('[data-fb-close]').addEventListener('click', close);
    return {
      close: close,
      listEl: box.querySelector('[data-fb-list]'),
      footEl: box.querySelector('[data-fb-foot]'),
      titleEl: box.querySelector('[data-fb-title]')
    };
  }

  // Adds a footer button. Pages use this to hang "Browse local files…" beside
  // the server list without owning any markup.
  function _footBtn(footEl, label, fn) {
    var b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = 'background:none;border:1px solid var(--border,#30363d);color:inherit;' +
      'border-radius:5px;padding:5px 11px;cursor:pointer;font:inherit;';
    b.onmouseenter = function () { b.style.background = 'var(--panel-2,rgba(255,255,255,.05))'; };
    b.onmouseleave = function () { b.style.background = 'none'; };
    b.addEventListener('click', fn);
    footEl.appendChild(b);
    return b;
  }

  // ── Load / import picker (the kind's designated folder) ───────────────────
  // opts: { title, ext, emptyText, actions:[{label, onClick(close)}] }
  function openPicker(kind, onLoad, opts) {
    opts = opts || {};
    var sh = _shell(opts.title || 'Load from server storage');
    (opts.actions || []).forEach(function (a) {
      _footBtn(sh.footEl, a.label, function () { a.onClick(sh.close); });
    });
    list(kind, opts.ext).then(function (files) {
      if (!files.length) {
        sh.listEl.innerHTML = '<div style="padding:14px;color:var(--text-dim,#8b949e);">' +
          _esc(opts.emptyText || 'Nothing saved yet — use Save first.') +
          '</div>';
        return;
      }
      sh.listEl.innerHTML = '';
      files.forEach(function (f) {
        var row = document.createElement('button');
        row.style.cssText = _ROW;
        row.onmouseenter = function () { row.style.background = 'var(--panel-2,rgba(255,255,255,.05))'; };
        row.onmouseleave = function () { row.style.background = 'none'; };
        row.innerHTML = '<span style="flex:1;word-break:break-all;">' + _esc(f.name) + '</span>' +
          '<span style="' + _DIM + '">' +
          _esc([_when(f.mtime), _kb(f.size)].filter(Boolean).join(' · ')) + '</span>';
        row.onclick = function () { sh.close(); onLoad(f.name); };
        sh.listEl.appendChild(row);
      });
    }).catch(function (err) {
      sh.listEl.innerHTML = '<div style="padding:14px;color:var(--red,#f85149);">' +
        'Could not list files: ' + _esc(err.message) + '</div>';
    });
    return sh;
  }

  // ── Device (server) tree browser ───────────────────────────────────────────
  // Navigates the OASIS tree via /api/browse and hands back the picked file's
  // suite-relative path. opts: { title, start, accept (e.g. '.csv'), actions }
  function openBrowser(onPick, opts) {
    opts = opts || {};
    var accept = (opts.accept || '').toLowerCase();
    var sh = _shell(opts.title || 'Browse device files');
    (opts.actions || []).forEach(function (a) {
      _footBtn(sh.footEl, a.label, function () { a.onClick(sh.close); });
    });

    function render(path) {
      sh.listEl.innerHTML = '<div style="padding:14px;color:var(--text-dim,#8b949e);">Loading…</div>';
      sh.titleEl.textContent = (opts.title || 'Browse device files') + ' — /' + (path || '');
      browse(path).then(function (j) {
        var entries = (j.entries || []).filter(function (e) {
          return e.type === 'dir' || !accept || e.name.toLowerCase().endsWith(accept);
        });
        sh.listEl.innerHTML = '';
        if (path) {
          var up = document.createElement('button');
          up.style.cssText = _ROW;
          up.innerHTML = '<span style="flex:1;">../</span>';
          up.onclick = function () { render(path.split('/').slice(0, -1).join('/')); };
          sh.listEl.appendChild(up);
        }
        if (!entries.length) {
          sh.listEl.insertAdjacentHTML('beforeend',
            '<div style="padding:14px;color:var(--text-dim,#8b949e);">Nothing here' +
            (accept ? ' matching ' + _esc(accept) : '') + '.</div>');
          return;
        }
        entries.forEach(function (e) {
          var isDir = e.type === 'dir';
          var row = document.createElement('button');
          row.style.cssText = _ROW;
          row.onmouseenter = function () { row.style.background = 'var(--panel-2,rgba(255,255,255,.05))'; };
          row.onmouseleave = function () { row.style.background = 'none'; };
          row.innerHTML = '<span style="flex:1;word-break:break-all;">' +
            _esc(e.name) + (isDir ? '/' : '') + '</span><span style="' + _DIM + '">' +
            _esc(isDir ? '' : [_when(e.modified), _kb(e.size)].filter(Boolean).join(' · ')) +
            '</span>';
          var child = (path ? path + '/' : '') + e.name;
          row.onclick = isDir
            ? function () { render(child); }
            : function () { sh.close(); onPick(child); };
          sh.listEl.appendChild(row);
        });
      }).catch(function (err) {
        sh.listEl.innerHTML = '<div style="padding:14px;color:var(--red,#f85149);">' +
          'Could not browse: ' + _esc(err.message) + '</div>';
      });
    }
    render(opts.start || '');
    return sh;
  }

  // ── One-call wiring for a page ─────────────────────────────────────────────
  // opts: { kind, saveButton, restoreButton, getData()->obj|null, applyData(obj),
  //         label?:string|fn, hasData?:()->bool, noun?:string }
  function attach(opts) {
    var kind = opts.kind;
    var noun = opts.noun || 'form';
    if (opts.saveButton) {
      opts.saveButton.addEventListener('click', async function () {
        var data;
        try { data = opts.getData(); } catch (e) { data = null; }
        if (data == null) { toast('Nothing to save yet', true); return; }
        var label = typeof opts.label === 'function' ? opts.label() : opts.label;
        var filename = filenameFor(kind, label);
        try {
          var j = await save(kind, filename, JSON.stringify(data, null, 2));
          toast('✓ Saved to ' + (j.saved || filename));
        } catch (err) { toast('Save failed: ' + err.message, true); }
      });
    }
    if (opts.restoreButton) {
      opts.restoreButton.addEventListener('click', function () {
        openPicker(kind, async function (name) {
          var obj;
          try { obj = await load(kind, name); }
          catch (err) { toast('Load failed: ' + err.message, true); return; }
          if (!obj || typeof obj !== 'object') { toast('That file is not a valid ' + noun, true); return; }
          if (opts.hasData && opts.hasData() &&
              !confirm('Load "' + name + '"? This replaces the current ' + noun + '.')) return;
          try { opts.applyData(obj); toast('✓ Loaded ' + name); }
          catch (err) { toast('Load failed: ' + err.message, true); }
        }, { title: 'Load ' + noun + ' from server storage' });
      });
    }
  }

  // ── Local-machine escape hatches ───────────────────────────────────────────
  // The server is the default target everywhere; these two keep the old
  // browser-side behaviour available behind an explicitly-labelled button.
  function pickLocal(onText, accept) {
    var inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = accept || '.csv';
    inp.style.display = 'none';
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0];
      if (f) {
        var r = new FileReader();
        r.onload = function (e) { onText(e.target.result, f.name); };
        r.readAsText(f);
      }
      inp.remove();
    });
    document.body.appendChild(inp);
    inp.click();
  }
  function saveLocal(filename, text, mime) {
    var blob = new Blob([text], { type: mime || 'text/csv' });
    var url = URL.createObjectURL(blob);
    var a = Object.assign(document.createElement('a'), { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  return {
    stamp: stamp, slug: slug, filenameFor: filenameFor,
    save: save, list: list, load: load, loadText: loadText,
    browse: browse, readTree: readTree,
    toast: toast, openPicker: openPicker, openBrowser: openBrowser, attach: attach,
    pickLocal: pickLocal, saveLocal: saveLocal
  };
});
