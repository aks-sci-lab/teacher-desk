// 🗓 주간 계획 (읽기 전용): 수업 있는 날별 일정·마감·꼭 할 일·학사일정, 날짜 없는 꼭 할 일은 '이번 주 안에'
import { api } from '../api.js';
import { el, clear, guarded, todayIso, addDays, parseIso, dayLabel, ddayInfo, catBadge, CAT_VARS } from '../ui.js';
import { registerView, openTab, findTab, on } from '../shell.js';
import { openItem } from './inbox.js';
import { openCalendar } from './calendar.js';

const KEY = 'desk.week.days';
const monday = iso => addDays(iso, -((parseIso(iso).getDay() + 6) % 7));
let start = monday(todayIso());
let days = (() => { try { return localStorage.getItem(KEY) === '14' ? 14 : 7; } catch { return 7; } })();   // 기본 1주, 마지막 선택 기억

const openEv = e => (e.item_id ? openItem(e.item_id) : openCalendar());

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

function dayCol(c) {
    const today = c.date === todayIso();
    const empty = !c.events.length && !c.todos.length;
    return el('section', { className: `wk-col${today ? ' today' : ''}`, dataset: { date: c.date } },
        el('h3', dayLabel(c.date, !today), today && el('span.badge', '오늘')),
        c.check && el('div.wk-check', `⚠ 확인 필요: ${c.check}`),
        c.school.map(t => el('div.wk-school', t)),
        c.events.map(e => evRow(e, c.date)),
        c.todos.map(todoRow),
        empty && !c.school.length && el('div.muted.small', '없음'));
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
            el('span.wk-toggle', { role: 'group', 'aria-label': '보는 기간' },
                el('button.btn.small', { className: `btn small${days === 7 ? ' primary' : ''}`, onclick: setDays(7) }, '1주'),
                el('button.btn.small', { className: `btn small${days === 14 ? ' primary' : ''}`, onclick: setDays(14) }, '2주'))),
        body);
    guarded(async () => {
        const w = await api.get(`/api/week?start=${start}&days=${days}`);
        const thisWeek = w.today >= w.start && w.today <= w.end;
        const weeks = [];
        for (const c of w.cols) {
            const wk = Math.round((parseIso(monday(c.date)) - parseIso(w.start)) / (7 * 86400000));
            (weeks[wk] ||= []).push(dayCol(c));
        }
        clear(body,
            w.overdue.length > 0 && el('section.wk-box.alert', el('h3', '지난 할 일'), w.overdue.map(todoRow)),
            weeks.map(cols => cols && el('div.wk-grid', cols)),
            !w.cols.length && el('div.empty', '이 기간에는 수업이 있는 날이 없습니다'),
            el('section.wk-box', el('h3', thisWeek ? '이번 주 안에 (날짜 없는 꼭 할 일)' : '날짜 없는 꼭 할 일'),
                w.undated.length ? w.undated.map(todoRow) : el('div.muted.small', '없음')),
            w.other.length > 0 && el('section.wk-box', el('h3', '수업 없는 날의 일정'),
                w.other.map(o => el('div.wk-other', el('b', dayLabel(o.date)), o.reason && el('span.muted', ` ${o.reason}`),
                    o.school.filter(t => t !== o.reason).map(t => el('div.wk-school', t)),
                    o.events.map(e => evRow(e, o.date)), o.todos.map(todoRow)))),
            el('p.small.muted', '보기만 하는 화면입니다. 일정을 누르면 원래 자료가 열립니다. 조퇴·외출 기록은 여기에 나오지 않습니다.'));
    })();
}

export function openWeek() {
    openTab({ id: 'week', title: '주간 계획', wide: true, render });
}

registerView({ id: 'week', icon: '🗓', label: '주간 계획', noSide: true, renderSide() {}, open: openWeek });

on('data-changed', () => findTab('week')?.rerender());
