// 시간표: 학교 시간표 엑셀 → 내 시간표(주마다, 교체 반영) · 내 수업 교체 후보 · 교체 부탁 글 · 교체 기록(달력)
import { api } from '../api.js';
import { icon } from '../icons.js';
import { el, clear, toast, guarded, todayIso, addDays, parseIso, modal } from '../ui.js';
import { registerView, openTab, refreshSide, findTab, emit, on } from '../shell.js';
import { store } from '../store.js';

// 시간표 담당용 전체 교체 도구 (사용자가 준 파일, GitHub Pages)
const WEB_TOOL = 'https://aks-sci-lab.github.io/teacher-desk/timetable/';
let info = null;          // GET /api/timetable
let week = todayIso();    // 보고 있는 주 (그 주의 아무 날)
let viewTid = null;       // 보고 있는 선생님 (기본: 나)
let picked = null;        // { key, date, cands }
let viewMode = 'teacher';
let viewClass = null;
let freeTids = null;
let teacherReadOnly = false;
let weekRender = 0;

export async function loadTimetable() {
    info = await api.get('/api/timetable');
    if (info.loaded && (viewTid == null || viewTid >= info.teachers.length)) viewTid = info.me ?? 0;
    if (info.loaded) {
        if (!info.classes.includes(viewClass)) viewClass = info.default_class || info.classes[0] || null;
        if (freeTids == null) freeTids = info.me == null ? [] : [info.me];
        freeTids = freeTids.filter(id => info.teachers.some(t => t.id === id));
    }
    return info;
}

const monday = iso => { const d = parseIso(iso); return addDays(iso, -((d.getDay() + 6) % 7)); };
const md = iso => { const d = parseIso(iso); return `${d.getMonth() + 1}/${d.getDate()}`; };
const teacherName = tid => info.teachers.find(t => t.id === tid)?.name || '';

// ---------------------------------------------------------------- 엑셀 불러오기
function importBox(after) {
    const words = el('input', { type: 'text', value: (info?.lock_words || ['자율', '동아리', '창체', '봉사']).join(', ') });
    const file = el('input', { type: 'file', accept: '.xlsx,.xlsm', hidden: true, onchange: guarded(async e => {
        const f = e.target.files[0];
        e.target.value = '';
        if (!f) return;
        const buf = new Uint8Array(await f.arrayBuffer());
        let bin = '';
        for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
        const res = await api.post('/api/timetable', { filename: f.name, data: btoa(bin),
            lock_words: words.value.split(/[,\s]+/).filter(Boolean) });
        info = res;
        viewTid = res.me ?? 0;
        viewClass = res.default_class || res.classes[0] || null;
        freeTids = res.me == null ? [] : [res.me];
        picked = null;
        toast(`시간표를 불러왔습니다: 선생님 ${res.teachers.length}명 · 수업 ${res.lessons}개 (잠금 ${res.locked}개)`);
        emit('timetable-changed');
        after?.();
    }) });
    return el('div.tt-import',
        el('button.btn.primary', { onclick: () => file.click() }, icon('doc'), info?.loaded ? '다른 시간표 엑셀 불러오기' : '시간표 엑셀 불러오기'),
        file,
        el('label.field', el('span', '언제나 잠글 과목 이름 (쉼표로)'), words,
            el('span.hint', '배경색이 있는 칸·가로로 병합된 칸도 잠급니다 (바꿀 수 없음). .xls 는 엑셀에서 .xlsx 로 다시 저장해 주세요.')));
}

