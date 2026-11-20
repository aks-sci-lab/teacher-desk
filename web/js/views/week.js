// 🗓 주간 계획: 월~금 다섯 칸에 일정·마감·꼭 할 일·학사일정, 날짜 없는 꼭 할 일은 '이번 주 안에'
// 학사일정 줄을 누르면 고칠 수 있고, 고친 것은 기록에 남음 (0.6.18)
import { api } from '../api.js';
import { el, clear, guarded, todayIso, addDays, parseIso, dayLabel, ddayInfo, catBadge, CAT_VARS } from '../ui.js';
import { registerView, openTab, findTab, on, openPopup } from '../shell.js';
import { openItem } from './inbox.js';
import { openEventForm } from './calendar.js';

const KEY = 'desk.week.days';
const monday = iso => addDays(iso, -((parseIso(iso).getDay() + 6) % 7));
let start = monday(todayIso());
let days = (() => { try { return localStorage.getItem(KEY) === '14' ? 14 : 7; } catch { return 7; } })();   // 기본 1주, 마지막 선택 기억

const openEv = e => (e.item_id ? openItem(e.item_id) : openEventForm(e));

function dday(iso) {
    const d = ddayInfo(iso);
    return el('span', { className: `dday ${d.cls}` }, d.label);
}

function evRow(e, d) {
    const deadline = e.kind === 'deadline';
    return el('button.wk-ev', {
        className: `wk-ev${e.done ? ' done' : ''}${deadline ? ' deadline' : ''}`,
        style: `--cc: var(${CAT_VARS[e.category] || '--c-ref'})`, title: e.text, onclick: () => openEv(e),
    }, deadline && el('b', '마감 '), e.time && d === e.date && el('span.muted', `${e.time} `),
    e.item_title || e.text, deadline && !e.done && dday(e.end_date || e.date));
}

function todoRow(t) {
    return el('div.wk-todo', el('span.box', '☐'), el('span', (t.time ? `${t.time} ` : '') + t.text),
        t.date && dday(t.date));
}

function schoolRow(e, warn) {
    const label = `${e.time ? e.time + ' ' : ''}${e.text}`;
    return el('button.wk-school', {
        className: `wk-school${warn ? ' warn' : ''}`,
        title: e.school ? '눌러서 학사일정 고치기 (고친 것은 기록에 남음)' : '눌러서 일정 고치기',
        onclick: () => openEventForm(e),
    }, warn && el('span.wk-warn', '⚠ 확인 필요 '), label, e.edited && el('span.wk-edited', { title: '선생님이 고친 학사일정' }, ' ✎고침'));
}

function dayCol(c, off = false) {
    const today = c.date === todayIso();
    const school = c.school_ev || [];
    const check = off ? null : c.check;
    // '확인 필요' 글이 학사일정 줄과 같으면 그 줄에 표시만 (같은 글이 두 번 나오지 않게)
    const checkInLine = check && school.some(e => e.text === check);
    const empty = !c.events.length && !c.todos.length && !school.length;
    return el('section', { className: `wk-col${today ? ' today' : ''}${off ? ' off' : ''}`, dataset: { date: c.date }, title: '두 번 누르면 이 날짜에 일정 추가',
        ondblclick: e => { if (!e.target.closest('button')) openEventForm({ date: c.date }); } },
        el('h3', dayLabel(c.date, !today), today && el('span.badge', '오늘'), off && parseIso(c.date).getDay() % 6 !== 0 && el('span.badge.off', '수업 없음')),
        check && !checkInLine && el('div.wk-check', `⚠ 확인 필요: ${check}`),
        off && c.reason && !school.some(e => e.text === c.reason) && el('div.wk-check.muted', c.reason),
        school.map(e => schoolRow(e, checkInLine && e.text === check)),
        c.events.map(e => evRow(e, c.date)),
        c.todos.map(todoRow),
        empty && el('div.muted.small', '없음'));
}

const ACTION = { edit: '고침', delete: '지움' };
const evText = e => (e ? `${e.date}${e.end_date ? '~' + e.end_date : ''}${e.time ? ' ' + e.time : ''} ${e.text}${e.category ? ` (${e.category})` : ''}` : '');

