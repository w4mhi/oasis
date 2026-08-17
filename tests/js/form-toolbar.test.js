/*
 * form-toolbar.test.js — pins the canonical action row.
 *
 * These assertions are the contract the five form pages agreed to. If someone
 * reorders GROUPS or renames a shared label, this fails before the drift ships.
 */
const test = require('node:test');
const assert = require('node:assert');
const T = require('../../common/js/form-toolbar.js');

// Slots as each page declares them, mirroring the real descriptors.
const ICS_FORM = {
  savePdf: {}, handoff: { label: '→ Send via Winlink' },
  saveStore: {}, loadStore: {}, print: {}, exportCsv: {}, importCsv: {}, clear: {},
};
const ICS_205 = Object.assign({}, ICS_FORM, { extra: { label: 'Load Frequency Plan' } });
const NET_LOG = {
  handoff: { label: '→ ICS-309' },
  saveStore: {}, loadStore: {}, print: {},
  exportCsv: {}, exportAdif: {}, importCsv: {}, clear: {},
};

const labels = (p) => T.plan(p).map((i) => (i.kind === 'sep' ? '|' : i.label));

test('ICS forms render the canonical row', () => {
  assert.deepStrictEqual(labels({ noun: 'Form', slots: ICS_FORM }), [
    '⬇ Save PDF', '→ Send via Winlink', '|',
    'Save Form', 'Load Form', '|',
    'Export CSV', 'Import CSV', '|',
    'Print',
    'Clear',
  ]);
});

test('ICS-205 is the same row plus its frequency-plan slot', () => {
  const base = labels({ noun: 'Form', slots: ICS_FORM });
  const l205 = labels({ noun: 'Form', slots: ICS_205 });
  assert.deepStrictEqual(l205.filter((x) => x !== 'Load Frequency Plan' && x !== '|'),
                         base.filter((x) => x !== '|'));
  // the extra slot is its own group, so it is preceded by a separator
  assert.deepStrictEqual(l205.slice(-3), ['|', 'Load Frequency Plan', 'Clear']);
});

test('a missing slot collapses its group — no leading gap, no stray separator', () => {
  const l = labels({ noun: 'Log', slots: NET_LOG });
  assert.strictEqual(l[0], '→ ICS-309', 'no empty leading group where Save PDF would be');
  assert.strictEqual(l[1], '|');
  assert.ok(!l.includes('⬇ Save PDF'));
  // never two separators in a row, and never one at either end
  assert.ok(!/\|\s*\|/.test(l.join(' ')));
  assert.notStrictEqual(l[l.length - 1], '|');
});

test('noun switches the storage labels, and only those', () => {
  assert.ok(labels({ noun: 'Log', slots: NET_LOG }).includes('Save Log'));
  assert.ok(labels({ noun: 'Log', slots: NET_LOG }).includes('Load Log'));
  assert.ok(labels({ noun: 'Form', slots: ICS_FORM }).includes('Save Form'));
  // Export/Import CSV are format names, not nouns — they never change
  assert.ok(labels({ noun: 'Log', slots: NET_LOG }).includes('Export CSV'));
});

test('Clear is last and never separated from what precedes it', () => {
  for (const [noun, slots] of [['Form', ICS_FORM], ['Form', ICS_205], ['Log', NET_LOG]]) {
    const l = labels({ noun, slots });
    assert.strictEqual(l[l.length - 1], 'Clear');
    assert.notStrictEqual(l[l.length - 2], '|', 'clear gets no separator — layout pushes it right');
  }
});

test('storage slots carry the ids OasisFormBackup.attach() looks up', () => {
  const p = T.plan({ noun: 'Form', slots: ICS_FORM });
  assert.strictEqual(p.find((i) => i.slot === 'saveStore').id, 'fb-save');
  assert.strictEqual(p.find((i) => i.slot === 'loadStore').id, 'fb-restore');
});

test('emphasis: primary on produce/send, danger on clear, plain elsewhere', () => {
  const p = T.plan({ noun: 'Form', slots: ICS_205 }).filter((i) => i.kind === 'button');
  const cls = Object.fromEntries(p.map((i) => [i.slot, i.cls]));
  assert.strictEqual(cls.savePdf, 'sbtn primary');
  assert.strictEqual(cls.handoff, 'sbtn primary');
  assert.strictEqual(cls.clear, 'sbtn danger');
  assert.strictEqual(cls.print, 'sbtn info', 'Print carries the blue accent');
  ['saveStore', 'loadStore', 'exportCsv', 'importCsv', 'extra']
    .forEach((s) => assert.strictEqual(cls[s], 'sbtn', s + ' should be plain'));
});

test('Export ADIF follows Import CSV in a group of its own', () => {
  const l = labels({ noun: 'Log', slots: NET_LOG });
  const i = l.indexOf('Export ADIF');
  assert.notStrictEqual(i, -1, 'net log declares the slot');
  // A rule separates it from the CSV pair: those two are the log's round trip
  // with itself, ADIF is a one-way handoff to a program nothing here reads back.
  assert.deepStrictEqual(l.slice(i - 3, i + 1),
                         ['Export CSV', 'Import CSV', '|', 'Export ADIF']);
  assert.strictEqual(T.plan({ noun: 'Log', slots: NET_LOG })
    .find((x) => x.slot === 'exportAdif').cls, 'sbtn');
});

test('the ICS forms do not grow an ADIF button — they have no QSOs', () => {
  assert.ok(!labels({ noun: 'Form', slots: ICS_FORM }).includes('Export ADIF'));
  assert.ok(!labels({ noun: 'Form', slots: ICS_205 }).includes('Export ADIF'));
});

test('all three page shapes agree on the order of every shared slot', () => {
  const order = (slots) => T.plan({ noun: 'Form', slots })
    .filter((i) => i.kind === 'button').map((i) => i.slot);
  const shared = ['saveStore', 'loadStore', 'exportCsv', 'importCsv', 'print', 'clear'];
  const keep = (a) => a.filter((s) => shared.includes(s));
  assert.deepStrictEqual(keep(order(ICS_FORM)), shared);
  assert.deepStrictEqual(keep(order(ICS_205)), shared);
  assert.deepStrictEqual(keep(order(NET_LOG)), shared);
});
