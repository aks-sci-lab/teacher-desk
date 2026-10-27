// 📋 브리핑: 지난 마감 · 오늘 일정 · 7일 안 마감 · 오늘 조퇴/외출 · 새로 받은 자료 · 수집 상태
import { api } from '../api.js';
import { el, clear, guarded, longDate, todayIso, eventLine, catBadge, srcBadge } from '../ui.js';
import { registerView, openTab, findTab, on } from '../shell.js';
import { store } from '../store.js';
import { openItem, openPaste, collectNow } from './inbox.js';
import { openForm, openPass } from './passes.js';
import { openCalendar } from './calendar.js';

const KIND = { early: '조퇴', out: '외출' };

function openEv(e) {
    if (e.item_id) openItem(e.item_id);
    else openCalendar();
}

function card(title, count, body, opts = {}) {
    return el('section.card', { className: `card${opts.alert ? ' alert' : ''}` },
        el('h3', el('span', title), count != null && el('span.muted', `${count}건`)),
        body);
}

function renderToday(pane) {
    pane.append(el('p.muted', '불러오는 중…'));
    guarded(async () => {
        const s = await api.get('/api/today');
        clear(pane);
        const st = store.state;
        const evList = (evs, empty) => evs.length ? el('div', evs.map(e => eventLine(e, openEv))) : el('p.muted.small', empty);
        pane.append(
            el('h1', `${longDate(todayIso())}`),
            el('p.sub', `${store.settings.teacher_name ? store.settings.teacher_name + ' 선생님, ' : ''}오늘 할 일을 모았습니다.`),
            el('div.row', { style: 'margin-bottom: 16px' },
                el('button.btn', { onclick: openPaste }, '＋ 메시지 붙여넣기'),
                el('button.btn', { onclick: () => openForm({ kind: 'early' }) }, '🎫 조퇴증'),
                el('button.btn', { onclick: () => openForm({ kind: 'out' }) }, '🎫 외출증'),
                el('button.btn', { onclick: collectNow }, '⟳ 지금 수집')),
            el('div.cards',
                s.overdue.length ? card('⚠ 지난 마감 (끝냄 표시 안 됨)', s.overdue.length, evList(s.overdue), { alert: true }) : null,
                card('📌 오늘 일정', s.today.length, evList(s.today, '오늘 일정이 없습니다')),
                card('⏰ 7일 안 마감', s.week_deadlines.length, evList(s.week_deadlines, '다가오는 마감이 없습니다')),
                card('🗓 7일 안 일정', s.week_events.length, evList(s.week_events, '다가오는 일정이 없습니다')),
                card('🎫 오늘 조퇴·외출', s.passes.length, s.passes.length
                    ? el('ul.list', s.passes.map(p => el('li', { onclick: () => openPass(p) },
                        el('span.ttl', `${p.student_number != null ? p.student_number + '번 ' : ''}${p.student_name}`),
                        el('span.meta', KIND[p.kind], p.time_out || '', p.time_return ? `~${p.time_return}` : '',
                            p.kind === 'out' ? (p.status === 'returned' ? '✔ 복귀' : '복귀 전') : ''))))
                    : el('p.muted.small', '없습니다')),
                card('📥 새로 받은 자료', s.new_items.length, s.new_items.length
                    ? el('ul.list', s.new_items.map(it => el('li', { onclick: () => openItem(it.id) },
                        el('span.ttl', it.title), el('span.meta', srcBadge(it.source), catBadge(it.category), it.received_at.slice(5, 16)))))
                    : el('p.muted.small', '새 자료가 없습니다')),
                card('🔄 자동 수집', null, el('div.small',
                    el('div', `수집 시각: ${(store.settings.collect_times || []).join(', ') || '없음'}`),
                    el('div', `다음 수집: ${st?.next_collect || '-'}`),
                    el('div', `마지막 수집: ${s.last_run ? s.last_run.finished_at.slice(5, 16) : '아직 없음'}`),
                    el('div', `쿨메신저: ${store.settings.coolmessenger_path || store.settings.coolmessenger_name || '미설정'}${store.settings.coolmessenger_folder ? ' (+ 받은 파일 폴더)' : ''}`),
                    el('div', `에듀파인 폴더: ${store.settings.edufine_folder || '미설정'}`),
                    el('div', `텔레그램: ${st?.telegram.linked ? '연결됨' : '연결 안 됨'}`)))));
    })();
}

export function openToday() {
    openTab({ id: 'today', title: '📋 브리핑', refresh: true, render: renderToday });
}

registerView({
    id: 'today', icon: '📋', label: '브리핑',
    renderSide(body) {
        body.append(el('div.empty', '오른쪽에 오늘의 업무 브리핑이 있습니다.'),
            el('ul.list',
                el('li', { onclick: openToday }, el('span.ttl', '📋 브리핑 새로 고침')),
                el('li', { onclick: openCalendar }, el('span.ttl', '📅 달력 열기')),
                el('li', { onclick: openPaste }, el('span.ttl', '＋ 메시지·공문 붙여넣기'))));
    },
    open: openToday,
});

on('data-changed', () => findTab('today')?.rerender());