// ---------------------------------------------------------------- 교시 시간·수업 시작 알림
function periodsBox(tab, periods) {
    const saved = info.periods || {};
    const base = Object.keys(saved).length ? saved : info.default_periods || {};
    const rows = [];
    for (let p = 1; p <= periods; p++) {
        const t = base[String(p)] || {};
        rows.push({ p, start: el('input', { type: 'time', value: t.start || '' }), end: el('input', { type: 'time', value: t.end || '' }) });
    }
    const on = el('input', { type: 'checkbox', checked: !!info.bell?.enabled });
    const lead = el('select', [[0, '시작할 때'], [5, '5분 전'], [10, '10분 전'], [15, '15분 전']].map(([v, l]) =>
        el('option', { value: v, selected: (info.bell?.lead ?? 5) === v }, l)));
    const linked = store.state?.telegram?.linked;
    return el('section.card.tt-periods',
        el('h3', el('span', '교시 시간 · 수업 시작 알림')),
        !Object.keys(saved).length && el('p.small.muted', '아래는 흔한 예시입니다 (50분 수업·10분 쉬는 시간·4교시 뒤 점심). 학교 일과에 맞게 고치고 저장하세요.'),
        el('div.tt-period-grid', rows.map(r => el('label.tt-period', el('b', `${r.p}교시`), r.start, el('span', '~'), r.end))),
        el('div.row.tt-bell',
            el('label.check', on, '수업 시작 알림을 텔레그램으로'), lead,
            !linked && el('span.small.muted', '(설정에서 텔레그램을 연결해야 받습니다)')),
        el('p.small.muted', '오늘 내 수업(교체 반영)만, 그 학년 행사·휴업 날은 보내지 않습니다. 업무 데스크가 켜져 있어야 보냅니다.'),
        el('div.row', el('button.btn.small.primary', { onclick: guarded(async () => {
            const body = {};
            rows.forEach(r => { if (r.start.value) body[r.p] = { start: r.start.value, end: r.end.value || null }; });
            info = await api.put('/api/timetable/periods', { periods: body, bell: { enabled: on.checked, lead: Number(lead.value) } });
            toast(on.checked ? '교시 시간을 저장하고 수업 알림을 켰습니다' : '교시 시간을 저장했습니다');
            emit('timetable-changed');
            tab.rerender();
        }) }, '저장')));
}

// ---------------------------------------------------------------- 후보 미리보기 팝업
function board(b) {
    return el('div.tt-board',
        el('div.tt-board-head', el('b', b.name), ` ${b.day}요일`,
            el('span', { className: `tt-run${!b.self && b.run_after >= 4 && b.run_after > b.run_before ? ' bad' : ''}` },
                b.run_after === b.run_before ? `최대 연속 ${b.run_after}교시` : `연속 ${b.run_before} → ${b.run_after}교시`),
            b.self && el('span.muted.small', ' (부탁하는 사람)')),
        el('table.tt-mini',
            el('tr', el('th'), b.cells.map(c => el('th', c.p))),
            el('tr.before', el('th', '전'), b.cells.map(c => el('td', { className: c.before ? '' : 'e' }, c.before))),
            el('tr', el('th', '후'), b.cells.map(c => el('td', { className: c.in ? 'in' : c.out ? 'out' : c.locked ? 'lk' : (c.after ? '' : 'e') },
                c.out ? '비움' : c.after)))));
}

function openCandidate(c, tab) {
    const kind = c.kind === 'swap' ? '맞교환' : '2단계 교체';
    const m0 = c.moves[0];
    const box = modal({
        title: `${kind} · ${m0.from_label} → ${m0.to_label}`, wide: true,
        body: el('div.tt-cand-body',
            c.block && el('p.tt-warn', `이 교체는 할 수 없습니다: ${c.block}`),
            c.worse && el('p.tt-warn', `연속 ${c.max_run}교시가 되는 선생님이 있습니다. 괜찮은지 확인하세요.`),
            el('ol.tt-moves', c.moves.map(m => el('li', el('b', m.name), ` · ${m.cls} ${m.subj} `,
                el('span.slot', `${m.from_label} → ${m.to_label}`)))),
            el('div.tt-boards', c.boards.map(board)),
            el('p.small.muted', '주황 칸은 새로 들어오는 수업, 점선 "비움"은 빠지는 수업입니다. 엑셀 시간표는 고치지 않습니다 (시간표 담당 선생님께 알려 주세요).'),
            el('h3', '교체 부탁 글 (쿨메신저에 붙여넣기)'),
            el('pre.tt-request', c.request)),
        actions: [
            el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(c.request); toast('부탁 글을 복사했습니다'); }) }, icon('copy'), '부탁 글 복사'),
            !c.block && el('button.btn.small.primary', { onclick: guarded(async () => {
                await api.post('/api/timetable/swaps', { moves: c.moves });
                toast('교체를 기록했습니다. 그 주 시간표와 달력에 들어갑니다.');
                box.close();
                picked = null;
                await loadTimetable();
                emit('data-changed');
                emit('timetable-changed');
                tab.rerender();
            }) }, icon('check'), '교체 확정 (기록)'),
        ],
    });
}

