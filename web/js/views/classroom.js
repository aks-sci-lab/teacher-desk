// 🏫 학급 경영: 좌석 배치도 · 역할(청소 구역·1인 1역·주번/일일 당번) · 모둠 (0.5.80)
// 자료는 이 PC 의 desk.db 에만 (검색·AI·텔레그램에 쓰지 않음). 학생은 학생 명단의 번호로 연결.
import { api } from '../api.js';
import { el, clear, guarded, toast, longDate, todayIso } from '../ui.js';
import { registerView, openTab, refreshSide, findTab, on, showView } from '../shell.js';
import { store } from '../store.js';
import { renderVotes, renderVoteSide } from './classvote.js';

const SECTIONS = [
    { id: 'seats', label: '좌석' },
    { id: 'roles', label: '역할' },
    { id: 'groups', label: '모둠' },
    { id: 'votes', label: '투표' },
];
const ROLE_TABS = [
    { id: 'clean', label: '청소 구역' },
    { id: 'role', label: '1인 1역' },
    { id: 'duty', label: '주번·당번' },
];
const KEY = 'desk.class.section';
let section = (() => { try { return localStorage.getItem(KEY) || 'seats'; } catch { return 'seats'; } })();
let roleTab = 'clean';
if (!SECTIONS.some(s => s.id === section)) section = 'seats';

// ---------------------------------------------------------------- 공통 도우미
let names = new Map();
const setStudents = list => { names = new Map((list || []).map(s => [s.number, s.name])); };
const who = n => `${n} ${names.get(n) || '(명단에 없음)'}`;
const classTitle = () => {
    const s = store.settings || {};
    return [s.grade && `${s.grade}학년`, s.class_no && `${s.class_no}반`].filter(Boolean).join(' ');
};

// '3, 7, 12~15' → [3, 7, 12, 13, 14, 15]
export function parseNums(text) {
    const out = [];
    for (const part of String(text || '').split(/[,\s]+/)) {
        const m = part.match(/^(\d+)(?:~(\d+))?$/);
        if (!m) continue;
        const a = +m[1], b = m[2] ? +m[2] : a;
        for (let n = Math.min(a, b); n <= Math.max(a, b) && n - Math.min(a, b) < 100; n++) if (!out.includes(n)) out.push(n);
    }
    return out;
}
// '1-2, 5-9' → [[1, 2], [5, 9]]
export function parsePairs(text) {
    const out = [];
    for (const part of String(text || '').split(/[,;\n]+/)) {
        const m = part.trim().match(/^(\d+)\s*[-/&]\s*(\d+)$/);
        if (m && m[1] !== m[2]) out.push([+m[1], +m[2]]);
    }
    return out;
}
const numsText = list => (list || []).join(', ');
const pairsText = list => (list || []).map(p => p.join('-')).join(', ');
const namesHint = list => (list || []).filter(n => names.has(n)).map(who).join(' · ');

function chip(n, opts = {}) {
    return el('span', {
        className: `cl-chip${opts.cls ? ' ' + opts.cls : ''}`, draggable: !!opts.drag, title: opts.title || '',
        ondragstart: opts.drag ? e => { e.dataTransfer.setData('text/plain', JSON.stringify(opts.drag)); e.dataTransfer.effectAllowed = 'move'; } : null,
    }, el('b', n), names.get(n) || '?');
}

// 끌어 놓을 자리: 받은 자료를 onDrop(data) 로
function dropTarget(node, onDrop) {
    node.addEventListener('dragover', e => { e.preventDefault(); node.classList.add('drop-on'); });
    node.addEventListener('dragleave', () => node.classList.remove('drop-on'));
    node.addEventListener('drop', e => {
        e.preventDefault();
        node.classList.remove('drop-on');
        try { onDrop(JSON.parse(e.dataTransfer.getData('text/plain'))); } catch { /* 다른 곳에서 끌어 온 글은 무시 */ }
    });
    return node;
}

// 인쇄: #print-area 에 넣고 인쇄 창 (가로 A4 는 그때만)
function printNode(node, landscape = false) {
    const area = document.getElementById('print-area');
    clear(area, landscape ? el('style', '@page { size: A4 landscape; margin: 10mm; }') : null, el('div.cl-print', node));
    window.print();
}

// 접었다 펴는 설정 묶음: 고친 뒤 화면을 다시 그려도 열림 상태를 그대로
const opened = new Map();
function foldBox(key, openByDefault, ...children) {
    const d = el('details.cl-settings', { open: opened.has(key) ? opened.get(key) : openByDefault }, ...children);
    d.addEventListener('toggle', () => opened.set(key, d.open));
    return d;
}

const noRoster = () => el('div.card.cl-empty',
    el('p', '학생 명단이 비어 있습니다. 학급 기능은 출결 메뉴의 학생 명단(번호·이름)을 그대로 씁니다.'),
    el('button.btn.primary', { onclick: () => showView('attendance') }, '출결 메뉴로 가기'));

