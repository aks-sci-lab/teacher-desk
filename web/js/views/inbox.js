// 📥 업무 수신함: 쿨메신저·에듀파인·직접 입력 → 날짜·마감 추출 → 분류 → 일정
import { api } from '../api.js';
import { el, clear, toast, guarded, dayLabel, debounce, catBadge, srcBadge, todayIso, parseIso, ddayInfo, markDates, modal, WD, CATEGORIES, SOURCE_NAMES } from '../ui.js';
import { icon } from '../icons.js';
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
        ? `수집 완료: 새 항목 ${s.items}건, 일정 ${s.events}건${s.errors.length ? ` (문제 ${s.errors.length}건 — 오른쪽 아래 '기록' 참고)` : ''}`
        : '수집할 폴더가 없습니다. 설정에서 폴더를 지정하세요.', s.errors.length ? 'error' : 'info', 6000);
    emit('data-changed');
});

// 체크 = 이 메시지의 일정을 달력·브리핑에 등록 (체크를 풀면 빠짐, 일정 자체는 남음)
const setRegistered = guarded(async (id, on) => {
    await api.patch(`/api/items/${id}`, { registered: on });
    toast(on ? '일정을 달력에 넣었습니다' : '일정을 달력에서 뺐습니다');
    emit('data-changed');
    findTab(`item-${id}`)?.rerender();
});