// ---------------------------------------------------------------- 주간 시간표
function viewSwitch(tab) {
    return el('label.tt-view', '보기 ', el('select', {
        'aria-label': '시간표 보기',
        onchange: e => { viewMode = e.target.value; picked = null; teacherReadOnly = false; tab.rerender(); },
    }, [['teacher', '선생님'], ['class', '학급'], ['free', '같이 비는 시간']].map(([value, name]) =>
        el('option', { value, selected: viewMode === value }, name))));
}

function weekNav(mon, tab) {
    const move = n => () => { week = addDays(mon, n); picked = null; tab.rerender(); };
    return el('div.row',
        el('button.btn.small', { onclick: move(-7), title: '지난주' }, '◀'),
        el('b.tt-week', `${md(mon)} ~ ${md(addDays(mon, 4))}`),
        el('button.btn.small', { onclick: move(7), title: '다음 주' }, '▶'),
        el('button.btn.small', { onclick: () => { week = todayIso(); picked = null; tab.rerender(); } }, '이번 주'));
}

const periodTime = p => {
    const pt = info.periods?.[String(p)];
    return pt?.start ? `${pt.start}${pt.end ? '~' + pt.end : ''}` : '';
};

function pastPeriod(date, p) {
    if (date !== todayIso()) return date < todayIso();
    const end = info.periods?.[String(p)]?.end;
    const now = new Date();
    return !!end && end <= `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
}

function noteLabel(note) {
    return note && el('em', { title: note.text }, note.level === 'off' ? '수업 없는 날' : '확인 필요');
}

async function copyText(text, done) {
    if (!text) return toast('복사할 시간이 없습니다');
    await navigator.clipboard.writeText(text);
    toast(done);
}

function printClass(title, mon, table) {
    const area = document.getElementById('print-area');
    clear(area, el('section.tt-class-print', el('h1', title),
        el('p', `${mon} ~ ${addDays(mon, 4)} · 교체 기록 반영`), table.cloneNode(true)));
    window.print();
}

async function renderClassWeek(pane, tab, current) {
    if (!viewClass) {
        clear(pane, el('h1', '학급 시간표'), viewSwitch(tab), el('p.muted', '시간표 자료에 학급이 없습니다.'));
        return;
    }
    const wk = await api.get(`/api/timetable/class?cls=${encodeURIComponent(viewClass)}&week=${week}`);
    if (!current()) return;
    const mon = wk.monday;
    const periods = Math.max(...info.slots.map(s => s.p));
    const slotSet = new Set(info.slots.map(s => s.key));
    const rows = [];
    for (let p = 1; p <= periods; p++) {
        rows.push(el('tr', el('th.p', p, periodTime(p) && el('small', periodTime(p))),
            wk.days.map((day, di) => {
                if (!slotSet.has(`${di}-${p}`)) return el('td.cell.na');
                const lessons = (wk.week[day.date] || []).filter(l => l.period === p);
                return el('td', { className: `cell${day.note?.level === 'off' ? ' dblk' : ''}` },
                    lessons.map(l => el('div', { className: `tt-class-lesson${l.locked ? ' locked' : ''}${l.moved ? ' moved' : ''}` },
                        el('span.s', l.subj),
                        el('button.tt-teacher-link', { onclick: () => { viewTeacher(l.tid, true); tab.rerender(); } }, l.teacher),
                        l.moved && el('span.mv', '교체'), l.locked && el('span.lk', '잠금'))));
            })));
    }
    const title = `${wk.cls} 학급 시간표`;
    const table = el('table.tt.tt-class-table', el('thead', el('tr', el('th.p', '교시'),
        wk.days.map(day => el('th', { className: day.date === todayIso() ? 'today' : '' },
            day.name, el('small', md(day.date)), noteLabel(day.note))))), el('tbody', rows));
    const text = wk.days.map(day => `${day.name} ${(wk.week[day.date] || []).map(l =>
        `${l.period} ${l.subj}(${l.teacher})${l.moved ? '[교체]' : ''}${l.locked ? '[잠금]' : ''}`).join(' · ')}${day.note ? ` — ${day.note.level === 'off' ? '수업 없는 날' : '확인 필요'}: ${day.note.text}` : ''}`).join('\n');
    clear(pane, el('div.tt-head', el('h1', title), weekNav(mon, tab), viewSwitch(tab),
        el('label.tt-view', '학급 ', el('select', { 'aria-label': '학급', onchange: e => { viewClass = e.target.value; tab.rerender(); } },
            info.classes.map(cls => el('option', { value: cls, selected: cls === wk.cls }, cls))))),
    el('div.row', el('button.btn.small', { onclick: guarded(() => copyText(text, '학급 시간표를 복사했습니다')) }, '글로 복사'),
        el('button.btn.small', { onclick: () => printClass(title, mon, table) }, '인쇄')),
    el('p.small.muted', '선생님 이름을 누르면 그 선생님 시간표를 보기만 합니다.'),
    el('div.tt-wrap', table));
}

async function renderCommonFree(pane, tab, current) {
    const wk = await api.get(`/api/timetable/free?tids=${freeTids.join(',')}&week=${week}`);
    if (!current()) return;
    const mon = wk.monday;
    const slots = new Map(wk.slots.map(s => [`${s.date}-${s.period}`, s]));
    const periods = Math.max(...info.slots.map(s => s.p));
    const days = Array.from({ length: 5 }, (_, i) => ({ date: addDays(mon, i), name: ['월', '화', '수', '목', '금'][i] }));
    const selected = el('div.row.tt-free-teachers', freeTids.map(id =>
        el('button.btn.small.tt-chip', { title: '선택에서 빼기', onclick: () => { freeTids = freeTids.filter(t => t !== id); tab.rerender(); } },
            `${teacherName(id)}${id === info.me ? ' (나)' : ''} ×`)));
    const allFree = wk.slots.filter(s => freeTids.length && !s.busy.length && s.note?.level !== 'off' && !pastPeriod(s.date, s.period));
    const text = allFree.map(s => `${s.date} ${s.day} ${s.period}교시${periodTime(s.period) ? ' ' + periodTime(s.period) : ''}${s.note ? ` (확인 필요: ${s.note.text})` : ''}`).join('\n');
    const rows = [];
    for (let p = 1; p <= periods; p++) {
        rows.push(el('tr', el('th.p', p, periodTime(p) && el('small', periodTime(p))),
            days.map(day => {
                const s = slots.get(`${day.date}-${p}`);
                if (!s) return el('td.cell.na');
                const off = s.note?.level === 'off';
                const label = off ? '수업 없는 날' : !freeTids.length ? '선생님 선택' : !s.busy.length ? '모두 빔' : `${s.free.length}/${freeTids.length} 빔`;
                return el('td', { className: ['cell', off && 'dblk', !off && freeTids.length && !s.busy.length && 'tt-all-free', pastPeriod(s.date, p) && 'tt-past'].filter(Boolean).join(' '),
                    title: [`${day.name} ${p}교시 ${periodTime(p)}`, s.busy.length ? `수업 있음: ${s.busy.map(teacherName).join(', ')}` : '', s.note?.text].filter(Boolean).join('\n') },
                el('span.s', label), s.note?.level === 'maybe' && el('small', '확인 필요'));
            })));
    }
    clear(pane, el('div.tt-head', el('h1', '같이 비는 시간'), weekNav(mon, tab), viewSwitch(tab)),
        teacherSearch(id => { if (!freeTids.includes(id)) freeTids.push(id); tab.rerender(); }, '함께 볼 선생님 추가 (이름·과목·반)'),
        selected,
        !freeTids.length && el('p.muted', '함께 볼 선생님을 골라 주세요.'),
        el('div.row', el('button.btn.small', { disabled: !allFree.length, onclick: guarded(() => copyText(text, '모두 비는 시간을 복사했습니다')) }, '모두 비는 시간 복사')),
        el('p.small.muted', '시간표에 있는 교시만 비교합니다. 점심·방과후는 다루지 않습니다. 학년별 행사나 일부 교시 변경은 확인이 필요합니다.'),
        el('div.tt-wrap', el('table.tt.tt-free-table', el('thead', el('tr', el('th.p', '교시'), days.map(day =>
            el('th', { className: day.date === todayIso() ? 'today' : '' }, day.name, el('small', md(day.date)))))), el('tbody', rows))));
}

function renderWeek(pane, tab) {
    const renderId = ++weekRender;
    const current = () => renderId === weekRender && tab.spec.id === 'timetable' && pane.isConnected;
    pane.append(el('p.muted', '불러오는 중…'));
    guarded(async () => {
        if (!info) await loadTimetable();
        if (!current()) return;
        clear(pane);
        if (!info.loaded) {
            pane.append(el('h1', '시간표'),
                el('p.sub', '학교 시간표 엑셀을 불러오면 브리핑에 "오늘 내 수업"이 나오고, 내 수업을 바꿀 때 바꿀 수 있는 자리를 찾아 드립니다. 파일은 이 PC 에만 저장됩니다.'),
                importBox(() => tab.rerender()));
            return;
        }
        if (viewMode === 'class') return renderClassWeek(pane, tab, current);
        if (viewMode === 'free') return renderCommonFree(pane, tab, current);
        const tid = viewTid ?? 0;
        const wk = await api.get(`/api/timetable/week?teacher=${tid}&date=${week}`);
        if (!current()) return;
        const mon = wk.monday;
        const periods = Math.max(...info.slots.map(s => s.p));
        const slotSet = new Set(info.slots.map(s => s.key));
        const mine = tid === info.me && !teacherReadOnly;
        // 후보가 있는 자리 (초록 칸)
        const dest = new Map();
        if (picked) picked.cands.forEach(c => { const k = c.moves[0].to; if (!dest.has(k)) dest.set(k, []); dest.get(k).push(c); });

        const pick = guarded(async (key, l) => {
            if (picked && picked.key === key) { picked = null; return tab.rerender(); }
            if (l.locked) return toast(`잠긴 수업입니다 (${l.why}). 바꿀 수 없습니다.`);
            const date = addDays(mon, Number(key.split('-')[0]));
            if (date < todayIso()) return toast('지난 날짜의 수업입니다. ▶ 로 다음 주를 보세요.');
            if (l.block) return toast(`그날은 ${l.block} 이라 바꿀 수 없습니다.`);
            const res = await api.get(`/api/timetable/candidates?teacher=${tid}&key=${key}&date=${date}`);
            picked = { key, date, cands: res.candidates };
            tab.rerender();
        });

        const head = el('tr', el('th.p'), info.days.map((d, i) => {
            const day = wk.days[i];
            return el('th', { className: [day.date === todayIso() && 'today', day.past && 'past'].filter(Boolean).join(' ') },
                d, el('small', md(day.date)), day.events.length > 0 && el('em', { title: day.events.join(', ') }, day.events.join(', ')));
        }));
        const rows = [];
        for (let p = 1; p <= periods; p++) {
            const pt = (info.periods || {})[String(p)];
            rows.push(el('tr', el('th.p', p, pt && el('small', pt.start)), info.days.map((d, di) => {
                const key = `${di}-${p}`;
                if (!slotSet.has(key)) return el('td.cell.na');
                const l = wk.grid[key];
                const cand = dest.get(key);
                if (!l && cand) {
                    const ok = cand.filter(c => !c.block);
                    const best = ok.find(c => !c.worse) || ok[0] || cand[0];
                    return el('td', { className: `cell cslot${!ok.length ? ' bad' : best.worse ? ' warn' : ''}`, tabIndex: 0,
                        title: '눌러서 미리보기', onclick: () => openCandidate(best, tab) },
                        el('span.cs-l', best.kind === 'swap' ? '맞교환' : '2단계'),
                        el('span.cs-who', best.moves[1].name + (best.kind === 'two' ? ' 외' : '')),
                        cand.length > 1 && el('span.cs-n', `후보 ${cand.length}`),
                        (!ok.length || best.worse) && el('span.cs-w', !ok.length ? best.block : `연속 ${best.max_run}`));
                }
                if (!l) return el('td.cell', { className: `cell${wk.days[di].block ? ' dblk' : ''}` });
                const cls = ['cell', 'les', l.locked && 'locked', l.moved && 'moved', picked?.key === key && 'sel', l.block && 'dblk'].filter(Boolean).join(' ');
                return el('td', { className: cls, style: l.locked && l.color ? `--lc: ${l.color}` : '',
                    title: l.locked ? `잠금 (${l.why})` : l.block ? `${l.block} — 바꿀 수 없음` : (mine ? '눌러서 바꿀 자리 찾기' : ''),
                    onclick: mine ? () => pick(key, l) : null },
                    el('span.c', l.cls || ''), el('span.s', l.subj), l.moved && el('span.mv', '교체'), l.locked && el('span.lk', '잠금'));
            })));
        }

        // '나' 정하기·바꾸기 (브리핑의 오늘 내 수업·텔레그램·교체 후보가 이 선생님 기준)
        const setMe = guarded(async id => {
            await api.put('/api/timetable/me', { teacher: id });
            await loadTimetable();
            viewTid = info.me;
            if (!freeTids.length) freeTids = [info.me];
            picked = null;
            toast(`'나'를 ${teacherName(info.me)} 선생님으로 정했습니다`);
            emit('timetable-changed');
            tab.rerender();
        });
        const meSel = el('select', { onchange: e => e.target.value !== '' && setMe(Number(e.target.value)) },
            el('option', { value: '', selected: info.me == null }, '— 선택 —'),
            info.teachers.map(t => el('option', { value: t.id, selected: t.id === info.me }, `${t.name}${t.dept ? ' · ' + t.dept : ''}`)));
        const teacherSel = el('select', { title: '볼 선생님', onchange: e => { viewTeacher(Number(e.target.value)); tab.rerender(); } },
            info.teachers.map(t => el('option', { value: t.id, selected: t.id === tid }, `${t.name}${t.dept ? ' · ' + t.dept : ''}${t.id === info.me ? ' (나)' : ''}`)));
        const move = n => () => { week = addDays(mon, n); picked = null; tab.rerender(); };
        // pane.append 는 false·null 을 글자로 넣으므로 빈 것은 거름
        pane.append(...[
            el('div.tt-head',
                el('h1', mine ? '내 시간표' : `${teacherName(tid)} 선생님 시간표`),
                el('div.row',
                    el('button.btn.small', { onclick: move(-7), title: '지난주' }, '◀'),
                    el('b.tt-week', `${md(mon)} ~ ${md(addDays(mon, info.days.length - 1))}`),
                    el('button.btn.small', { onclick: move(7), title: '다음 주' }, '▶'),
                    el('button.btn.small', { onclick: () => { week = todayIso(); picked = null; tab.rerender(); } }, '이번 주')),
                el('span.spacer'),
                teacherSearch(id => { viewTeacher(id); tab.rerender(); }),
                viewSwitch(tab), el('label.tt-view', '선생님 ', teacherSel),
                !mine && !teacherReadOnly && el('button.btn.small.primary', { onclick: () => setMe(tid), title: '브리핑의 오늘 내 수업·텔레그램·교체 후보를 이 선생님 기준으로' }, `이 선생님을 '나'로`)),
            info.me == null && el('div.tt-me',
                '내 이름을 시간표에서 고르세요 (설정의 담임 이름과 같으면 저절로 찾습니다): ', meSel),
            el('div.tt-bar', mine
                ? (picked
                    ? el('span', el('b', `${md(picked.date)} ${info.days[Number(picked.key.split('-')[0])]} ${picked.key.split('-')[1]}교시 ${wk.grid[picked.key]?.cls || ''} ${wk.grid[picked.key]?.subj || ''}`),
                        picked.cands.length
                            ? ` — 바꿀 수 있는 자리 ${new Set(picked.cands.filter(c => !c.block).map(c => c.moves[0].to)).size}곳 (초록 칸), 후보 ${picked.cands.length}개`
                            : ' — 그 주 안에서 바꿀 자리가 없습니다. 다른 주를 보세요.',
                        el('button.btn.small', { onclick: () => { picked = null; tab.rerender(); } }, '선택 해제'))
                    : '바꿀 수업을 누르면 그 주 안에서 바꿀 수 있는 자리가 초록 칸으로 나옵니다.')
                : el('span', `${teacherName(tid)} 선생님 시간표는 보기만 합니다.`, !teacherReadOnly && " 내 시간표라면 위의 [이 선생님을 '나'로] 를 누르세요.")),
            el('div.tt-wrap', el('table.tt', el('thead', head), el('tbody', rows))),
            picked && picked.cands.length > 0 && el('section.tt-cands',
                el('h2', '교체 후보'),
                el('div.tt-cand-list', picked.cands.map(c => el('button', { className: `tt-cand${c.block ? ' bad' : ''}`, onclick: () => openCandidate(c, tab) },
                    el('div.k',
                        el('span', { className: `tag ${c.kind}` }, c.kind === 'swap' ? '맞교환' : '2단계'),
                        el('b', c.moves[0].to_label),
                        c.block ? el('span.tag.warn', `불가: ${c.block}`) : c.worse ? el('span.tag.warn', `연속 ${c.max_run}교시`) : el('span.tag.run', `최대 연속 ${c.max_run}`),
                        el('span.muted.small', `선생님 ${c.teachers}명`)),
                    el('div.mv', c.moves.slice(1).map(m => el('div', `${m.name} · ${m.cls} ${m.subj} ${m.from_label} → ${m.to_label}`))))))),
            mine && periodsBox(tab, periods),
            el('p.small.muted.tt-foot', `${info.file} · ${info.sheet} · 선생님 ${info.teachers.length}명 · 수업 ${info.lessons}개 (잠금 ${info.locked}개) · ${info.at} 불러옴`),
            el('p.small.tt-web', '학교 전체 시간표 교체·바뀐 엑셀 저장·교체 안내문 인쇄는 ',
                el('a', { href: WEB_TOOL, target: '_blank', rel: 'noopener' }, '시간표 교체 도우미 (웹) ↗'),
                ' — 엑셀은 그 브라우저 안에서만 읽고 어디에도 올리지 않습니다.'),
            el('details.tt-more', el('summary', "'나' 바꾸기 · 시간표 파일 바꾸기·지우기"),
                info.me != null && el('label.field.tt-me-field', el('span', "나 (오늘 내 수업·텔레그램·교체 후보 기준)"), meSel),
                importBox(() => tab.rerender()),
                el('button.btn.small.danger', { onclick: guarded(async () => {
                    if (!confirm('불러온 시간표와 교체 기록을 지울까요?')) return;
                    await api.del('/api/timetable');
                    info = null;
                    picked = null;
                    emit('data-changed');
                    emit('timetable-changed');
                    tab.rerender();
                }) }, '시간표 지우기'))].filter(Boolean));
    })();
}

// ---------------------------------------------------------------- 선생님 찾기 (0.5.60)
// 이름·부서·과목·반으로 찾기 (띄어 쓰면 모든 낱말이 들어간 선생님만). 이름이 앞에서 맞으면 위로
const norm = s => String(s || '').toLowerCase().replace(/\s+/g, '');
export function findTeachers(teachers, q, limit = 8) {
    const words = String(q || '').trim().split(/\s+/).map(norm).filter(Boolean);
    if (!words.length) return [];
    const scored = [];
    for (const tch of teachers) {
        const name = norm(tch.name);
        const hay = [tch.name, tch.dept, ...(tch.subjects || []), ...(tch.classes || [])].map(norm).join('|');
        if (!words.every(w => hay.includes(w))) continue;
        const w0 = words[0];
        scored.push([name.startsWith(w0) ? 0 : name.includes(w0) ? 1 : 2, tch]);
    }
    return scored.sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name, 'ko')).slice(0, limit).map(x => x[1]);
}

function teacherDesc(tch) {
    const subj = (tch.subjects || []).filter(x => x !== tch.dept).slice(0, 3).join('·');   // 부서와 같은 과목은 한 번만
    return [tch.dept, subj, tch.classes?.length ? `${tch.classes.slice(0, 4).join(', ')}${tch.classes.length > 4 ? ' …' : ''}` : '']
        .filter(Boolean).join(' · ');
}

// 찾기 칸: 고르면 onPick(선생님 번호). ↑↓ 로 고르고 Enter, Esc 로 지움
function teacherSearch(onPick, placeholder = '선생님 찾기 (이름·과목·반)') {
    const results = el('div.tt-find-list', { hidden: true });
    let found = [];
    let cur = 0;
    const draw = () => {
        results.hidden = !input.value.trim();
        clear(results, found.length
            ? found.map((tch, i) => el('button', { type: 'button', className: `tt-find-item${i === cur ? ' on' : ''}`,
                onmousedown: e => { e.preventDefault(); pick(tch); } },
                el('b', tch.name), tch.id === info.me && el('span.tag', '나'), el('span.muted.small', teacherDesc(tch))))
            : el('div.muted.small', { style: 'padding: 8px 10px' }, '맞는 선생님이 없습니다'));
    };
    const pick = tch => { input.value = ''; found = []; results.hidden = true; onPick(tch.id); };
    const input = el('input', { type: 'search', placeholder, 'aria-label': '선생님 찾기', autocomplete: 'off',
        oninput: () => { found = findTeachers(info.teachers, input.value); cur = 0; draw(); },
        onkeydown: e => {
            if (e.key === 'ArrowDown' && found.length) { cur = (cur + 1) % found.length; draw(); e.preventDefault(); }
            else if (e.key === 'ArrowUp' && found.length) { cur = (cur - 1 + found.length) % found.length; draw(); e.preventDefault(); }
            else if (e.key === 'Enter' && found[cur]) { pick(found[cur]); e.preventDefault(); }
            else if (e.key === 'Escape') { input.value = ''; found = []; draw(); }
        },
        onblur: () => setTimeout(() => { results.hidden = true; }, 150),
        onfocus: () => { if (input.value.trim()) draw(); } });
    return el('div.tt-find', input, results);
}

const viewTeacher = (id, readOnly = false) => { viewTid = id; picked = null; viewMode = 'teacher'; teacherReadOnly = readOnly; };

export function openTimetable() {
    openTab({ id: 'timetable', title: '시간표', menu: true, wide: true, refresh: true, render: renderWeek });
}

// ---------------------------------------------------------------- 목록 패널: 교체 기록
function renderSide(body, actions) {
    actions.append(el('button.icon-btn', { title: '시간표 열기', onclick: openTimetable }, icon('timetable')));
    if (!info || !info.loaded) {
        body.append(el('div.empty', '불러온 시간표가 없습니다.'), el('div', { style: 'padding: 0 12px' }, el('button.btn', { onclick: openTimetable }, '시간표 엑셀 불러오기')));
        return;
    }
    body.append(el('div', { style: 'padding: 8px 12px' }, teacherSearch(id => { viewTeacher(id); openTimetable(); refreshSide(); }, '선생님 찾아 시간표 보기')));
    body.append(el('div.tt-side-views',
        el('button.btn.small', { onclick: () => { viewMode = 'class'; picked = null; openTimetable(); } }, '학급 시간표'),
        el('button.btn.small', { onclick: () => { viewMode = 'free'; picked = null; openTimetable(); } }, '같이 비는 시간')));
    const list = el('ul.list');
    const swaps = [...info.swaps].reverse();
    if (!swaps.length) list.append(el('li.empty', '아직 기록한 교체가 없습니다. 내 시간표에서 바꿀 수업을 누르세요.'));
    for (const sw of swaps) {
        const m0 = sw.moves[0];
        list.append(el('li', { onclick: () => { week = m0.from_date; openTimetable(); } },
            el('span.ttl', `${m0.from_label} → ${m0.to_label}`),
            el('span.meta', `${m0.cls} ${m0.subj}`, `${new Set(sw.moves.map(m => m.name)).size}명`,
                el('button.btn.small', { onclick: guarded(async e => {
                    e.stopPropagation();
                    if (!confirm('이 교체 기록을 지울까요? (달력의 교체 일정도 지웁니다)')) return;
                    await api.del(`/api/timetable/swaps/${sw.id}`);
                    await loadTimetable();
                    emit('data-changed');
                    emit('timetable-changed');
                }) }, '지우기'))));
    }
    body.append(el('div.group-title', '교체 기록'), list);
}

export async function initTimetable() {
    try { await loadTimetable(); } catch (e) { info = { loaded: false }; }
}

on('timetable-changed', () => { refreshSide(); findTab('today')?.rerender(); });

registerView({ id: 'timetable', icon: '▦', label: '시간표', renderSide, open: openTimetable });