// ---------------------------------------------------------------- 좌석 배치도
let seatState = null;     // 서버의 지금 배치
let undo = [];            // 되돌리기 (이 화면을 연 동안만)
let picked = null;        // 눌러서 바꾸기: 처음 누른 칸
let offEdit = false;      // 책상 없는 칸 고르기
let lastProblems = [];

const cloneSeats = s => JSON.parse(JSON.stringify(s));

async function saveSeats(next, opts = {}) {
    if (!opts.noUndo) undo.push(cloneSeats(seatState));
    if (undo.length > 50) undo.shift();
    const r = await api.put('/api/class/seats', { seats: next, record: opts.record || null });
    seatState = r.seats;
    seatHistory = r.history;
    setStudents(r.students);
    findTab('classroom')?.rerender();
    if (opts.record) refreshSide();
}
let seatHistory = [];

// 보이는 순서: 학생 쪽 보기는 그대로, 교사 쪽 보기는 180도 돌림
function viewOrder(s) {
    const idx = [...Array(s.rows * s.cols).keys()];
    return s.view === 'teacher' ? idx.reverse() : idx;
}

function seatCell(s, i) {
    const n = s.cells[i];
    const off = s.off.includes(i);
    const locked = s.locked.includes(i);
    const cell = el('div', {
        className: `cl-seat${off ? ' off' : ''}${n == null ? ' empty' : ''}${locked ? ' locked' : ''}${picked === i ? ' picked' : ''}`,
        dataset: { i },
        title: off ? '책상 없음' : n == null ? '빈 자리 (학생을 끌어 놓으세요)' : '끌어서 다른 자리와 바꾸기 · 눌러서 고른 뒤 다른 자리를 누르면 바꿈',
        draggable: n != null && !offEdit,
        ondragstart: e => { e.dataTransfer.setData('text/plain', JSON.stringify({ seat: i })); e.dataTransfer.effectAllowed = 'move'; },
        onclick: guarded(async e => {
            if (e.target.closest('.lock')) return;
            if (offEdit) {
                const offs = off ? s.off.filter(x => x !== i) : [...s.off, i];
                const cells = s.cells.slice();
                if (!off) cells[i] = null;
                return saveSeats({ ...s, off: offs, cells, locked: s.locked.filter(x => x !== i) });
            }
            if (off) return;
            if (picked == null) {
                if (n == null) return;
                picked = i;
                findTab('classroom')?.rerender();
                return;
            }
            const from = picked;
            picked = null;
            if (from === i) { findTab('classroom')?.rerender(); return; }
            await moveSeat(from, i);
        }),
    });
    if (off) {
        cell.append(el('span.muted.small', offEdit ? '책상 없음' : ''));
        return cell;
    }
    if (n == null) cell.append(el('span.muted.small', offEdit ? '' : '빈 자리'));
    else cell.append(el('b.no', n), el('span.nm', names.get(n) || '(명단에 없음)'),
        el('button.lock', {
            type: 'button', title: locked ? '자리 고정 풀기' : '자리 고정 (무작위 배치 때 그대로)', 'aria-pressed': locked,
            onclick: guarded(() => saveSeats({ ...s, locked: locked ? s.locked.filter(x => x !== i) : [...s.locked, i] })),
        }, locked ? '🔒' : '🔓'));
    return dropTarget(cell, guarded(async d => {
        if (off) return;
        if (d.seat != null) await moveSeat(d.seat, i);
        else if (d.student != null) await placeStudent(d.student, i);
    }));
}

async function moveSeat(from, to) {
    const s = seatState;
    if (s.off.includes(to)) return;
    const cells = s.cells.slice();
    [cells[from], cells[to]] = [cells[to], cells[from]];
    // 고정 표시는 학생을 따라감
    const locked = s.locked.map(x => (x === from ? to : x === to ? from : x)).filter(x => cells[x] != null);
    await saveSeats({ ...s, cells, locked });
}

async function placeStudent(n, to) {
    const s = seatState;
    const cells = s.cells.map(x => (x === n ? null : x));
    cells[to] = n;
    await saveSeats({ ...s, cells, locked: s.locked.filter(x => x !== to) });
}

async function unseat(n) {
    const s = seatState;
    const i = s.cells.indexOf(n);
    if (i < 0) return;
    const cells = s.cells.slice();
    cells[i] = null;
    await saveSeats({ ...s, cells, locked: s.locked.filter(x => x !== i) });
}

function boardBar(s, print = false) {
    return el('div.cl-board', s.view === 'teacher' ? (print ? '칠판 · 교탁 (선생님 자리)' : '칠판 · 교탁 — 선생님이 서서 바라보는 방향') : '칠판');
}

