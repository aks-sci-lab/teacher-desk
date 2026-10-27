// 📥 업무 수신함: 쿨메신저·에듀파인·직접 입력 → 날짜·마감 추출 → 분류 → 일정
import { api } from '../api.js';
import { el, clear, toast, guarded, dayLabel, debounce, catBadge, srcBadge, todayIso, CATEGORIES, SOURCE_NAMES } from '../ui.js';
import { registerView, openTab, refreshSide, findTab, setBadge, emit, on } from '../shell.js';
import { loadState } from '../store.js';

let filter = { status: 'active', source: '' };
let items = [];

export async function loadItems() {
    const q = new URLSearchParams({ status: filter.status });
    if (filter.source) q.set('source', filter.source);
    items = await api.get(`/api/items?${q}`);
    return items;
}

export const collectNow = guarded(async () => {
    toast('수집을 시작합니다…');
    const s = await api.post('/api/collect');
    toast(s.sources.length
        ? `수집 완료: 새 항목 ${s.items}건, 일정 ${s.events}건${s.errors.length ? ` (문제 ${s.errors.length}건 — 아래 기록 참고)` : ''}`
        : '수집할 폴더가 없습니다. ⚙ 설정에서 폴더를 지정하세요.', s.errors.length ? 'error' : 'info', 6000);
    emit('data-changed');
});

// ---------------------------------------------------------------- 목록 패널
function renderSide(body, actions) {
    actions.append(
        el('button.icon-btn', { title: '직접 붙여넣기', onclick: openPaste }, '＋'),
        el('button.icon-btn', { title: '지금 수집', onclick: collectNow }, '⟳'));
    const chip = (key, value, label) => el('button.chip', {
        className: filter[key] === value ? 'chip on' : 'chip',
        onclick: guarded(async () => { filter[key] = value; await loadItems(); refreshSide(); }),
    }, label);
    body.append(
        el('div.filters', chip('status', 'active', '진행 중'), chip('status', 'done', '완료'), chip('status', 'archived', '보관'), chip('status', 'all', '전체')),
        el('div.filters', chip('source', '', '모든 출처'), chip('source', 'cool', '쿨메신저'), chip('source', 'edufine', '에듀파인'), chip('source', 'paste', '직접 입력')));
    const list = el('ul.list');
    if (!items.length) {
        list.append(el('li.empty', filter.status === 'active'
            ? '처리할 항목이 없습니다. ＋ 로 메시지를 붙여넣거나 ⟳ 로 수집하세요.' : '항목이 없습니다.'));
    }
    for (const it of items) {
        list.append(el('li', {
            className: [it.status === 'new' && 'unread', findTab(`item-${it.id}`) && 'on'].filter(Boolean).join(' '),
            onclick: () => openItem(it.id),
        },
        el('span.ttl', it.title),
        el('span.meta', srcBadge(it.source), catBadge(it.category),
            it.next_deadline && el('span', { style: 'color: var(--c-deadline); font-weight: 700' }, `⏰ ${dayLabel(it.next_deadline)}`),
            el('span', it.received_at.slice(5, 16)))));
    }
    body.append(list);
}

// ---------------------------------------------------------------- 분석 결과 미리보기 (붙여넣기 화면)
function analysisPreview(a) {
    return el('div',
        el('h2', '분석 결과 미리보기'),
        el('div.row', catBadge(a.category), el('b', a.title)),
        a.summary.length ? el('ul.summary', a.summary.map(s => el('li', s))) : null,
        el('h2', `찾은 일정 ${a.events.length}건`),
        a.events.length
            ? el('div', a.events.map(e => el('div', { className: `ev ${e.kind}` },
                el('span.when', (e.kind === 'deadline' ? '⏰ ' : '') + dayLabel(e.date) + (e.end_date ? `~${dayLabel(e.end_date, false)}` : '') + (e.time ? ` ${e.time}` : '')),
                catBadge(e.category), el('span', e.text))))
            : el('p.muted', '날짜를 찾지 못했습니다. 저장한 뒤 일정을 직접 추가할 수 있습니다.'),
        a.doc_numbers.length ? el('p.small.muted', `문서번호: ${a.doc_numbers.join(', ')}`) : null);
}

export function openPaste() {
    openTab({
        id: 'paste', title: '＋ 붙여넣기',
        render(pane, tab) {
            const src = el('select', ['paste', 'cool', 'edufine'].map(s => el('option', { value: s }, SOURCE_NAMES[s])));
            const title = el('input', { type: 'text', placeholder: '비워 두면 첫 줄이 제목이 됩니다' });
            const preview = el('div');
            const area = el('textarea', { rows: 14, placeholder: '쿨메신저 메시지나 공문 내용을 복사해 붙여넣으세요' });
            const analyze = debounce(guarded(async () => {
                if (!area.value.trim()) return clear(preview);
                clear(preview, analysisPreview(await api.post('/api/analyze', { text: area.value })));
            }), 400);
            area.addEventListener('input', analyze);
            pane.append(
                el('h1', '메시지·공문 붙여넣기'),
                el('p.sub', '날짜·마감·시각을 자동으로 찾아 일정으로 만듭니다. 내용은 이 PC 에만 저장됩니다.'),
                el('div.grid2', el('label.field', el('span', '출처'), src), el('label.field', el('span', '제목 (선택)'), title)),
                el('label.field', el('span', '내용'), area),
                el('div.row', el('button.btn.primary', { onclick: guarded(async () => {
                    const it = await api.post('/api/items', { text: area.value, source: src.value, title: title.value });
                    toast(`수신함에 저장했습니다 (일정 ${it.events.length}건)`);
                    tab.close();
                    emit('data-changed');
                    openItem(it.id);
                }) }, '📥 수신함에 저장')),
                preview);
            setTimeout(() => area.focus(), 0);
        },
    });
}

