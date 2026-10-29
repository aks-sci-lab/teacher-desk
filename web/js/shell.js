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
let defaultViewOrder = [];
let draggingViewId = null;

export const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export const on = (name, fn) => bus.addEventListener(name, e => fn(e.detail));

// ---------------------------------------------------------------- 활동 막대·목록 패널
// view = { id, icon, label, bottom?, noSide?(목록 패널 없이 작업 영역만), renderSide(body, actions), open?() }
export function registerView(view) {
    views.set(view.id, view);
    const label = menuLabel(view);
    const btn = el('button.act', {
        title: label, 'aria-label': label, draggable: true, dataset: { view: view.id },
        onclick: () => showView(view.id, true),
        ondragstart: e => {
            if (!document.body.classList.contains('layout-edit')) {
                e.preventDefault();
                return;
            }
            draggingViewId = view.id;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', view.id);
            btn.classList.add('dragging');
        },
        ondragover: e => {
            const source = views.get(draggingViewId);
            if (!source || source.button.parentElement !== btn.parentElement || source === view) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            btn.classList.toggle('drop-after', e.clientY > btn.getBoundingClientRect().top + btn.offsetHeight / 2);
            btn.classList.toggle('drop-before', !btn.classList.contains('drop-after'));
        },
        ondragleave: () => btn.classList.remove('drop-before', 'drop-after'),
        ondrop: e => {
            const sourceId = e.dataTransfer.getData('text/plain') || draggingViewId;
            const after = btn.classList.contains('drop-after');
            e.preventDefault();
            btn.classList.remove('drop-before', 'drop-after');
            moveView(sourceId, view.id, after);
        },
        ondragend: () => {
            draggingViewId = null;
            for (const item of views.values()) item.button.classList.remove('dragging', 'drop-before', 'drop-after');
        },
    },
        el('span.ico', icon(view.id) || view.icon), el('span.lbl', label), el('span.badge', { hidden: true }));
    $(view.bottom ? 'activity-bottom' : 'activity-top').append(btn);
    view.button = btn;
}

// 활동 막대 버튼 순서 정하기 (모듈을 불러오는 순서와 상관없이)
export function orderViews(ids) {
    defaultViewOrder = ids.filter(id => views.has(id));
    applyViewOrder(layout.viewOrder);
}

function normalizedViewOrder(order) {
    const known = Array.isArray(order) ? order.filter((id, i) => views.has(id) && order.indexOf(id) === i) : [];
    return [...known, ...defaultViewOrder.filter(id => !known.includes(id))];
}

function applyViewOrder(order) {
    const ids = normalizedViewOrder(order);
    for (const parent of [$('activity-top'), $('activity-bottom')]) {
        for (const id of ids) {
            const view = views.get(id);
            if (view?.button.parentElement === parent) parent.append(view.button);
        }
    }
}

function moveView(sourceId, targetId, after) {
    const source = views.get(sourceId);
    const target = views.get(targetId);
    if (!source || !target || source === target || source.button.parentElement !== target.button.parentElement) return;
    const ids = normalizedViewOrder(layout.viewOrder);
    ids.splice(ids.indexOf(sourceId), 1);
    const targetIndex = ids.indexOf(targetId);
    ids.splice(targetIndex + Number(after), 0, sourceId);
    setLayout({ viewOrder: ids });
}

function menuLabel(view) {
    const custom = layout?.menuLabels?.[view.id];
    return typeof custom === 'string' && custom.trim() ? custom.trim().slice(0, 20) : view.label;
}

function applyMenuLabels() {
    for (const view of views.values()) {
        const label = menuLabel(view);
        view.button.title = label;
        view.button.setAttribute('aria-label', label);
        view.button.querySelector('.lbl').textContent = label;
    }
    if (currentView) $('side-title').textContent = menuLabel(views.get(currentView));
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
    $('side-title').textContent = menuLabel(view);
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
    menuLabels: {},
    viewOrder: [],
};
let layout = { ...DEFAULT_LAYOUT };
let saveTimer;

export function getLayout() {
    return { ...layout };
}