function seatGrid(s, print = false) {
    const grid = el('div.cl-grid', { style: `--cols: ${s.cols}` });
    const order = viewOrder(s);
    order.forEach((i, k) => {
        let node;
        if (print) {
            const n = s.cells[i];
            node = el('div', { className: `cl-seat${s.off.includes(i) ? ' off' : ''}${n == null ? ' empty' : ''}` },
                n != null && el('b.no', n), n != null && el('span.nm', names.get(n) || ''));
        } else node = seatCell(s, i);
        // 분단 사이 통로: 오른쪽 칸이 다른 분단이면 간격 (보기 방향을 바꿔도 같은 짝끼리)
        const next = order[k + 1];
        if (s.pair && (k + 1) % s.cols && (i % s.cols) >> 1 !== (next % s.cols) >> 1) node.classList.add('aisle');
        grid.append(node);
    });
    const board = boardBar(s, print);
    return s.view === 'teacher' ? el('div.cl-room', grid, board) : el('div.cl-room', board, grid);
}

function renderSeats(pane) {
    const body = el('div', el('p.muted', '불러오는 중…'));
    pane.append(body);
    guarded(async () => {
        if (!seatState) {
            const r = await api.get('/api/class/seats');
            seatState = r.seats;
            seatHistory = r.history;
            setStudents(r.students);
        }
        const s = seatState;
        clear(body);
        if (!names.size) { body.append(noRoster()); return; }
        const seated = new Set(s.cells.filter(n => n != null));
        const unseated = [...names.keys()].filter(n => !seated.has(n));
        const freeSeats = s.rows * s.cols - s.off.length;
        const setView = v => guarded(() => saveSeats({ ...s, view: v }, { noUndo: true }));
        const shuffle = guarded(async () => {
            const r = await api.post('/api/class/seats/shuffle', { seats: s });
            lastProblems = r.problems;
            await saveSeats({ ...s, cells: r.cells }, { record: '무작위 배치 전' });
            if (r.unplaced.length) toast(`자리가 모자라 ${r.unplaced.length}명은 자리 없는 학생에 있습니다`, 'error', 6000);
            else toast(r.problems.length ? '무작위로 앉혔습니다. 맞추지 못한 조건은 아래에 있습니다' : '무작위로 앉혔습니다 (조건을 모두 맞춤)');
        });
        clear(body,
            el('div.cl-head',
                el('h1', '좌석 배치도'),
                el('span.muted', `${classTitle()} · 학생 ${names.size}명 · 자리 ${freeSeats}개`),
                el('span.spacer'),
                el('span.seg', { role: 'group', 'aria-label': '보는 방향' },
                    el('button', { className: s.view !== 'teacher' ? 'on' : '', onclick: setView('student'), title: '학생이 칠판을 바라보는 방향 (칠판이 위)' }, '학생 쪽에서'),
                    el('button', { className: s.view === 'teacher' ? 'on' : '', onclick: setView('teacher'), title: '선생님이 교탁에서 바라보는 방향 (칠판이 아래)' }, '교사 쪽에서'))),
            el('div.row.cl-tools',
                el('button.btn.primary', { onclick: shuffle, title: '🔒 고정한 자리는 그대로 두고 나머지를 무작위로' }, '🎲 무작위 배치'),
                el('button.btn', { disabled: !undo.length, title: '방금 바꾼 것 되돌리기 (Ctrl+Z)', onclick: guarded(() => saveSeats(undo.pop(), { noUndo: true })) }, '↶ 되돌리기'),
                el('button.btn', { onclick: guarded(async () => {
                    const r = await api.post('/api/class/seats/record', { label: '직접 남긴 기록' });
                    seatHistory = r.history;
                    refreshSide();
                    toast('지금 배치를 기록에 남겼습니다 (왼쪽 목록)');
                }) }, '기록 남기기'),
                el('button.btn', { className: `btn${offEdit ? ' ok' : ''}`, title: '눌러서 책상이 없는 칸(통로·빈 곳)을 끄고 켬', onclick: () => { offEdit = !offEdit; picked = null; findTab('classroom')?.rerender(); } },
                    offEdit ? '✔ 책상 없는 칸 고르기 끝' : '책상 없는 칸 고르기'),
                el('span.spacer'),
                el('button.btn', { onclick: () => printSeats('student') }, '인쇄 (학생용)'),
                el('button.btn', { onclick: () => printSeats('teacher') }, '인쇄 (교사용)')),
            offEdit && el('p.cl-note', '책상이 없는 칸을 누르면 꺼지고, 다시 누르면 켜집니다. 다 고르면 위의 단추를 누르세요.'),
            lastProblems.length > 0 && el('div.cl-warn', el('b', '맞추지 못한 조건: '), lastProblems.map(([k, nums]) =>
                el('span', k === 'front' ? `앞자리 ${who(nums[0])}` : k === 'apart' ? `떼어 앉히기 ${nums.map(who).join('·')}` : `지난 짝 ${nums.map(who).join('·')}`)),
                el('button.btn.small', { onclick: () => { lastProblems = []; findTab('classroom')?.rerender(); } }, '닫기')),
            seatGrid(s),
            dropTarget(el('div.cl-unseated',
                el('span.muted.small', unseated.length ? '자리 없는 학생 (끌어서 자리에 놓기) · 자리의 학생을 여기로 끌면 자리에서 뺌' : '모든 학생이 자리에 있습니다 · 자리의 학생을 여기로 끌면 자리에서 뺌'),
                unseated.map(n => chip(n, { drag: { student: n } }))), guarded(async d => { if (d.seat != null) await unseat(s.cells[d.seat]); })),
            seatSettings(s));
    })();
}

