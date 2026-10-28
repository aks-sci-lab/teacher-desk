// 📅 달력: 한 달 보기, 일정 추가·끝냄, 구글 캘린더용 .ics 내보내기
import { api } from '../api.js';
import { icon } from '../icons.js';
import { el, clear, toast, guarded, todayIso, isoDate, parseIso, addDays, dayLabel, download, eventLine, WD, CAT_VARS, CATEGORIES } from '../ui.js';
import { registerView, openTab, findTab, refreshSide, emit, on } from '../shell.js';
import { openItem } from './inbox.js';
import { openSchoolCal } from './schoolcal.js';

let month = todayIso().slice(0, 7);  // 'YYYY-MM'
let upcoming = [];

function openEvent(e) {
    if (e.item_id) openItem(e.item_id);
    else openEventForm(e);
}

export const exportIcs = guarded(async () => {
    const res = await api.get('/api/calendar.ics');
    download(await res.blob(), 'teacher-desk.ics');
    toast('달력 파일을 내려받았습니다. 구글 캘린더 → 설정 → 가져오기에서 올리세요.', 'info', 6000);
});

function openEventForm(e = {}) {
    const ev = { date: todayIso(), time: '', kind: 'event', category: '참고', text: '', ...e };
    openTab({
        id: e.id ? `event-${e.id}` : 'event-new', refresh: true, title: e.id ? '일정 고치기' : '새 일정',
        render(pane, tab) {
            const f = {
                date: el('input', { type: 'date', value: ev.date }),
                end: el('input', { type: 'date', value: ev.end_date || '' }),
                time: el('input', { type: 'time', value: ev.time || '' }),
                kind: el('select', el('option', { value: 'event', selected: ev.kind === 'event' }, '일정'),
                    el('option', { value: 'deadline', selected: ev.kind === 'deadline' }, '마감')),
                cat: el('select', CATEGORIES.map(c => el('option', { value: c, selected: c === ev.category }, c))),
                text: el('input', { type: 'text', value: ev.text, placeholder: '예: 학년 협의회' }),
            };
            pane.append(
                el('h1', e.id ? '일정 고치기' : '새 일정'),
                el('div.grid2',
                    el('label.field', el('span', '날짜'), f.date),
                    el('label.field', el('span', '끝 날짜 (기간일 때)'), f.end),
                    el('label.field', el('span', '시각 (선택)'), f.time),
                    el('label.field', el('span', '종류'), f.kind),
                    el('label.field', el('span', '분류'), f.cat)),
                el('label.field', el('span', '내용'), f.text),
                el('div.row',
                    el('button.btn.primary', { onclick: guarded(async () => {
                        const body = { date: f.date.value, end_date: f.end.value || null, time: f.time.value || null,
                            kind: f.kind.value, category: f.cat.value, text: f.text.value };
                        if (e.id) await api.patch(`/api/events/${e.id}`, body);
                        else await api.post('/api/events', body);
                        toast('저장했습니다');
                        tab.close();
                        emit('data-changed');
                    }) }, '저장'),
                    e.id && el('button.btn.danger', { onclick: guarded(async () => {
                        if (!confirm('이 일정을 지울까요?')) return;
                        await api.del(`/api/events/${e.id}`);
                        tab.close();
                        emit('data-changed');
                    }) }, '삭제')));
            setTimeout(() => f.text.focus(), 0);
        },
    });
}

