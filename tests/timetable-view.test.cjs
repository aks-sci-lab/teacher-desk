const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'js', 'views', 'timetable.js'), 'utf8')
    .replace(/^import .*;\n/gm, '').replace(/^export /gm, '');

function setup(response) {
    const pane = {};
    const printArea = {};
    let copied = '';
    const el = (spec, attrs, ...children) => {
        if (typeof attrs !== 'object' || attrs === null || Array.isArray(attrs) || attrs.spec) {
            children.unshift(attrs);
            attrs = {};
        }
        return { spec, attrs, children: children.flat(Infinity).filter(x => x != null && x !== false),
            cloneNode() { return { ...this }; } };
    };
    const context = vm.createContext({
        el, clear: (node, ...children) => { node.children = children.filter(Boolean); },
        api: { get: async () => response },
        todayIso: () => '2026-11-03',
        parseIso: iso => new Date(iso + 'T00:00:00'),
        addDays: (iso, n) => { const d = new Date(iso + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); },
        Date: class extends Date { constructor(...args) { super(...(args.length ? args : ['2026-11-03T10:00:00'])); } },
        navigator: { clipboard: { writeText: async text => { copied = text; } } },
        document: { getElementById: id => id === 'print-area' ? printArea : null },
        window: { print() { context.printed = true; } },
        guarded: fn => fn, toast() {}, on() {}, registerView() {}, pane, rerenders: 0,
    });
    vm.runInContext(source, context);
    vm.runInContext(`
        info = { me: 1, classes: ['2-3'], default_class: '2-3',
            slots: [{key:'0-1',p:1},{key:'1-1',p:1}], periods: {'1':{start:'09:00',end:'09:50'}},
            teachers: [{id:1,name:'김교사'}, {id:2,name:'이교사'}] };
        viewClass = '2-3'; freeTids = [1,2];
        tab = { rerender() { rerenders++; } };
    `, context);
    return { context, pane, printArea, copied: () => copied };
}

function nodes(node) {
    if (!node || typeof node !== 'object') return [];
    return [node, ...(node.children || []).flatMap(nodes)];
}

const text = node => typeof node === 'object' && node ? (node.children || []).map(text).join(' ') : String(node);

test('학급 시간표: 교체·잠금·학사일정과 이름 연결, 글 복사', async () => {
    const { context, pane, printArea, copied } = setup({
        cls: '2-3', monday: '2026-11-02',
        days: [{ date: '2026-11-02', name: '월', note: { level: 'maybe', text: '행사' } }],
        week: { '2026-11-02': [{ period: 1, subj: '국어', tid: 1, teacher: '김교사', moved: true, locked: true }] },
    });
    await vm.runInContext('renderClassWeek(pane, tab, () => true)', context);
    assert.match(text(pane), /국어.*김교사.*교체.*잠금/);
    assert.match(text(pane), /확인 필요/);
    const link = nodes(pane).find(n => n.spec === 'button.tt-teacher-link');
    link.attrs.onclick();
    assert.equal(vm.runInContext("viewMode === 'teacher' && viewTid === 1 && teacherReadOnly", context), true);
    await nodes(pane).find(n => text(n) === '글로 복사').attrs.onclick();
    assert.match(copied(), /월 1 국어\(김교사\)\[교체\]\[잠금\]/);
    nodes(pane).find(n => text(n) === '인쇄').attrs.onclick();
    assert.equal(context.printed, true);
    assert.match(text(printArea), /2-3 학급 시간표/);
    assert.doesNotMatch(text(printArea), /글로 복사/);
});

test('공동 빈 시간: 휴업·확인 필요·수업 있는 사람·지난 교시·복사 제외', async () => {
    const slot = (date, free, busy, note = null) => ({ date, day: '화', period: 1, free, busy, note });
    const { context, pane, copied } = setup({
        monday: '2026-11-02',
        slots: [
            slot('2026-11-02', [1, 2], []),
            slot('2026-11-03', [1], [2]),
            slot('2026-11-04', [1, 2], [], { level: 'off', text: '휴업일' }),
            slot('2026-11-05', [1, 2], [], { level: 'maybe', text: '행사' }),
            slot('2026-11-06', [1, 2], []),
        ],
    });
    await vm.runInContext('renderCommonFree(pane, tab, () => true)', context);
    const cells = nodes(pane).filter(n => n.spec === 'td');
    assert.ok(cells.some(n => /tt-past/.test(n.attrs.className) && /1\/2 빔/.test(text(n))));
    assert.ok(cells.some(n => /수업 있음: 이교사/.test(n.attrs.title)));
    assert.match(text(pane), /수업 없는 날/);
    assert.match(text(pane), /확인 필요/);
    await nodes(pane).find(n => n.spec?.startsWith('button') && text(n) === '모두 비는 시간 복사').attrs.onclick();
    assert.doesNotMatch(copied(), /2026-11-0[234]/);
    assert.match(copied(), /2026-11-05 화 1교시 09:00~09:50 \(확인 필요: 행사\)/);
    assert.match(copied(), /2026-11-06/);
});

test('공용 메뉴를 옮긴 뒤 도착한 응답은 화면에 쓰지 않음', async () => {
    const { context, pane } = setup({});
    await vm.runInContext('renderClassWeek(pane, tab, () => false)', context);
    assert.equal(pane.children, undefined);
    await vm.runInContext('renderCommonFree(pane, tab, () => false)', context);
    assert.equal(pane.children, undefined);
});