function seatSettings(s) {
    const num = (value, min, max, onchange, label) => el('input', { type: 'number', min, max, value, 'aria-label': label, className: 'cl-num', onchange });
    const save = change => guarded(() => saveSeats({ ...s, ...change }));
    const resize = (rows, cols) => {
        rows = Math.max(1, Math.min(12, rows || s.rows));
        cols = Math.max(1, Math.min(12, cols || s.cols));
        // 줄·칸을 바꿔도 앉은 위치(줄, 칸)를 되도록 그대로
        const cells = Array(rows * cols).fill(null);
        const off = [], locked = [];
        for (let i = 0; i < s.cells.length; i++) {
            const r = Math.floor(i / s.cols), c = i % s.cols;
            if (r < rows && c < cols) {
                const j = r * cols + c;
                cells[j] = s.cells[i];
                if (s.off.includes(i)) off.push(j);
                if (s.locked.includes(i)) locked.push(j);
            }
        }
        const lost = s.cells.filter(n => n != null).length - cells.filter(n => n != null).length;
        if (lost > 0) toast(`줄·칸이 줄어 ${lost}명이 자리 없는 학생으로 옮겨졌습니다`);
        return save({ rows, cols, cells, off, locked, front_rows: Math.min(s.front_rows, rows) })();
    };
    const listInput = (value, placeholder, onchange) => el('input', { type: 'text', value, placeholder, onchange, className: 'cl-list' });
    return foldBox('seat-set', !s.cells.some(n => n != null),
        el('summary', '교실 크기 · 무작위 배치 조건'),
        el('div.cl-set-grid',
            el('label.field', el('span', '줄 수 (앞뒤)'), num(s.rows, 1, 12, e => resize(+e.target.value, s.cols), '줄 수')),
            el('label.field', el('span', '칸 수 (옆으로)'), num(s.cols, 1, 12, e => resize(s.rows, +e.target.value), '칸 수')),
            el('label.check', el('input', { type: 'checkbox', checked: s.pair, onchange: e => save({ pair: e.target.checked })() }),
                '두 칸씩 붙여 짝으로 앉기 (분단)', el('span.hint', '끄면 바로 옆 사람이 짝입니다')),
            el('label.field', el('span', '앞자리에 앉힐 학생 번호'),
                listInput(numsText(s.front), '예: 3, 7, 12', e => save({ front: parseNums(e.target.value) })()),
                el('span.hint', namesHint(s.front) || '시력·키 등으로 앞에 앉아야 하는 학생')),
            el('label.field', el('span', '앞자리 = 앞에서 몇 줄 안'), num(s.front_rows, 1, s.rows, e => save({ front_rows: +e.target.value })(), '앞자리 줄 수')),
            el('label.field', el('span', '떼어 앉힐 학생 (두 명씩)'),
                listInput(pairsText(s.apart), '예: 1-2, 5-9', e => save({ apart: parsePairs(e.target.value) })()),
                el('span.hint', s.apart.length ? s.apart.map(p => p.map(who).join(' ↔ ')).join(' · ') : '앞뒤·옆·대각선으로 붙지 않게')),
            el('label.check', el('input', { type: 'checkbox', checked: s.avoid_mates, onchange: e => save({ avoid_mates: e.target.checked })() }),
                '지난 배치의 짝과 다시 짝이 되지 않게')),
        el('p.small.muted', '🔒 를 누른 자리는 무작위 배치 때 그대로 남습니다. 무작위 배치를 하면 그 전 배치가 왼쪽 기록에 저장됩니다(최근 30개).'));
}

function printSeats(view) {
    const s = { ...seatState, view };
    const t = classTitle();
    printNode(el('div.cl-print-seats',
        el('h1', `${t ? t + ' ' : ''}좌석 배치도`, el('small', view === 'teacher' ? ' (교사용 — 교탁에서 보는 방향)' : ' (학생용 — 칠판을 보는 방향)')),
        seatGrid(s, true),
        el('p.cl-print-foot', `${longDate(todayIso())}${store.settings?.teacher_name ? ` · 담임 ${store.settings.teacher_name}` : ''}`)), true);
}

