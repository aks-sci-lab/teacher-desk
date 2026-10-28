// 시간표: 학교 시간표 엑셀 → 내 시간표(주마다, 교체 반영) · 내 수업 교체 후보 · 교체 부탁 글 · 교체 기록(달력)
import { api } from '../api.js';
import { icon } from '../icons.js';
import { el, clear, toast, guarded, todayIso, addDays, parseIso, modal } from '../ui.js';
import { registerView, openTab, refreshSide, findTab, emit, on } from '../shell.js';

let info = null;          // GET /api/timetable
let week = todayIso();    // 보고 있는 주 (그 주의 아무 날)
let viewTid = null;       // 보고 있는 선생님 (기본: 나)
let picked = null;        // { key, date, cands }

export async function loadTimetable() {
    info = await api.get('/api/timetable');
    if (info.loaded && (viewTid == null || viewTid >= info.teachers.length)) viewTid = info.me ?? 0;
    return info;
}

const monday = iso => { const d = parseIso(iso); return addDays(iso, -((d.getDay() + 6) % 7)); };
const md = iso => { const d = parseIso(iso); return `${d.getMonth() + 1}/${d.getDate()}`; };
const teacherName = tid => info.teachers[tid]?.name || '';

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
function renderWeek(pane, tab) {
    pane.append(el('p.muted', '불러오는 중…'));
    guarded(async () => {
        if (!info) await loadTimetable();
        clear(pane);
        if (!info.loaded) {
            pane.append(el('h1', '시간표'),
                el('p.sub', '학교 시간표 엑셀을 불러오면 브리핑에 "오늘 내 수업"이 나오고, 내 수업을 바꿀 때 바꿀 수 있는 자리를 찾아 드립니다. 파일은 이 PC 에만 저장됩니다.'),
                importBox(() => tab.rerender()));
            return;
        }
        const tid = viewTid ?? 0;
        const wk = await api.get(`/api/timetable/week?teacher=${tid}&date=${week}`);
        const mon = wk.monday;
        const periods = Math.max(...info.slots.map(s => s.p));
        const slotSet = new Set(info.slots.map(s => s.key));
        const mine = tid === info.me;
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
            rows.push(el('tr', el('th.p', p), info.days.map((d, di) => {
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

        const teacherSel = el('select', { onchange: e => { viewTid = Number(e.target.value); picked = null; tab.rerender(); } },
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
                el('span.spacer'), teacherSel),
            info.me == null && el('div.tt-me',
                '내 이름을 시간표에서 고르세요 (설정의 담임 이름과 같으면 저절로 찾습니다): ',
                el('select', { onchange: guarded(async e => {
                    await api.put('/api/timetable/me', { teacher: Number(e.target.value) });
                    await loadTimetable();
                    viewTid = info.me;
                    emit('timetable-changed');
                    tab.rerender();
                }) }, el('option', { value: '' }, '— 선택 —'), info.teachers.map(t => el('option', { value: t.id }, t.name)))),
            el('div.tt-bar', mine
                ? (picked
                    ? el('span', el('b', `${md(picked.date)} ${info.days[Number(picked.key.split('-')[0])]} ${picked.key.split('-')[1]}교시 ${wk.grid[picked.key]?.cls || ''} ${wk.grid[picked.key]?.subj || ''}`),
                        picked.cands.length
                            ? ` — 바꿀 수 있는 자리 ${new Set(picked.cands.filter(c => !c.block).map(c => c.moves[0].to)).size}곳 (초록 칸), 후보 ${picked.cands.length}개`
                            : ' — 그 주 안에서 바꿀 자리가 없습니다. 다른 주를 보세요.',
                        el('button.btn.small', { onclick: () => { picked = null; tab.rerender(); } }, '선택 해제'))
                    : '바꿀 수업을 누르면 그 주 안에서 바꿀 수 있는 자리가 초록 칸으로 나옵니다.')
                : '다른 선생님 시간표는 보기만 합니다.'),
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
            el('p.small.muted.tt-foot', `${info.file} · ${info.sheet} · 선생님 ${info.teachers.length}명 · 수업 ${info.lessons}개 (잠금 ${info.locked}개) · ${info.at} 불러옴`),
            el('details.tt-more', el('summary', '시간표 파일 바꾸기·지우기'), importBox(() => tab.rerender()),
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

export function openTimetable() {
    openTab({ id: 'timetable', title: '시간표', wide: true, refresh: true, render: renderWeek });
}

// ---------------------------------------------------------------- 목록 패널: 교체 기록
function renderSide(body, actions) {
    actions.append(el('button.icon-btn', { title: '시간표 열기', onclick: openTimetable }, icon('timetable')));
    if (!info || !info.loaded) {
        body.append(el('div.empty', '불러온 시간표가 없습니다.'), el('div', { style: 'padding: 0 12px' }, el('button.btn', { onclick: openTimetable }, '시간표 엑셀 불러오기')));
        return;
    }
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
