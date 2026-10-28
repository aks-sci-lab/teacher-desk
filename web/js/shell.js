// 화면 틀: ① 활동 막대 ② 목록 패널 ③ 탭 ④ 작업 영역 ⑤ 하단 패널 ⑥ 상태 표시줄 + 🧩 화면 배치 모드
import { api } from './api.js';
import { el, clear, toast, guarded } from './ui.js';
import { icon } from './icons.js';

const $ = id => document.getElementById(id);
const app = $('app');
const views = new Map();
const tabs = new Map();
let currentView = null;
let activeTab = null;

export const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export const on = (name, fn) => bus.addEventListener(name, e => fn(e.detail));

// ---------------------------------------------------------------- 활동 막대·목록 패널
// view = { id, icon, label, bottom?, noSide?(목록 패널 없이 작업 영역만), renderSide(body, actions), open?() }
export function registerView(view) {
    views.set(view.id, view);
    const btn = el('button.act', { title: view.label, dataset: { view: view.id }, onclick: () => showView(view.id, true) },
        el('span.ico', icon(view.id) || view.icon), el('span.lbl', view.label), el('span.badge', { hidden: true }));
    $(view.bottom ? 'activity-bottom' : 'activity-top').append(btn);
    view.button = btn;
}

// 활동 막대 버튼 순서 정하기 (모듈을 불러오는 순서와 상관없이)
export function orderViews(ids) {
    for (const id of ids) {
        const v = views.get(id);
        if (v) v.button.parentElement.append(v.button);
    }
}

export function setBadge(viewId, n) {
    const b = views.get(viewId)?.button.querySelector('.badge');
    if (!b) return;
    b.hidden = !n;
    b.textContent = n > 99 ? '99+' : n;
}

export function showView(id, fromClick = false) {
    const view = views.get(id);
    if (!view) return;
    // 같은 버튼을 다시 누르면 목록 패널 접기/펴기 (VS Code 와 같은 동작)
    if (fromClick && currentView === id && !isNarrow() && !view.noSide) {
        app.classList.toggle('side-hidden');
        return;
    }
    app.classList.remove('side-hidden');
    app.classList.toggle('no-side', !!view.noSide);   // 목록이 필요 없는 화면은 빈 목록 패널을 숨김
    currentView = id;
    for (const v of views.values()) v.button.classList.toggle('on', v.id === id);
    $('side-title').textContent = view.label;
    clear($('side-actions'));
    clear($('side-body'));
    view.renderSide($('side-body'), $('side-actions'));
    if (view.open && (!isNarrow() || !fromClick || view.noSide)) view.open();
    if (isNarrow()) app.classList.toggle('show-main', !!view.noSide);
}

export function refreshSide() {
    if (currentView) {
        const view = views.get(currentView);
        const body = $('side-body');
        const scroll = body.scrollTop;
        clear($('side-actions'));
        clear(body);
        view.renderSide(body, $('side-actions'));
        body.scrollTop = scroll;
    }
}

export function sideButton(label, title, onclick) {
    $('side-actions').append(el('button.icon-btn', { title, onclick }, label));
}

// ---------------------------------------------------------------- 탭
// 탭 이름 앞의 이모지는 떼고 글자만 (화면이 어수선하지 않게)
const P_LEAD_ICON = /^(?:\p{Extended_Pictographic}|[＋✎⚙☑✔↺])\uFE0F?\s*/u;
const plainTitle = t => String(t).replace(P_LEAD_ICON, '');

// openTab({ id, title, render(pane, tab), wide? }) — 같은 id 가 열려 있으면 그 탭으로 이동
export function openTab(spec) {
    let tab = tabs.get(spec.id);
    if (!tab) {
        const pane = el('div.pane', { role: 'tabpanel' });
        if (spec.wide) pane.classList.add('wide');
        const label = el('span.t', plainTitle(spec.title));
        const head = el('button.tab', { role: 'tab', title: plainTitle(spec.title), onclick: () => activate(spec.id) },
            label, el('span.x', { title: '닫기', onclick: e => { e.stopPropagation(); closeTab(spec.id); } }, '✕'));
        head.addEventListener('auxclick', e => { if (e.button === 1) closeTab(spec.id); });
        $('tab-list').append(head);
        $('editor').append(pane);
        tab = {
            id: spec.id, pane, head, spec,
            setTitle(t) { label.textContent = plainTitle(t); head.title = plainTitle(t); },
            close: () => closeTab(spec.id),
            rerender: () => { clear(pane); tab.spec.render(pane, tab); },
        };
        tabs.set(spec.id, tab);
        spec.render(pane, tab);
    } else if (spec.refresh) {
        tab.spec = spec;
        tab.setTitle(spec.title);
        tab.rerender();
    }
    activate(spec.id);
    return tab;
}