function renderSeatSide(body) {
    body.append(el('p.small.muted.cl-side-p', '기록 (무작위 배치·불러오기 전의 배치, 최근 30개)'));
    if (!seatHistory.length) { body.append(el('div.empty', '아직 기록이 없습니다')); return; }
    body.append(el('ul.list', seatHistory.map(h => el('li', { title: '누르면 이 배치를 불러옵니다 (지금 배치는 기록에 남김)',
        onclick: guarded(async () => {
            const r = await api.get(`/api/class/seats/history/${h.id}`);
            if (!confirm(`${h.created_at.slice(0, 16)} 기록을 불러올까요? 지금 배치는 기록에 남습니다.`)) return;
            await saveSeats({ ...r.data, view: seatState?.view || r.data.view }, { record: '불러오기 전' });
            toast('기록을 불러왔습니다');
        }) },
        el('span.ttl', h.label || '기록'), el('span.meta', h.created_at.slice(0, 16),
            el('button.icon-btn', { title: '이 기록 지우기', onclick: guarded(async e => {
                e.stopPropagation();
                if (!confirm('이 기록을 지울까요?')) return;
                await api.del(`/api/class/seats/history/${h.id}`);
                seatHistory = seatHistory.filter(x => x.id !== h.id);
                refreshSide();
            }) }, '✕'))))));
}

// ---------------------------------------------------------------- 역할: 청소 구역·1인 1역 (돌리기)
const rotCache = {};

async function loadRot(kind, force = false) {
    if (!rotCache[kind] || force) {
        const r = await api.get(`/api/class/rot/${kind}`);
        rotCache[kind] = r;
        setStudents(r.students);
    }
    return rotCache[kind];
}

function rotTable(r, editable) {
    const assign = r.current?.assign || {};
    const used = new Set(Object.values(assign).flat());
    const rest = [...names.keys()].filter(n => !used.has(n) && !r.config.exclude.includes(n));
    const move = guarded(async (d, to) => {
        if (!editable || d.student == null) return;
        const next = {};
        for (const it of r.config.items) next[it.name] = (assign[it.name] || []).filter(n => n !== d.student);
        if (to) next[to] = [...next[to], d.student];
        rotCache[r.kind] = await api.put(`/api/class/rot/${r.kind}/assign`, { assign: next });
        findTab('classroom')?.rerender();
    });
    const rows = r.config.items.map(it => {
        const nums = assign[it.name] || [];
        const short = nums.length < it.count;
        return dropTarget(el('tr', el('th', it.name), el('td.num', { className: `num${short ? ' short' : ''}` }, `${nums.length}/${it.count}`),
            el('td', nums.length ? nums.map(n => chip(n, { drag: editable && { student: n } })) : el('span.muted.small', '비어 있음'))), d => move(d, it.name));
    });
    return el('table.cl-table',
        el('thead', el('tr', el('th', r.kind === 'clean' ? '구역' : '역할'), el('th', '인원'), el('th', '학생'))),
        el('tbody', rows,
            editable && dropTarget(el('tr.rest', el('th', '맡지 않음'), el('td.num', rest.length),
                el('td', rest.length ? rest.map(n => chip(n, { drag: { student: n } })) : el('span.muted.small', '없음'))), d => move(d, null))));
}

function itemsText(items) {
    return items.map(it => `${it.name} ${it.count}`).join('\n');
}
function parseItems(text) {
    return text.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
        const m = l.match(/^(.*?)\s+(\d+)\s*(?:명)?$/);
        return m ? { name: m[1].trim(), count: +m[2] } : { name: l, count: 1 };
    });
}