// ---------------------------------------------------------------- 목록 패널
function renderSide(body, actions) {
    actions.append(
        el('button.icon-btn', { title: '직접 붙여넣기', onclick: openPaste }, icon('plus')),
        el('button.icon-btn', { title: '지금 수집', onclick: collectNow }, icon('refresh')));
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
            ? '처리할 항목이 없습니다. 위의 + 로 메시지를 붙여넣거나 ↻ 로 수집하세요.' : '항목이 없습니다.'));
    }
    for (const it of items) {
        list.append(el('li', {
            className: ['reg', it.status === 'new' && 'unread', findTab(`item-${it.id}`) && 'on'].filter(Boolean).join(' '),
            onclick: () => openItem(it.id),
        },
        el('input', { type: 'checkbox', checked: !!it.registered,
            title: it.event_count ? `체크하면 일정 ${it.event_count}건이 달력·브리핑에 들어갑니다` : '체크하면 이 메시지의 일정이 달력·브리핑에 들어갑니다',
            onclick: e => e.stopPropagation(),
            onchange: e => setRegistered(it.id, e.target.checked) }),
        el('div.body',
            el('span.ttl', it.title),
            el('span.meta', srcBadge(it.source), catBadge(it.category),
                it.event_count ? el('span', it.registered ? `일정 ${it.event_count}` : `후보 ${it.event_count}`) : null,
                it.next_deadline && el('span.dl', `마감 ${dayLabel(it.next_deadline)}`),
                el('span', it.received_at.slice(5, 16))))));
    }
    body.append(el('p.small.muted', { style: 'margin: 6px var(--pad)' }, '체크한 메시지의 일정만 달력·브리핑에 들어갑니다.'), list);
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
                el('span.when', (e.kind === 'deadline' ? '마감 ' : '') + dayLabel(e.date) + (e.end_date ? `~${dayLabel(e.end_date, false)}` : '') + (e.time ? ` ${e.time}` : '')),
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
                }) }, '수신함에 저장')),
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
                tab.setTitle(it.title);
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
    const catSel = el('select.quiet', { title: '분류 바꾸기', onchange: e => patch({ category: e.target.value }) },
        CATEGORIES.map(c => el('option', { value: c, selected: c === it.category }, c)));

    // 제목: 글자로 크게, ✎ 를 누르면 고치기
    const titleBox = el('div.item-title');
    const showTitle = () => clear(titleBox,
        el('h1', it.title),
        el('button.icon-btn', { title: '제목 고치기', onclick: editTitle }, icon('edit')));
    const editTitle = () => {
        const input = el('input', { type: 'text', value: it.title,
            onkeydown: e => { if (e.key === 'Enter') input.blur(); if (e.key === 'Escape') { input.value = it.title; input.blur(); } },
            onblur: () => (input.value.trim() && input.value !== it.title ? patch({ title: input.value.trim() }) : showTitle()) });
        clear(titleBox, input);
        input.focus();
        input.select();
    };
    showTitle();

    const actions = el('div.item-actions',
        it.registered
            ? el('button.btn.ok', { onclick: () => patch({ registered: false }), title: '누르면 이 메시지의 일정을 달력·브리핑에서 뺍니다' }, icon('check'), '달력에 넣음')
            : el('button.btn.primary', { onclick: () => patch({ registered: true }), title: '이 메시지의 일정을 달력·브리핑에 넣습니다' }, icon('calendar'), '일정 등록'),
        it.status !== 'done'
            ? el('button.btn', { onclick: () => patch({ status: 'done' }) }, icon('check'), '처리 완료')
            : el('button.btn', { onclick: () => patch({ status: 'read' }) }, '↺ 다시 진행 중으로'),
        it.status !== 'archived' && el('button.btn', { onclick: () => patch({ status: 'archived' }) }, icon('archive'), '보관'),
        el('button.btn.danger', { title: '이 항목과 일정 지우기', onclick: guarded(async () => {
            if (!confirm('이 항목과 일정을 지울까요?')) return;
            await api.del(`/api/items/${it.id}`);
            tab.close();
            emit('data-changed');
        }) }, icon('trash'), '삭제'));

    const head = el('header.item-head',
        el('div.item-kicker', srcBadge(it.source), catSel, el('span', `받은 때 ${it.received_at.slice(0, 16)}`)),
        titleBox, actions);

    // ---- 일정 (가장 위에, 크게)
    const open = it.events.filter(e => !e.done).length;
    const evSec = el('section.item-sec',
        el('div.sec-head',
            el('h2', it.registered ? '일정' : '일정 후보', el('span.count', it.events.length)),
            it.events.length > 0 && (it.registered
                ? el('span.pill.ok', '달력·브리핑에 들어감')
                : el('span.pill.warn', '아직 달력에 넣지 않음')),
            el('span.spacer'),
            el('button.btn.small', { onclick: guarded(async () => {
                await api.post('/api/events', { item_id: it.id, date: todayIso(), kind: 'event', category: it.category, text: it.title });
                emit('data-changed');
                tab.rerender();
            }) }, icon('plus'), '일정 추가')),
        it.events.length
            ? el('div.evcs', it.events.map(e => eventCard(e, it, tab)))
            : el('p.muted', '글에서 날짜를 찾지 못했습니다. 필요하면 ＋ 일정 추가로 직접 넣으세요.'),
        !it.registered && open > 0 && el('p.small.muted', '맞으면 위의 [일정 등록] 을 누르거나 수신함 목록에서 체크하세요. 틀린 것은 고치거나 지우면 됩니다.'));

    // ---- 내용 요약
    const sumSec = el('section.item-sec',
        el('div.sec-head', el('h2', '내용 요약'), el('span.spacer'),
            el('button.btn.small', { onclick: () => openOriginal(it) }, icon('doc'), '원문 보기')),
        it.summary.length
            ? el('ul.item-summary', it.summary.map(s => el('li', markDates(s))))
            : el('p.muted', '요약할 줄을 찾지 못했습니다. 원문 보기로 확인하세요.'));

    // ---- 오른쪽: 원문·파일·문서번호·관련 항목·사진
    const f = it.file;
    const side = el('aside.item-side',
        el('section.card.info',
            el('h3', '원문'),
            el('div.info-btns',
                el('button.btn', { onclick: () => openOriginal(it) }, icon('doc'), `글자로 보기 (${it.body.length.toLocaleString()}자)`),
                f && f.pdf && el('button.btn', { onclick: () => openOriginal(it, { pdf: true }) }, icon('doc'), 'PDF 원본 보기'),
                f && f.can_open && el('button.btn', { onclick: () => openFile(it) }, icon('open'), f.ext === '.pdf' ? 'PDF 프로그램으로 열기' : '한글로 열기')),
            f && el('div.kv', el('span', '파일'), el('span.path', { title: it.file_path }, f.name)),
            it.doc_numbers.length > 0 && el('div.kv', el('span', '문서번호'), el('span', it.doc_numbers.map(n => el('span.tag', n))))),
        it.related.length > 0 && el('section.card.info',
            el('h3', '관련 항목 (같은 문서번호)'),
            el('ul.list.compact', it.related.map(r => el('li', { onclick: () => openItem(r.id) },
                el('span.ttl', r.title), el('span.meta', srcBadge(r.source), r.received_at.slice(0, 16)))))),
        it.images && it.images.length > 0 && el('section.card.info', el('h3', `사진 ${it.images.length}장`), imageGrid(it)));

    pane.append(el('div.item-view', head, el('div.item-grid', el('div.item-main', evSec, sumSec), side)));
}