export function applyLayout(next, save = true) {
    layout = { ...DEFAULT_LAYOUT, ...next };
    layout.fontScale = Math.min(1.5, Math.max(0.8, Number(layout.fontScale) || DEFAULT_LAYOUT.fontScale));
    const root = document.documentElement;
    app.classList.toggle('side-right', layout.sidePosition === 'right');
    app.classList.toggle('side-left', layout.sidePosition !== 'right');
    app.classList.toggle('bottom-hidden', !layout.bottomVisible);
    app.classList.toggle('hide-labels', !layout.labels);
    root.classList.toggle('compact', !!layout.compact);
    root.style.setProperty('--side-w', `${layout.sideWidth}px`);
    root.style.setProperty('--bottom-h', `${layout.bottomHeight}px`);
    root.style.setProperty('--font-scale', layout.fontScale);
    applyMenuLabels();
    applyViewOrder(layout.viewOrder);
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
        `메뉴 순서: ${normalizedViewOrder(l.viewOrder).map(id => menuLabel(views.get(id))).join(' → ')}`,
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
        const viewOrder = normalizedViewOrder(l.viewOrder);
        const updateMenuLabel = (id, value) => {
            const menuLabels = { ...(layout.menuLabels || {}) };
            const text = value.trim().slice(0, 20);
            if (text) menuLabels[id] = text;
            else delete menuLabels[id];
            setLayout({ menuLabels });
        };
        const seg = (key, options) => el('div.seg', options.map(([v, label]) =>
            el('button', { className: l[key] === v ? 'on' : '', onclick: () => { setLayout({ [key]: v }); render(); } }, label)));
        const fontScaleLabel = el('output', `${Math.round(l.fontScale * 100)}%`);
        const menuRows = viewOrder.map(id => {
            const view = views.get(id);
            return el('div.layout-menu-row',
                el('label', { className: 'layout-menu-name' },
                    el('span', view.label),
                    el('input', {
                        type: 'text', maxlength: 20, value: l.menuLabels?.[id] || '',
                        placeholder: view.label, 'aria-label': `${view.label} 메뉴 이름`,
                        oninput: e => updateMenuLabel(id, e.target.value),
                    })));
        });
        clear(panel,
            el('h3', '화면 배치'),
            el('p.small.muted', '점선 영역의 번호(①~⑥)로 부르면 바꾸고 싶은 곳을 정확히 전달할 수 있습니다. 경계선을 끌어 크기를 조절하세요.'),
            el('div.field', el('span', '② 목록 패널 위치'), seg('sidePosition', [['left', '왼쪽'], ['right', '오른쪽']])),
            el('div.field', el('span', '⑤ 하단 패널(기록)'), seg('bottomVisible', [[true, '보이기'], [false, '숨기기']])),
            el('div.field', el('span', '① 활동 막대 글자'), seg('labels', [[true, '보이기'], [false, '아이콘만']])),
            el('label.field.layout-font', el('span', '글자 크기'), fontScaleLabel,
                el('input', {
                    type: 'range', min: 0.8, max: 1.5, step: 0.05, value: l.fontScale,
                    'aria-label': '글자 크기',
                    oninput: e => {
                        const fontScale = Math.round(Number(e.target.value) * 100) / 100;
                        fontScaleLabel.value = `${Math.round(fontScale * 100)}%`;
                        fontScaleLabel.textContent = fontScaleLabel.value;
                        setLayout({ fontScale });
                    },
                })),
            el('div.field', el('span', '간격'), seg('compact', [[false, '넉넉하게'], [true, '촘촘하게']])),
            el('div.field', el('span', '색상'), seg('theme', [['auto', '자동'], ['light', '밝게'], ['dark', '어둡게']])),
            el('div.field.layout-menus', el('span', '메뉴 이름과 순서'),
                el('p.small.muted', '메뉴 순서는 왼쪽 메뉴를 같은 묶음 안에서 끌어 바꾸세요. 이름을 비우면 원래대로 돌아옵니다.'),
                ...menuRows),
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