export function openSchoolEdits() {
    openPopup({
        id: 'school-edits', refresh: true, title: '학사일정 고친 기록',
        render(pane) {
            const box = el('div', el('div.muted', '불러오는 중…'));
            pane.append(el('h1', '학사일정 고친 기록'),
                el('p.small.muted', '주간 계획·달력에서 학사일정을 고치거나 지운 기록입니다. 학사일정을 다시 가져와도 고친 대로 유지됩니다. 다시 원래대로 하려면 그 일정을 원래 값으로 고치면 됩니다.'),
                box);
            guarded(async () => {
                const rows = await api.get('/api/schoolcal/edits');
                clear(box, rows.length ? el('table.tbl',
                    el('thead', el('tr', el('th', '언제'), el('th', '무엇을'), el('th', '고치기 전'), el('th', '고친 뒤'), el('th', '이유'))),
                    el('tbody', rows.map(r => el('tr',
                        el('td.small', r.at.slice(0, 16)), el('td', ACTION[r.action] || r.action),
                        el('td.small', evText(r.before)), el('td.small', r.after ? evText(r.after) : '— (지움)'),
                        el('td.small', r.note || ''))))) : el('div.empty', '아직 고친 학사일정이 없습니다.'));
            })();
        },
    });
}

function render(pane, tab) {
    const move = n => () => { start = addDays(start, n * 7); tab.rerender(); };
    const setDays = n => () => { days = n; try { localStorage.setItem(KEY, String(n)); } catch { /* 저장 못 해도 화면은 그대로 */ } tab.rerender(); };
    const body = el('div', el('div.muted', '불러오는 중…'));
    const last = addDays(start, days - 1);
    pane.append(
        el('div.cal-head',
            el('h1', `${dayLabel(start, false)} ~ ${dayLabel(last, false)}`),
            el('button.btn.small', { onclick: move(-1) }, '◀ 이전 주'),
            el('button.btn.small', { onclick: () => { start = monday(todayIso()); tab.rerender(); } }, '이번 주'),
            el('button.btn.small', { onclick: move(1) }, '다음 주 ▶'),
            el('span.spacer'),
            el('button.btn.small', { onclick: openSchoolEdits, title: '학사일정을 언제·어떻게 고쳤는지 보기' }, '학사일정 고친 기록'),
            el('span.wk-toggle', { role: 'group', 'aria-label': '보는 기간' },
                el('button.btn.small', { className: `btn small${days === 7 ? ' primary' : ''}`, onclick: setDays(7) }, '1주'),
                el('button.btn.small', { className: `btn small${days === 14 ? ' primary' : ''}`, onclick: setDays(14) }, '2주'))),
        body);
    guarded(async () => {
        const w = await api.get(`/api/week?start=${start}&days=${days}`);
        const thisWeek = w.today >= w.start && w.today <= w.end;
        // 주마다 월~금 다섯 칸을 늘 같은 자리에 (수업 없는 날도 그 자리에 흐리게, 0.6.18 — 칸이 비어 줄이 어긋나지 않게)
        const byDate = new Map(w.cols.map(c => [c.date, { c, off: false }]));
        for (const o of w.other) if (parseIso(o.date).getDay() % 6) byDate.set(o.date, { c: o, off: true });
        const weekend = w.other.filter(o => !(parseIso(o.date).getDay() % 6));
        const weeks = [];
        for (let i = 0; i < w.days; i += 7) {
            const cols = [];
            for (let d = 0; d < 5; d++) {
                const iso = addDays(w.start, i + d);
                const hit = byDate.get(iso);
                cols.push(hit ? dayCol(hit.c, hit.off)
                    : dayCol({ date: iso, school_ev: [], events: [], todos: [], reason: null }, true));
            }
            weeks.push(el('div.wk-grid', cols));
        }
        clear(body,
            w.overdue.length > 0 && el('section.wk-box.alert', el('h3', '지난 할 일'), w.overdue.map(todoRow)),
            weeks,
            el('section.wk-box', el('h3', thisWeek ? '이번 주 안에 (날짜 없는 꼭 할 일)' : '날짜 없는 꼭 할 일'),
                w.undated.length ? w.undated.map(todoRow) : el('div.muted.small', '없음')),
            weekend.length > 0 && el('section.wk-box', el('h3', '주말 일정'),
                el('div.wk-weekend', weekend.map(o => dayCol(o, true)))),
            el('p.small.muted', '날짜 칸을 두 번 누르면 그 날짜에 일정을 직접 추가합니다. 일정을 누르면 원래 자료가 열리고, 학사일정(초록 글)을 누르면 고칠 수 있습니다. 조퇴·외출 기록은 여기에 나오지 않습니다.'));
    })();
}

export function openWeek() {
    openTab({ id: 'week', title: '주간 계획', menu: true, wide: true, render });
}

registerView({ id: 'week', icon: '🗓', label: '주간 계획', noSide: true, renderSide() {}, open: openWeek });

on('data-changed', () => findTab('week')?.rerender());