// ---------------------------------------------------------------- 항목 화면
export function openItem(id) {
    openTab({
        id: `item-${id}`, title: '…', refresh: true,
        render(pane, tab) {
            pane.append(el('p.muted', '불러오는 중…'));
            guarded(async () => {
                const it = await api.get(`/api/items/${id}`);
                tab.setTitle(`${it.source === 'edufine' ? '📄' : it.source === 'cool' ? '💬' : '📝'} ${it.title}`);
                clear(pane);
                renderItem(pane, tab, it);
                emit('item-read', id);
            })();
        },
    });
}

function renderItem(pane, tab, it) {
    const patch = guarded(async change => {
        await api.patch(`/api/items/${it.id}`, change);
        emit('data-changed');
        tab.rerender();
    });
    const titleInput = el('input', { type: 'text', value: it.title, style: 'font-size: 1.3em; font-weight: 700; width: 100%',
        onchange: e => patch({ title: e.target.value }) });
    const catSel = el('select', { onchange: e => patch({ category: e.target.value }) },
        CATEGORIES.map(c => el('option', { value: c, selected: c === it.category }, c)));

    pane.append(
        titleInput,
        el('div.row', { style: 'margin: 8px 0 4px' },
            srcBadge(it.source), catSel, el('span.muted.small', `받은 때 ${it.received_at.slice(0, 16)}`),
            el('span.spacer'),
            it.status !== 'done'
                ? el('button.btn', { onclick: () => patch({ status: 'done' }) }, '✔ 처리 완료')
                : el('button.btn', { onclick: () => patch({ status: 'read' }) }, '↺ 다시 진행 중으로'),
            it.status !== 'archived' && el('button.btn', { onclick: () => patch({ status: 'archived' }) }, '🗄 보관'),
            el('button.btn.danger', { onclick: guarded(async () => {
                if (!confirm('이 항목과 일정을 지울까요?')) return;
                await api.del(`/api/items/${it.id}`);
                tab.close();
                emit('data-changed');
            }) }, '삭제')),
        it.file_path ? el('p.small.muted', `파일: ${it.file_path}`) : '');

    if (it.summary.length) pane.append(el('h2', '요약'), el('ul.summary', it.summary.map(s => el('li', s))));

    // 일정 표 (바로 고치기)
    const evPatch = (e, change) => guarded(async () => {
        await api.patch(`/api/events/${e.id}`, change);
        emit('data-changed');
    })();
    const rows = it.events.map(e => el('tr',
        el('td.c', el('input', { type: 'checkbox', checked: !!e.done, title: '끝냄', onchange: ev => evPatch(e, { done: ev.target.checked }) })),
        el('td', el('input', { type: 'date', value: e.date, onchange: ev => evPatch(e, { date: ev.target.value }) })),
        el('td', el('input', { type: 'time', value: e.time || '', onchange: ev => evPatch(e, { time: ev.target.value || null }) })),
        el('td', el('select', { onchange: ev => evPatch(e, { kind: ev.target.value }) },
            el('option', { value: 'deadline', selected: e.kind === 'deadline' }, '⏰ 마감'),
            el('option', { value: 'event', selected: e.kind === 'event' }, '일정'))),
        el('td', el('select', { onchange: ev => evPatch(e, { category: ev.target.value }) },
            CATEGORIES.map(c => el('option', { value: c, selected: c === e.category }, c)))),
        el('td', el('input', { type: 'text', value: e.text, onchange: ev => evPatch(e, { text: ev.target.value }) }),
            e.end_date && el('div.small.muted', `~ ${e.end_date} 까지`)),
        el('td', el('button.icon-btn', { title: '일정 지우기', onclick: guarded(async () => {
            await api.del(`/api/events/${e.id}`);
            emit('data-changed');
            tab.rerender();
        }) }, '🗑'))));
    pane.append(
        el('h2', `일정 ${it.events.length}건`),
        el('div', { style: 'overflow-x: auto' }, el('table.tbl',
            el('thead', el('tr', ['끝냄', '날짜', '시각', '종류', '분류', '내용', ''].map(h => el('th', h)))),
            el('tbody', rows))),
        el('div.row', { style: 'margin-top: 8px' }, el('button.btn.small', { onclick: guarded(async () => {
            await api.post('/api/events', { item_id: it.id, date: todayIso(), kind: 'event', category: it.category, text: it.title });
            emit('data-changed');
            tab.rerender();
        }) }, '＋ 일정 추가')));

    if (it.related.length) {
        pane.append(el('h2', '🔗 관련 항목 (같은 문서번호)'),
            el('ul.list', it.related.map(r => el('li', { onclick: () => openItem(r.id) },
                el('span.ttl', r.title), el('span.meta', srcBadge(r.source), r.received_at.slice(0, 16))))));
    }
    if (it.doc_numbers.length) pane.append(el('p.small.muted', `문서번호: ${it.doc_numbers.join(', ')}`));
    pane.append(el('h2', '원문'), el('details.orig', { open: it.body.length < 1500 }, el('summary', `${it.body.length.toLocaleString()}자`), el('pre.text', it.body)));
}

registerView({
    id: 'inbox', icon: '📥', label: '수신함',
    renderSide,
});

async function refresh() {
    await loadItems();
    const st = await loadState();
    setBadge('inbox', st.counts.new_items);
    refreshSide();
}

on('data-changed', guarded(refresh));
on('item-read', guarded(refresh));

export async function initInbox() {
    await loadItems();
}