function renderMonth(pane, tab) {
    const first = parseIso(`${month}-01`);
    const start = addDays(`${month}-01`, -first.getDay());
    const days = Array.from({ length: 42 }, (_, i) => addDays(start, i));
    const grid = el('div.cal', [...WD].map((d, i) => el('div', { className: `dow${i === 0 ? ' sun' : i === 6 ? ' sat' : ''}` }, d)));
    const cells = new Map();
    for (const d of days) {
        const dt = parseIso(d);
        const cell = el('div', {
            className: ['day', d.slice(0, 7) !== month && 'other', d === todayIso() && 'today',
                dt.getDay() === 0 && 'sun', dt.getDay() === 6 && 'sat'].filter(Boolean).join(' '),
            ondblclick: () => openEventForm({ date: d }),
            title: '두 번 누르면 일정 추가',
        }, el('div.num', dt.getDate()));
        cells.set(d, cell);
        grid.append(cell);
    }
    const title = `${first.getFullYear()}년 ${first.getMonth() + 1}월`;
    const move = n => () => {
        const d = parseIso(`${month}-01`);
        d.setMonth(d.getMonth() + n);
        month = isoDate(d).slice(0, 7);
        tab.rerender();
    };
    pane.append(
        el('div.cal-head',
            el('h1', title),
            el('button.btn.small', { onclick: move(-1) }, '◀'),
            el('button.btn.small', { onclick: () => { month = todayIso().slice(0, 7); tab.rerender(); } }, '오늘'),
            el('button.btn.small', { onclick: move(1) }, '▶'),
            el('span.spacer'),
            el('button.btn.small', { onclick: () => openEventForm() }, icon('plus'), '일정'),
            el('button.btn.small', { onclick: openSchoolCal, title: '학교 학사일정을 한꺼번에 넣기' }, '학사일정 가져오기'),
            el('button.btn.small', { onclick: exportIcs, title: '구글 캘린더 가져오기용' }, '.ics 내보내기')),
        grid,
        el('p.small.muted', '날짜 칸을 두 번 누르면 일정을 추가합니다. 일정을 누르면 원래 메시지·공문이 열립니다.'));
    guarded(async () => {
        const evs = await api.get(`/api/events?from=${days[0]}&to=${days[41]}`);
        for (const e of evs) {
            // 기간 일정은 기간 안의 모든 날에 표시
            const last = e.end_date && e.end_date < days[41] ? e.end_date : (e.end_date ? days[41] : e.date);
            for (let d = e.date < days[0] ? days[0] : e.date; d <= last; d = addDays(d, 1)) {
                const cell = cells.get(d);
                if (!cell) continue;
                cell.append(el('button.chipev', {
                    className: `chipev${e.done ? ' done' : ''}`,
                    style: `--cc: var(${CAT_VARS[e.category] || '--c-ref'})`,
                    title: `${e.time ? e.time + ' ' : ''}${e.text}`,
                    onclick: () => openEvent(e),
                }, `${e.kind === 'deadline' ? '마감 ' : ''}${e.time && d === e.date ? e.time + ' ' : ''}${e.item_title || e.text}`));
            }
        }
    })();
}

export function openCalendar() {
    openTab({ id: 'calendar', title: '달력', wide: true, render: renderMonth });
}

async function loadUpcoming() {
    upcoming = await api.get(`/api/events?from=${todayIso()}&to=${addDays(todayIso(), 30)}`);
}

registerView({
    id: 'calendar', icon: '📅', label: '달력',
    renderSide(body, actions) {
        actions.append(el('button.icon-btn', { title: '새 일정', onclick: () => openEventForm() }, icon('plus')));
        const pending = upcoming.filter(e => !e.done);
        if (!pending.length) return body.append(el('div.empty', '30일 안에 일정이 없습니다'));
        const list = el('div', { style: 'padding: 4px 12px' });
        let last = '';
        for (const e of pending) {
            const d = e.date < todayIso() ? todayIso() : e.date;
            if (d !== last) {
                list.append(el('div.small', { style: 'font-weight: 700; margin-top: 8px' }, dayLabel(d)));
                last = d;
            }
            list.append(eventLine(e, openEvent));
        }
        body.append(list);
    },
    open: openCalendar,
});

on('data-changed', guarded(async () => {
    await loadUpcoming();
    refreshSide();
    findTab('calendar')?.rerender();
}));

export async function initCalendar() {
    await loadUpcoming();
}