// 일정 후보 하나: 날짜를 크게(D-day), 무엇인지, 원문 어느 줄에서 찾았는지. ✎ 로 고치기
function eventCard(e, it, tab) {
    const card = el('div', { className: `evc ${e.kind}${e.done ? ' done' : ''}` });
    const save = guarded(async change => {
        await api.patch(`/api/events/${e.id}`, change);
        emit('data-changed');
        tab.rerender();
    });
    const d = parseIso(e.date);
    const dd = ddayInfo(e.date);
    const view = () => clear(card,
        el('div.evc-date',
            el('div.md', `${d.getMonth() + 1}.${d.getDate()}`),
            el('div.wd', `${WD[d.getDay()]}요일`),
            !e.done && el('span', { className: `dday ${dd.cls}` }, dd.label)),
        el('div.evc-body',
            el('div.evc-top',
                el('span', { className: `kind ${e.kind}` }, e.kind === 'deadline' ? '마감' : '일정'),
                catBadge(e.category),
                e.time && el('span.evc-time', icon('clock'), e.time),
                e.end_date && el('span.evc-time', `~ ${dayLabel(e.end_date, false)} 까지`),
                e.source === 'ai' && el('span.ai-mark', { title: '브리핑 AI 요약에서 찾은 일정입니다' }, icon('spark'), 'AI')),
            el('div.evc-text', e.text),
            e.context && el('div.evc-ctx', { title: '원문에서 이 날짜가 나온 곳' },
                e.context.before && el('div.c-side', e.context.before),
                el('div.c-line', markDates(e.context.line)),
                e.context.after && el('div.c-side', e.context.after),
                el('button.link', { onclick: () => openOriginal(it, { line: e.context.index }) }, '원문에서 보기 →'))),
        el('div.evc-tools',
            el('label.done', { title: '끝냄 표시' }, el('input', { type: 'checkbox', checked: !!e.done, onchange: ev => save({ done: ev.target.checked }) }), '끝냄'),
            el('button.icon-btn', { title: '고치기', onclick: edit }, icon('edit')),
            el('button.icon-btn', { title: '이 일정 지우기', onclick: guarded(async () => {
                await api.del(`/api/events/${e.id}`);
                emit('data-changed');
                tab.rerender();
            }) }, icon('trash'))));
    const edit = () => {
        const f = {
            date: el('input', { type: 'date', value: e.date }),
            time: el('input', { type: 'time', value: e.time || '' }),
            kind: el('select', el('option', { value: 'deadline', selected: e.kind === 'deadline' }, '마감'),
                el('option', { value: 'event', selected: e.kind === 'event' }, '일정')),
            category: el('select', CATEGORIES.map(c => el('option', { value: c, selected: c === e.category }, c))),
            text: el('input', { type: 'text', value: e.text }),
        };
        clear(card,
            el('div.evc-edit',
                el('label.field', el('span', '날짜'), f.date),
                el('label.field', el('span', '시각'), f.time),
                el('label.field', el('span', '종류'), f.kind),
                el('label.field', el('span', '분류'), f.category),
                el('label.field.wide', el('span', '내용'), f.text),
                el('div.row',
                    el('button.btn.primary.small', { onclick: () => save({ date: f.date.value, time: f.time.value || null, kind: f.kind.value, category: f.category.value, text: f.text.value }) }, '저장'),
                    el('button.btn.small', { onclick: view }, '취소'))));
        f.text.focus();
    };
    view();
    return card;
}

// 원문 팝업: 줄바꿈·띄어쓰기를 받은 그대로 (원래 양식). PDF 는 원본 그대로도 볼 수 있음
function openOriginal(it, opts = {}) {
    const textBox = el('div.raw-text');
    let n = -1;
    let target = null;
    for (const raw of it.body.split('\n')) {
        const line = raw.replace(/\s+$/, '');
        if (!line.trim()) { textBox.append(el('div.rl.blank')); continue; }
        n += 1;
        const row = el('div.rl', markDates(line));
        if (n === opts.line) { row.classList.add('hl'); target = row; }
        textBox.append(row);
    }
    const body = el('div.raw-wrap', textBox);
    const f = it.file;
    const showPdf = guarded(async () => {
        const res = await api.get(`/api/items/${it.id}/file`);
        const url = URL.createObjectURL(await res.blob());
        clear(body, el('iframe.pdf', { src: url, title: 'PDF 원본' }));
        seg.querySelectorAll('button').forEach((b, i) => b.classList.toggle('on', i === 1));
    });
    const showText = () => {
        clear(body, textBox);
        seg.querySelectorAll('button').forEach((b, i) => b.classList.toggle('on', i === 0));
    };
    const seg = f && f.pdf ? el('div.seg', el('button.on', { onclick: showText }, '글자'), el('button', { onclick: showPdf }, 'PDF 원본')) : null;
    modal({
        title: it.title, wide: true, body,
        actions: [
            seg,
            el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(it.body); toast('원문을 복사했습니다'); }) }, icon('copy'), '복사'),
            f && f.can_open && el('button.btn.small', { onclick: () => openFile(it) }, icon('open'), f.ext === '.pdf' ? 'PDF 프로그램으로' : '한글로 열기'),
        ],
    });
    if (opts.pdf) showPdf();
    else if (target) setTimeout(() => target.scrollIntoView({ block: 'center' }), 0);
}

const openFile = guarded(async it => {
    await api.post(`/api/items/${it.id}/open`);
    toast('원본 파일을 열었습니다 (작업 표시줄의 새 창을 확인하세요)');
});

// 쿨메신저 사진: 인터넷 주소는 바로, 쪽지 안 그림·PC 파일은 업무 데스크에서 받아 보여 줌. 누르면 크게
function imageGrid(it) {
    const grid = el('div.img-grid');
    for (const im of it.images) {
        const img = el('img', { alt: `사진 ${im.n + 1}`, loading: 'lazy', referrerPolicy: 'no-referrer',
            onclick: () => img.classList.toggle('big'),
            onerror: () => img.replaceWith(el('div.img-missing', '사진을 불러올 수 없습니다')) });
        grid.append(img);
        if (im.url) {
            img.src = im.url;
        } else {
            api.get(`/api/items/${it.id}/images/${im.n}`)
                .then(res => res.blob())
                .then(b => { img.src = URL.createObjectURL(b); })
                .catch(e => img.replaceWith(el('div.img-missing', e.message)));
        }
    }
    return grid;
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