function activate(id) {
    activeTab = id;
    for (const t of tabs.values()) {
        const onNow = t.id === id;
        t.head.classList.toggle('on', onNow);
        t.pane.classList.toggle('on', onNow);
        t.head.setAttribute('aria-selected', onNow);
    }
    tabs.get(id)?.head.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    if (isNarrow()) app.classList.add('show-main');
    emit('tab', id);
}

export function closeTab(id) {
    const tab = tabs.get(id);
    if (!tab) return;
    tab.spec.onClose?.();
    tab.head.remove();
    tab.pane.remove();
    tabs.delete(id);
    if (activeTab === id) {
        const last = [...tabs.keys()].pop();
        if (last) activate(last);
        else if (isNarrow()) app.classList.remove('show-main');
    }
}

export function findTab(id) {
    return tabs.get(id);
}

export function rerenderTabs(prefix) {
    for (const t of tabs.values()) if (t.id.startsWith(prefix)) t.rerender();
}

$('back').addEventListener('click', () => app.classList.remove('show-main'));

export function isNarrow() {
    return matchMedia('(max-width: 760px)').matches;
}

// ---------------------------------------------------------------- 하단 패널 (기록)
let lastLog = 0;
export async function pollLogs() {
    try {
        const rows = await api.get(`/api/logs?after=${lastLog}`);
        const box = $('log');
        const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
        for (const r of rows) {
            lastLog = Math.max(lastLog, r.id);
            box.append(el('div', { className: `l ${r.level}` }, el('time', r.at.slice(5, 16)), r.message));
        }
        while (box.children.length > 300) box.firstChild.remove();
        if (stick) box.scrollTop = box.scrollHeight;
    } catch (e) { /* 상태 표시줄에서 연결 끊김을 보여 줌 */ }
}
$('bottom-close').addEventListener('click', () => setLayout({ bottomVisible: false }));

// ---------------------------------------------------------------- 상태 표시줄
const status = {};
export function statusItem(id, opts = {}) {
    const node = el(opts.onclick ? 'button.sb' : 'span.sb', { title: opts.title || '', onclick: opts.onclick });
    if (opts.right) {
        if (!$('statusbar').querySelector('.grow')) $('statusbar').append(el('span.grow'));
    }
    $('statusbar').append(node);
    status[id] = node;
    return node;
}
export function setStatus(id, text, title) {
    const n = status[id];
    if (!n) return;
    n.textContent = text;
    if (title != null) n.title = title;
}

// ---------------------------------------------------------------- 화면 배치
export const DEFAULT_LAYOUT = {
    sidePosition: 'left',
    sideWidth: 300,
    bottomVisible: false,   // 기록은 상태 표시줄의 '기록' 을 누르면 보임 (0.5.13 부터 기본 숨김)
    v: 2,
    bottomHeight: 150,
    fontScale: 1,
    compact: false,
    theme: 'auto',
    labels: true,
};
let layout = { ...DEFAULT_LAYOUT };
let saveTimer;

export function getLayout() {
    return { ...layout };
}

export function applyLayout(next, save = true) {
    layout = { ...DEFAULT_LAYOUT, ...next };
    const root = document.documentElement;
    app.classList.toggle('side-right', layout.sidePosition === 'right');
    app.classList.toggle('side-left', layout.sidePosition !== 'right');
    app.classList.toggle('bottom-hidden', !layout.bottomVisible);
    app.classList.toggle('hide-labels', !layout.labels);
    root.classList.toggle('compact', !!layout.compact);
    root.style.setProperty('--side-w', `${layout.sideWidth}px`);
    root.style.setProperty('--bottom-h', `${layout.bottomHeight}px`);
    root.style.setProperty('--font-scale', layout.fontScale);
    const dark = layout.theme === 'dark' || (layout.theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
    root.dataset.theme = dark ? 'dark' : 'light';
    if (save) {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(guarded(() => api.put('/api/settings', { layout })), 500);
    }
    emit('layout', layout);
}

export function setLayout(change) {
    applyLayout({ ...layout, ...change });
}

matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyLayout(layout, false));

