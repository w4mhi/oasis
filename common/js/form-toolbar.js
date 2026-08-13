/*
 * form-toolbar.js — the one action row for every OASIS form page.
 *
 * ICS-205/213/214/309 and the net log all render the same toolbar: the same
 * actions, in the same order, grouped the same way. Before this existed that
 * agreement was five copies of hand-written markup, and it drifted — different
 * labels for the same action, Print in three different positions, Import before
 * Export on one page only.
 *
 * A page now declares WHICH slots it has and WHAT they do. This file owns the
 * order, the grouping, the separators, the button classes and the shared labels,
 * so changing the row changes every page at once.
 *
 *   OasisFormToolbar.render({
 *     mount: document.querySelector('.actions'),
 *     noun:  'Form',                                  // or 'Log'
 *     slots: {
 *       savePdf:   { onClick: savePDF,   title: '…' },
 *       handoff:   { label: '→ Send via Winlink', onClick: sendWinlink, title: '…' },
 *       saveStore: { title: '…' },                    // wired later by OasisFormBackup.attach()
 *       loadStore: { title: '…' },
 *       print:     { onClick: doPrint,   title: '…' },
 *       exportCsv: { onClick: exportCSV, title: '…' },
 *       importCsv: { onClick: importCSVPrompt, title: '…' },
 *       extra:     { label: 'Load Frequency Plan', onClick: loadChirpPrompt, title: '…' },
 *       clear:     { onClick: clearAll,  title: '…' },
 *     },
 *   });
 *
 * An omitted slot is simply not rendered and its group collapses, so net-log —
 * which has no Save PDF — gets no leading gap and no stray separator.
 *
 * MUST be called before OasisFormBackup.attach(), which looks up #fb-save and
 * #fb-restore by id.
 *
 * Classic <script src>. plan() is pure and requireable by node --test.
 */
(function (root, factory) {
  var api = factory();
  root.OasisFormToolbar = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Canonical order, grouped by what the buttons do. Groups are separated by a
  // thin rule; `clear` is deliberately its own trailing group and gets no
  // separator, because CSS pushes it to the far end of the row anyway.
  var GROUPS = [
    ['savePdf', 'handoff'],       // produce / hand off
    ['saveStore', 'loadStore'],   // server storage
    ['exportCsv', 'importCsv'],   // interchange
    ['print'],                    // output
    ['extra'],                    // page-specific (Load Frequency Plan, …)
    ['clear'],                    // destructive
  ];

  // Per-slot defaults. `label` may contain {noun}, replaced with 'Form' or 'Log'
  // so the ICS pages read "Save Form" and the net log reads "Save Log".
  var SPEC = {
    savePdf:   { cls: 'sbtn primary', label: '⬇ Save PDF' },
    handoff:   { cls: 'sbtn primary' },
    saveStore: { cls: 'sbtn', id: 'fb-save',    label: 'Save {noun}' },
    loadStore: { cls: 'sbtn', id: 'fb-restore', label: 'Load {noun}' },
    print:     { cls: 'sbtn info', label: 'Print' },
    exportCsv: { cls: 'sbtn', label: 'Export CSV' },
    importCsv: { cls: 'sbtn', label: 'Import CSV' },
    extra:     { cls: 'sbtn' },
    clear:     { cls: 'sbtn danger', label: 'Clear' },
  };

  /**
   * Pure: descriptor -> ordered render plan. No DOM, so node --test can assert
   * the slot order and separator placement of every page without a browser.
   * Returns [{kind:'button'|'sep', slot, cls, label, id, title}].
   */
  function plan(opts) {
    opts = opts || {};
    var slots = opts.slots || {};
    var noun = opts.noun || 'Form';
    var out = [];
    var rendered = 0;   // groups emitted so far, so separators land between them

    GROUPS.forEach(function (group, gi) {
      var items = group.filter(function (name) {
        return Object.prototype.hasOwnProperty.call(slots, name) && slots[name];
      });
      if (!items.length) return;

      // A separator precedes every non-empty group except the first, and never
      // precedes `clear` — that one is pushed away by layout, not by a rule.
      var isClear = GROUPS[gi][0] === 'clear';
      if (rendered && !isClear) out.push({ kind: 'sep' });

      items.forEach(function (name) {
        var spec = SPEC[name];
        var given = slots[name] === true ? {} : slots[name];
        var label = given.label || (spec.label || '').replace('{noun}', noun);
        out.push({
          kind: 'button',
          slot: name,
          cls: given.cls || spec.cls,
          label: label,
          id: given.id || spec.id || null,
          title: given.title || '',
        });
      });
      rendered++;
    });
    return out;
  }

  /** Build the plan into `mount`, replacing whatever was there. */
  function render(opts) {
    var mount = opts && opts.mount;
    if (typeof mount === 'string') mount = document.querySelector(mount);
    if (!mount) throw new Error('OasisFormToolbar.render: mount not found');
    var slots = (opts && opts.slots) || {};

    mount.innerHTML = '';
    plan(opts).forEach(function (item) {
      if (item.kind === 'sep') {
        var sep = document.createElement('span');
        sep.className = 'sep';
        sep.setAttribute('aria-hidden', 'true');
        sep.textContent = '|';
        mount.appendChild(sep);
        return;
      }
      var b = document.createElement('button');
      b.type = 'button';
      b.className = item.cls;
      b.textContent = item.label;
      if (item.id) b.id = item.id;
      if (item.title) b.title = item.title;
      // saveStore/loadStore carry no handler here — OasisFormBackup.attach()
      // binds them by id once the page has declared its get/apply callbacks.
      var given = slots[item.slot];
      if (given && typeof given.onClick === 'function') {
        b.addEventListener('click', given.onClick);
      }
      mount.appendChild(b);
    });
    return mount;
  }

  return { GROUPS: GROUPS, SPEC: SPEC, plan: plan, render: render };
});