function renderRot(body, kind) {
    guarded(async () => {
        const r = await loadRot(kind);
        clear(body);
        if (!names.size) { body.append(noRoster()); return; }
        const cfg = r.config;
        const word = kind === 'clean' ? '구역' : '역할';
        const saveCfg = guarded(async change => {
            rotCache[kind] = await api.put(`/api/class/rot/${kind}/config`, { ...cfg, ...change });
            toast('저장했습니다');
            findTab('classroom')?.rerender();
        });
        const box = el('textarea', { rows: Math.max(5, cfg.items.length + 1), value: itemsText(cfg.items), placeholder: kind === 'clean' ? '교실 바닥 4\n칠판 2\n복도 3' : '우유 당번 2\n칠판 지우기 1\n불 끄기 1' });
        clear(body,
            el('div.cl-head',
                el('h1', kind === 'clean' ? '청소 구역' : '1인 1역'),
                el('span.muted', `${cfg.period === 'month' ? '매월' : '매주'} 돌림 · 이번 기간 ${r.label}`),
                el('span.spacer'),
                el('button.btn', { disabled: !cfg.items.length, title: `이번 기간을 다시 무작위로 (같은 ${word} 연속은 피함)`, onclick: guarded(async () => {
                    if (r.current && !confirm(`이번 기간(${r.label}) 배정을 새로 섞을까요?`)) return;
                    rotCache[kind] = await api.post(`/api/class/rot/${kind}/shuffle`, {});
                    findTab('classroom')?.rerender();
                }) }, '🎲 다시 섞기'),
                el('button.btn', { disabled: !r.current, onclick: () => printNode(el('div',
                    el('h1', `${classTitle()} ${kind === 'clean' ? '청소 구역' : '1인 1역'}`, el('small', ` ${r.label}`)), rotTable(r, false))) }, '인쇄')),
            !cfg.items.length
                ? el('div.card.cl-empty', el('p', `아래 '${word} 목록'에 한 줄에 하나씩 넣으면 학생에게 자동으로 나눕니다.`))
                : rotTable(r, true),
            cfg.items.length > 0 && el('p.small.muted', `학생을 끌어서 다른 ${word}·'맡지 않음'으로 옮길 수 있습니다. 새 ${cfg.period === 'month' ? '달' : '주'}이 되면 자동으로 돌아갑니다 — 맡은 횟수가 고르게, 바로 전과 같은 ${word}은 피합니다.`),
            foldBox(`rot-set-${kind}`, !cfg.items.length,
                el('summary', `${word} 목록 · 돌리는 주기`),
                el('div.cl-set-grid',
                    el('label.field', el('span', `${word} 목록 (한 줄에 '이름 인원')`), box),
                    el('div',
                        el('label.field', el('span', '돌리는 주기'),
                            el('select', { onchange: e => saveCfg({ period: e.target.value }) },
                                el('option', { value: 'week', selected: cfg.period === 'week' }, '매주 (월요일마다)'),
                                el('option', { value: 'month', selected: cfg.period === 'month' }, '매월 (1일마다)'))),
                        el('label.field', el('span', '빠지는 학생 번호'),
                            el('input.cl-list', { type: 'text', value: numsText(cfg.exclude), placeholder: '예: 5, 18', onchange: e => saveCfg({ exclude: parseNums(e.target.value) }) }),
                            el('span.hint', namesHint(cfg.exclude) || '장기 결석·다른 역할이 있는 학생')),
                        el('button.btn.primary', { onclick: () => saveCfg({ items: parseItems(box.value) }) }, `${word} 목록 저장`)))),
            r.past.length > 0 && foldBox(`rot-past-${kind}`, false,
                el('summary', `지난 기록 (${r.past.length})`),
                r.past.map(p => el('div.cl-past', el('b', p.label), Object.entries(p.assign).map(([k, v]) =>
                    el('span', `${k}: ${v.map(n => names.get(n) || n).join(', ') || '-'}`))))));
    })();
}

// ---------------------------------------------------------------- 역할: 주번·일일 당번 (번호 순서)
function renderDuty(body) {
    guarded(async () => {
        const r = await api.get('/api/class/duty');
        setStudents(r.students);
        clear(body);
        if (!names.size) { body.append(noRoster()); return; }
        const cfg = r.config;
        const save = guarded(async (mode, change) => {
            const next = { ...cfg, [mode]: mode === 'exclude' ? change : { ...cfg[mode], ...change } };
            await api.put('/api/class/duty', next);
            findTab('classroom')?.rerender();
        });
        const line = nums => nums.length ? nums.map(n => chip(n)) : el('span.muted.small', '-');
        const modeBox = (mode, title) => {
            const c = cfg[mode];
            return el('div.card.cl-duty-set',
                el('label.check', el('input', { type: 'checkbox', checked: c.on, onchange: e => save(mode, { on: e.target.checked }) }), el('b', title)),
                el('div.cl-set-grid',
                    el('label.field', el('span', mode === 'day' ? '하루 인원' : '한 주 인원'),
                        el('input.cl-num', { type: 'number', min: 1, max: 10, value: c.per, onchange: e => save(mode, { per: +e.target.value }) })),
                    el('label.field', el('span', '시작 날짜'), el('input', { type: 'date', value: c.start, onchange: e => save(mode, { start: e.target.value }) })),
                    el('label.field', el('span', '시작 날짜의 첫 번호'),
                        el('input.cl-num', { type: 'number', min: 1, value: c.first ?? '', placeholder: '1', onchange: e => save(mode, { first: e.target.value ? +e.target.value : null }) }))));
        };
        const t = classTitle();
        const dayTable = el('table.cl-table',
            el('thead', el('tr', el('th', '날짜'), el('th', '일일 당번'))),
            el('tbody', r.days.map(d => el('tr', { className: d.date === r.today ? 'today' : '' }, el('th', d.label),
                el('td', d.off ? el('span.muted.small', d.off) : line(d.nums))))));
        const weekTable = el('table.cl-table',
            el('thead', el('tr', el('th', '주'), el('th', '주번'))),
            el('tbody', r.weeks.map((w, k) => el('tr', { className: k === 0 ? 'today' : '' }, el('th', w.label), el('td', line(w.nums))))));
        clear(body,
            el('div.cl-head', el('h1', '주번 · 일일 당번'), el('span.muted', '번호 순서대로 돌아갑니다 (주말·공휴일·학사일정 휴업일은 건너뜀)'),
                el('span.spacer'),
                el('button.btn', { onclick: () => printNode(el('div', el('h1', `${t} 주번 · 일일 당번`),
                    cfg.day.on && dayTable.cloneNode(true), cfg.week.on && weekTable.cloneNode(true))) }, '인쇄')),
            el('div.cl-duty-now',
                cfg.day.on && el('div.card', el('h3', '오늘 당번'), line(r.day_today)),
                cfg.week.on && el('div.card', el('h3', '이번 주 주번'), line(r.weeks[0]?.nums || []))),
            el('div.cl-duty-tables', cfg.day.on && dayTable, cfg.week.on && weekTable),
            !cfg.day.on && !cfg.week.on && el('div.card.cl-empty', el('p', '아래에서 일일 당번이나 주번을 켜세요.')),
            foldBox('duty-set', !cfg.day.on && !cfg.week.on,
                el('summary', '당번 설정'),
                el('div.cl-duty-sets', modeBox('day', '일일 당번'), modeBox('week', '주번')),
                el('label.field', el('span', '당번에서 빠지는 학생 번호'),
                    el('input.cl-list', { type: 'text', value: numsText(cfg.exclude), placeholder: '예: 5, 18', onchange: e => save('exclude', parseNums(e.target.value)) }),
                    el('span.hint', namesHint(cfg.exclude) || '결석이 길거나 빠져야 하는 학생')),
                el('p.small.muted', '순서를 바로잡으려면 시작 날짜를 오늘로, 첫 번호를 오늘 당번 번호로 바꾸면 됩니다.')));
    })();
}