// 끌어서 크기 조절
function dragSplitter(splitter, onMove) {
    splitter.addEventListener('pointerdown', e => {
        e.preventDefault();
        splitter.setPointerCapture(e.pointerId);
        splitter.classList.add('drag');
        const move = ev => onMove(ev);
        const up = () => {
            splitter.classList.remove('drag');
            splitter.removeEventListener('pointermove', move);
            splitter.removeEventListener('pointerup', up);
            applyLayout(layout); // 저장
        };
        splitter.addEventListener('pointermove', move);
        splitter.addEventListener('pointerup', up);
    });
}
dragSplitter($('split-side'), ev => {
    const actW = $('activity').offsetWidth;
    const w = layout.sidePosition === 'right' ? window.innerWidth - ev.clientX : ev.clientX - actW;
    layout.sideWidth = Math.round(Math.min(Math.max(w, 200), window.innerWidth * 0.6));
    document.documentElement.style.setProperty('--side-w', `${layout.sideWidth}px`);
});
dragSplitter($('split-bottom'), ev => {
    const rect = $('main').getBoundingClientRect();
    layout.bottomHeight = Math.round(Math.min(Math.max(rect.bottom - ev.clientY, 60), rect.height * 0.7));
    document.documentElement.style.setProperty('--bottom-h', `${layout.bottomHeight}px`);
});

// 🧩 화면 배치 모드: 영역마다 번호 표시 + 설정 패널 + "배치 설명 복사"
export function describeLayout(l = layout) {
    return [
        '[업무 데스크 화면 배치]',
        `② 목록 패널: ${l.sidePosition === 'right' ? '오른쪽' : '왼쪽'}, 너비 ${l.sideWidth}px`,
        `⑤ 하단 패널: ${l.bottomVisible ? `보임, 높이 ${l.bottomHeight}px` : '숨김'}`,
        `① 활동 막대 글자: ${l.labels ? '보임' : '숨김'}`,
        `글자 크기: ${Math.round(l.fontScale * 100)}%, 간격: ${l.compact ? '촘촘하게' : '넉넉하게'}`,
        `색상: ${{ auto: '시스템 따라', light: '밝게', dark: '어둡게' }[l.theme]}`,
        `창 크기: ${window.innerWidth}×${window.innerHeight}`,
        '바라는 점: (여기에 적어 주세요. 예: ④ 작업 영역의 오늘 카드를 2줄로)',
    ].join('\n');
}

export function toggleLayoutMode() {
    const open = document.body.classList.toggle('layout-edit');
    $('layout-panel')?.remove();
    if (!open) return;
    const panel = el('div#layout-panel');
    const render = () => {
        const l = getLayout();
        const seg = (key, options) => el('div.seg', options.map(([v, label]) =>
            el('button', { className: l[key] === v ? 'on' : '', onclick: () => { setLayout({ [key]: v }); render(); } }, label)));
        clear(panel,
            el('h3', '화면 배치'),
            el('p.small.muted', '점선 영역의 번호(①~⑥)로 부르면 바꾸고 싶은 곳을 정확히 전달할 수 있습니다. 경계선을 끌어 크기를 조절하세요.'),
            el('div.field', el('span', '② 목록 패널 위치'), seg('sidePosition', [['left', '왼쪽'], ['right', '오른쪽']])),
            el('div.field', el('span', '⑤ 하단 패널(기록)'), seg('bottomVisible', [[true, '보이기'], [false, '숨기기']])),
            el('div.field', el('span', '① 활동 막대 글자'), seg('labels', [[true, '보이기'], [false, '아이콘만']])),
            el('div.field', el('span', '글자 크기'), seg('fontScale', [[0.9, '작게'], [1, '보통'], [1.15, '크게'], [1.3, '아주 크게']])),
            el('div.field', el('span', '간격'), seg('compact', [[false, '넉넉하게'], [true, '촘촘하게']])),
            el('div.field', el('span', '색상'), seg('theme', [['auto', '자동'], ['light', '밝게'], ['dark', '어둡게']])),
            el('div.row',
                el('button.btn', { onclick: async () => {
                    const text = describeLayout();
                    try {
                        await navigator.clipboard.writeText(text);
                        toast('배치 설명을 복사했습니다. Claude Code 에게 붙여넣고 바라는 점을 적어 주세요.');
                    } catch (e) {
                        prompt('아래 글을 복사하세요', text);
                    }
                } }, '배치 설명 복사'),
                el('button.btn', { onclick: () => { applyLayout(DEFAULT_LAYOUT); render(); } }, '처음대로'),
                el('button.btn.primary', { onclick: toggleLayoutMode }, '완료')));
    };
    render();
    document.body.append(panel);
}
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.body.classList.contains('layout-edit')) toggleLayoutMode();
});
