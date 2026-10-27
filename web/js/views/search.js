// 🔍 전체 검색: 수신 항목(제목·본문·요약·문서번호) · 일정 · 조퇴/외출 기록 · 학생 명단
import { api } from '../api.js';
import { el, clear, guarded, debounce, dayLabel, catBadge, srcBadge, eventLine } from '../ui.js';
import { registerView } from '../shell.js';
import { openItem } from './inbox.js';
import { openPass, openRoster } from './passes.js';
import { openCalendar } from './calendar.js';

let query = '';

registerView({
    id: 'search', icon: '🔍', label: '검색',
    renderSide(body) {
        const results = el('div');
        const run = debounce(guarded(async () => {
            if (!query.trim()) return clear(results, el('div.empty', '메시지·공문 내용, 일정, 조퇴·외출 기록, 학생 명단 전체에서 찾습니다. 띄어 쓰면 모든 낱말이 들어간 것만 보여 줍니다.'));
            const r = await api.get(`/api/search?q=${encodeURIComponent(query)}`);
            const total = r.items.length + r.events.length + r.passes.length + r.students.length;
            clear(results,
                !total && el('div.empty', '찾은 것이 없습니다'),
                r.items.length > 0 && el('div.list', el('div.group', `수신 항목 ${r.items.length}`)),
                r.items.length > 0 && el('ul.list', r.items.map(it => el('li', { onclick: () => openItem(it.id) },
                    el('span.ttl', it.title), el('span.meta', srcBadge(it.source), catBadge(it.category), it.received_at.slice(0, 10))))),
                r.events.length > 0 && el('div.list', el('div.group', `일정 ${r.events.length}`)),
                r.events.length > 0 && el('div', { style: 'padding: 4px 12px' },
                    r.events.map(e => eventLine(e, ev => (ev.item_id ? openItem(ev.item_id) : openCalendar())))),
                r.passes.length > 0 && el('div.list', el('div.group', `조퇴·외출 ${r.passes.length}`)),
                r.passes.length > 0 && el('ul.list', r.passes.map(p => el('li', { onclick: () => openPass(p) },
                    el('span.ttl', `${p.student_name} ${p.kind === 'out' ? '외출' : '조퇴'}`),
                    el('span.meta', dayLabel(p.date), p.reason || '')))),
                r.students.length > 0 && el('div.list', el('div.group', `학생 ${r.students.length}`)),
                r.students.length > 0 && el('ul.list', r.students.map(st => el('li', { onclick: openRoster },
                    el('span.ttl', `${st.number}번 ${st.name}`)))));
        }), 250);
        const input = el('input', { type: 'search', value: query, placeholder: '검색어', style: 'width: 100%',
            oninput: e => { query = e.target.value; run(); } });
        body.append(el('div', { style: 'padding: 10px 12px' }, input), results);
        run();
        setTimeout(() => input.focus(), 0);
    },
});