function renderRoles(pane) {
    const body = el('div', el('p.muted', '불러오는 중…'));
    pane.append(el('div.seg.cl-subtabs', ROLE_TABS.map(t => el('button', {
        className: t.id === roleTab ? 'on' : '', onclick: () => { roleTab = t.id; refreshSide(); findTab('classroom')?.rerender(); },
    }, t.label))), body);
    if (roleTab === 'duty') renderDuty(body);
    else renderRot(body, roleTab);
}

// ---------------------------------------------------------------- 모둠
let groupState = null;
let groupProblems = [];

function renderGroups(pane) {
    const body = el('div', el('p.muted', '불러오는 중…'));
    pane.append(body);
    guarded(async () => {
        if (!groupState) {
            groupState = await api.get('/api/class/groups');
            setStudents(groupState.students);
        }
        const g = groupState;
        clear(body);
        if (!names.size) { body.append(noRoster()); return; }
        const cfgOf = () => ({ mode: g.mode, value: g.value, apart: g.apart, avoid_last: g.avoid_last, exclude: g.exclude });
        const saveCfg = guarded(async change => {
            groupState = await api.put('/api/class/groups', { config: { ...cfgOf(), ...change } });
            findTab('classroom')?.rerender();
        });
        const make = guarded(async () => {
            if (g.groups.length && !confirm('새로 나눌까요? 지금 모둠은 기록으로 남아 다음에 덜 겹치게 하는 데 씁니다.')) return;
            const r = await api.post('/api/class/groups/make', cfgOf());
            groupProblems = r.problems || [];
            groupState = r;
            findTab('classroom')?.rerender();
        });
        const move = guarded(async (d, to) => {
            if (d.student == null) return;
            const groups = g.groups.map(x => x.filter(n => n !== d.student));
            if (to != null) groups[to].push(d.student);
            groupState = await api.put('/api/class/groups', { groups });
            findTab('classroom')?.rerender();
        });
        const inGroup = new Set(g.groups.flat());
        const rest = [...names.keys()].filter(n => !inGroup.has(n) && !g.exclude.includes(n));
        const cards = g.groups.map((m, k) => dropTarget(el('div.card.cl-group', el('h3', `${k + 1}모둠`, el('span.muted.small', ` ${m.length}명`)),
            el('div.cl-chips', m.map(n => chip(n, { drag: { student: n } })))), d => move(d, k)));
        const t = classTitle();
        clear(body,
            el('div.cl-head', el('h1', '모둠'),
                el('span.muted', g.at ? `${g.at.slice(0, 16)} 나눔 · 학생을 끌어서 다른 모둠으로 옮길 수 있습니다` : '아직 나누지 않았습니다'),
                el('span.spacer'),
                el('button.btn.primary', { onclick: make }, '🎲 모둠 나누기'),
                el('button.btn', { disabled: !g.groups.length, onclick: () => printNode(el('div', el('h1', `${t} 모둠`),
                    el('div.cl-groups', g.groups.map((m, k) => el('div.cl-group', el('h3', `${k + 1}모둠`), el('div', m.map(who).join(', ')))))), true) }, '인쇄')),
            groupProblems.length > 0 && el('div.cl-warn', el('b', '같은 모둠이 된 떼어 놓을 학생: '), groupProblems.map(p => p.map(who).join('·')).join(', ')),
            g.groups.length ? el('div.cl-groups', cards) : el('div.card.cl-empty', el('p', '아래 조건을 정하고 [모둠 나누기]를 누르세요.')),
            rest.length > 0 && g.groups.length > 0 && dropTarget(el('div.cl-unseated', el('span.muted.small', '모둠에 없는 학생 (끌어서 모둠에 넣기)'),
                rest.map(n => chip(n, { drag: { student: n } }))), d => move(d, null)),
            foldBox('group-set', !g.groups.length,
                el('summary', '나누는 조건'),
                el('div.cl-set-grid',
                    el('label.field', el('span', '나누는 방법'),
                        el('div.row',
                            el('select', { onchange: e => saveCfg({ mode: e.target.value }) },
                                el('option', { value: 'count', selected: g.mode === 'count' }, '모둠 수'),
                                el('option', { value: 'size', selected: g.mode === 'size' }, '모둠 인원')),
                            el('input.cl-num', { type: 'number', min: 1, max: 40, value: g.value, onchange: e => saveCfg({ value: +e.target.value || 1 }) }),
                            el('span.muted', g.mode === 'count' ? '개' : '명씩'))),
                    el('label.field', el('span', '떼어 놓을 학생 (두 명씩)'),
                        el('input.cl-list', { type: 'text', value: pairsText(g.apart), placeholder: '예: 1-2, 5-9', onchange: e => saveCfg({ apart: parsePairs(e.target.value) }) }),
                        el('span.hint', g.apart.length ? g.apart.map(p => p.map(who).join(' ↔ ')).join(' · ') : '같은 모둠이 되지 않게')),
                    el('label.field', el('span', '빠지는 학생 번호'),
                        el('input.cl-list', { type: 'text', value: numsText(g.exclude), placeholder: '예: 5, 18', onchange: e => saveCfg({ exclude: parseNums(e.target.value) }) }),
                        el('span.hint', namesHint(g.exclude) || '')),
                    el('label.check', el('input', { type: 'checkbox', checked: g.avoid_last, onchange: e => saveCfg({ avoid_last: e.target.checked }) }),
                        '지난 모둠(최근 몇 번)과 덜 겹치게', el('span.hint', `기록 ${g.history.length}개`)))));
    })();
}

// ---------------------------------------------------------------- 화면 틀
function render(pane, tab) {
    const selected = SECTIONS.find(s => s.id === section);
    tab.setTitle(`학급 · ${selected.label}`);
    if (section === 'seats') renderSeats(pane);
    else if (section === 'roles') renderRoles(pane);
    else if (section === 'votes') renderVotes(pane);
    else renderGroups(pane);
}

export function openClassroom(sec, sub) {
    if (sec) section = sec;
    if (sub) roleTab = sub;
    try { localStorage.setItem(KEY, section); } catch { /* 저장 못 해도 화면은 그대로 */ }
    openTab({ id: 'classroom', title: '학급', menu: true, wide: true, refresh: true, render });
}

function renderSide(body) {
    body.append(el('div.seg.attendance-sections', SECTIONS.map(s => el('button', {
        className: s.id === section ? 'on' : '', type: 'button',
        onclick: () => { if (s.id !== section) { section = s.id; picked = null; refreshSide(); openClassroom(); } },
    }, s.label))));
    if (section === 'seats') renderSeatSide(body);
    else if (section === 'roles') body.append(el('ul.list', ROLE_TABS.map(t => el('li', {
        className: t.id === roleTab ? 'on' : '', onclick: () => openClassroom('roles', t.id),
    }, el('span.ttl', t.label)))),
    el('p.small.muted.cl-side-p', '청소 구역·1인 1역은 주기마다 자동으로 돌고, 주번·당번은 번호 순서대로 돕니다.'));
    else if (section === 'votes') renderVoteSide(body);
    else body.append(el('p.small.muted.cl-side-p', '모둠 수나 인원을 정하고 나눕니다. 떼어 놓을 학생은 다른 모둠으로, 지난 모둠과는 덜 겹치게 나눕니다.'));
    body.append(el('p.small.muted.cl-side-p', '🔐 학급 자료는 이 PC 에만 저장되고 검색·AI·텔레그램으로 보내지 않습니다.'));
}

registerView({ id: 'classroom', icon: '🏫', label: '학급', renderSide, open: () => openClassroom() });

// 좌석 화면에서 Ctrl+Z = 되돌리기 (글 입력 중에는 브라우저 기본 동작)
window.addEventListener('keydown', e => {
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'z' || section !== 'seats' || !undo.length) return;
    if (e.target.closest?.('input, textarea, select') || !findTab('classroom')?.pane.classList.contains('on')) return;
    e.preventDefault();
    guarded(() => saveSeats(undo.pop(), { noUndo: true }))();
});

// 학생 명단을 바꾸면 다시 불러옴
on('students', () => {
    seatState = null;
    groupState = null;
    for (const k of Object.keys(rotCache)) delete rotCache[k];
    findTab('classroom')?.rerender();
});
