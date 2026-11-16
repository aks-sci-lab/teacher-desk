// 설정 칸 부품 (0.5.79): ⚙ 설정 화면과 첫 실행 설정 도우미가 함께 씀
// - 칸을 고치고 다른 곳을 누르면(또는 Enter) 그 칸만 바로 저장 (autoSave)
// - 잘못된 값은 칸 바로 아래에 빨간 글로 안내, 저장 상태는 화면 위 '저장됨 ✓' 로 표시
// - 비밀값(텔레그램 토큰·나이스 인증키)은 화면에 다시 보여 주지 않음: '설정됨'만, 바꿀 때 입력 후 [저장]
import { api } from '../api.js';
import { el, clear, toast, guarded } from '../ui.js';
import { store, loadState, saveSettings } from '../store.js';
import { checkValue } from '../setup-rules.js';

// 검색·설명이 붙는 한 칸 묶음. search 에는 이름·설명 글자를 넣어 검색 칸으로 찾게 함
export function item(search, ...children) {
    return el('div.set-item', { dataset: { search: String(search || '').toLowerCase() } }, children, el('p.set-err', { role: 'alert' }));
}

export function textField(key, label, hint, attrs = {}) {
    const input = el('input', { type: 'text', value: store.settings[key] ?? '', ...attrs });
    input.dataset.key = key;
    return item(`${label} ${hint || ''}`, el('label.field', el('span', label), input, hint && el('span.hint', hint)));
}

// 경로 칸 + 찾아보기 (윈도우 찾아보기 창으로 고르면 칸에 채워지고 바로 저장)
export function pathField(key, label, hint, purpose, placeholder = '', extra = null) {
    const input = el('input', { type: 'text', value: store.settings[key] ?? '', placeholder, style: 'flex: 1' });
    input.dataset.key = key;
    const browse = el('button.btn.small', { type: 'button', onclick: guarded(async () => {
        browse.disabled = true;
        toast('찾아보기 창을 열었습니다. 작업 표시줄에서 창을 확인하세요.');
        try {
            const r = await api.post('/api/pick', { purpose });
            if (r.path) {
                input.value = r.path;
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
        } finally {
            browse.disabled = false;
        }
    }) }, '찾아보기');
    return item(`${label} ${hint || ''}`, el('div.field', el('span', label), el('div.row', input, browse), hint && el('span.hint', hint)), extra);
}

// 켬/끔 칸
export function checkField(key, label, hint) {
    const input = el('input', { type: 'checkbox', checked: !!store.settings[key] });
    input.dataset.key = key;
    return item(`${label} ${hint || ''}`, el('label.check', input, el('span', label), hint && el('span.hint', hint)));
}

export function selectField(key, label, options, hint, fallback) {
    const cur = store.settings[key] ?? fallback;
    const sel = el('select', { dataset: { key } }, options.map(([v, l]) => el('option', { value: v, selected: cur === v }, l)));
    return item(`${label} ${hint || ''} ${options.map(o => o[1]).join(' ')}`, el('label.field', el('span', label), sel, hint && el('span.hint', hint)));
}

// 비밀값 칸: 화면에 값을 돌려받지 않으므로 '설정됨' 만 보이고, 새 값을 넣고 [저장]을 누를 때만 바꿈
export function secretField(key, label, hint, emptyPlaceholder, after) {
    const isSet = () => !!store.settings[`${key}_set`];
    const input = el('input', { type: 'password', autocomplete: 'off', style: 'flex: 1',
        placeholder: isSet() ? '설정됨 (바꿀 때만 입력)' : emptyPlaceholder });
    const state = el('span.set-badge', isSet() ? '설정됨' : '비어 있음');
    const wrap = item(`${label} ${hint || ''}`);
    const save = el('button.btn.small.primary', { type: 'button', onclick: guarded(async () => {
        const v = input.value.trim();
        if (!v) { showError(wrap, '새 값을 입력한 뒤 [저장]을 누르세요.'); return; }
        markSaving(wrap.closest('.pane'));
        try {
            await saveSettings({ [key]: v });
        } catch (e) {
            showError(wrap, e.message);
            markSaved(wrap.closest('.pane'), e.message);
            return;
        }
        showError(wrap, '');
        input.value = '';
        input.placeholder = '설정됨 (바꿀 때만 입력)';
        state.textContent = '설정됨';
        markSaved(wrap.closest('.pane'));
        toast(`${label} 저장됨`, 'info', 1800);
        await loadState();
        after?.();
    }) }, '저장');
    input.addEventListener('keydown', e => { if (e.key === 'Enter') save.click(); });
    wrap.prepend(el('div.field', el('span', label, ' ', state), el('div.row', input, save), hint && el('span.hint', hint)));
    return wrap;
}

export function fieldValue(input) {
    if (input.type === 'checkbox') return input.checked;
    if (input.dataset.key === 'collect_times') return input.value.split(/[,，]/).map(t => t.trim()).filter(Boolean);
    return input.value;
}

export function showError(wrap, message) {
    const box = wrap?.querySelector(':scope > .set-err');
    if (box) box.textContent = message || '';
    wrap?.classList.toggle('bad', !!message);
}

// 화면 위 저장 상태 표시 (.save-state)
export function markSaving(pane) {
    const s = pane?.querySelector('.save-state');
    if (s) { s.textContent = '저장 중…'; s.className = 'save-state busy'; }
}
export function markSaved(pane, error = '') {
    const s = pane?.querySelector('.save-state');
    if (!s) return;
    if (error) { s.textContent = '⚠ 저장하지 못한 칸이 있습니다'; s.className = 'save-state err'; return; }
    const t = new Date();
    s.textContent = `저장됨 ✓ ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
    s.className = 'save-state ok';
}

export function saveStateBox() {
    return el('span.save-state', { role: 'status', 'aria-live': 'polite' }, '바꾸면 바로 저장됩니다');
}

// 칸을 고치면 그 칸만 바로 저장 (pane 하나에 한 번만 붙임)
export function autoSave(pane) {
    if (pane.dataset.autosave) return;
    pane.dataset.autosave = '1';
    pane.addEventListener('change', async ev => {
        const input = ev.target.closest?.('[data-key]');
        if (!input || !pane.contains(input)) return;
        const key = input.dataset.key;
        const wrap = input.closest('.set-item');
        const label = input.closest('.field, .check')?.querySelector('span')?.textContent || '설정';
        const value = fieldValue(input);
        const problem = checkValue(key, value);
        if (problem) {
            showError(wrap, problem);
            markSaved(pane, problem);
            return;
        }
        markSaving(pane);
        try {
            await saveSettings({ [key]: value });
        } catch (e) {
            showError(wrap, e.message);
            markSaved(pane, e.message);
            return;
        } finally {
            delete pane.dataset.dirty;
        }
        showError(wrap, '');
        markSaved(pane);
        if (key === 'collect_times') input.value = (store.settings.collect_times || []).join(', ');
        loadState();
        toast(`${label} 저장됨`, 'info', 1500);
    });
    pane.addEventListener('input', () => { pane.dataset.dirty = '1'; });
}

// ---- 연결 확인 부품 (예전 설정 화면의 확인 기능 그대로) ----

// 쿨메신저 이름 칸 + [연결 확인] + 결과
export function coolConnect() {
    const s = store.settings;
    const box = el('div');
    const list = el('datalist#cool-names');
    const name = el('input', { type: 'text', value: s.coolmessenger_name || '', placeholder: '쿨메신저에 로그인하는 이름 (예: 홍길동)',
        list: 'cool-names', dataset: { key: 'coolmessenger_name' } });
    guarded(async () => {
        const r = await api.get('/api/cool');
        clear(list, r.names.map(n => el('option', { value: n })));
        if (!box.childElementCount) {
            clear(box, el('p.small.muted', r.names.length
                ? `이 PC 에서 찾은 이름: ${r.names.join(', ')}`
                : `쿨메신저 쪽지 폴더를 찾지 못했습니다: ${r.dir}`));
        }
    })();
    const check = guarded(async () => {
        clear(box, el('p.small.muted', '확인 중…'));
        const path = document.querySelector('.pane.on [data-key="coolmessenger_path"]')?.value ?? (s.coolmessenger_path || '');
        let r;
        try {
            r = await api.post('/api/cool/check', { name: name.value, path });
        } catch (e) {
            clear(box, el('p.set-bad', e.message));
            return;
        }
        const d = r.detected;
        clear(box,
            el('p.small', `파일: ${r.path}`),
            d ? el('div',
                el('p.set-good', `연결됨 — 쪽지 ${d.rows.toLocaleString()}개`),
                el('p.small.muted', `읽는 곳: 표 ${d.table} · 내용 ${d.body}${d.title ? ' · 제목 ' + d.title : ''}${d.date ? ' · 날짜 ' + d.date : ' · 날짜 열 없음'}${d.sender ? ' · 보낸 사람 ' + d.sender : ''}`),
                r.sender_check && el('p.small', r.sender_check.me
                    ? (r.sender_check.mine
                        ? `내가 보낸 쪽지: 최근 ${r.sender_check.checked}개 중 ${r.sender_check.mine}개 (보낸 사람 열 ${r.sender_check.column} 에서 내 이름과 같은 것). 앞으로 이런 쪽지는 '쿨메신저 보냄' 으로 따로 모읍니다.`
                        : `보낸 사람 열(${r.sender_check.column})에서 내 이름과 같은 쪽지를 찾지 못했습니다 (서로 다른 보낸 사람 ${r.sender_check.kinds}명). 이 열에 이름이 아닌 번호가 들어 있을 수 있으니 아래 '파일 구조' 를 개발 담당에게 보내 주세요.`)
                    : '위의 쿨메신저 이름을 입력해야 내가 보낸 쪽지를 구분합니다.'),
                r.images && Object.keys(r.images.kinds).length
                    ? el('p.small.muted', `최근 쪽지의 사진: ${Object.entries(r.images.kinds).map(([k, n]) => `${k} ${n}장`).join(', ')}${r.images.hosts.length ? ' (' + r.images.hosts.join(', ') + ')' : ''}`)
                    : el('p.small.muted', '최근 쪽지 200개에서 사진을 찾지 못했습니다.'),
                el('p.small', '최근 쪽지:'),
                el('ul.small', r.recent.map(m => el('li', `${m.when || ''} ${m.sender ? '[' + m.sender + '] ' : ''}${m.title}`))),
                el('p.small.muted', '이름 칸은 이미 저장되었습니다. 처음 수집 때 최근 30일 쪽지를 가져옵니다.'))
              : el('p.set-bad', '쪽지 표를 찾지 못했습니다. 아래 구조를 복사해 개발 담당에게 보내 주세요.'),
            el('details', el('summary.small', '파일 구조 (내용 없음)'),
                el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(JSON.stringify(r.tables, null, 2)); toast('구조를 복사했습니다'); }) }, '복사'),
                el('pre.text', JSON.stringify(r.tables, null, 2))));
    });
    return item('쿨메신저 이름 쪽지 연결 확인 로그인',
        el('label.field', el('span', '쿨메신저 이름'), name, list,
            el('span.hint', '쿨메신저에 로그인하는 이름. 쪽지 기록 파일은 자동으로 찾습니다.')),
        el('div.row', el('button.btn.small', { type: 'button', onclick: check }, '연결 확인')),
        box);
}

// 공문·받은 파일 폴더 [폴더 확인] (source: edufine | cool)
export function folderCheck(source) {
    const box = el('div');
    const run = guarded(async () => {
        const key = source === 'edufine' ? 'edufine_folder' : 'coolmessenger_folder';
        const folder = document.querySelector(`.pane.on [data-key="${key}"]`)?.value || '';
        const r = await api.get(`/api/diagnose?source=${source}&folder=${encodeURIComponent(folder)}`);
        if (r.error) return clear(box, el('p.set-bad', r.error));
        clear(box,
            el('p', { style: 'font-weight: 700' }, `${r.folder}: 파일 ${r.total}개 (읽는 형식 ${r.supported}개)`),
            r.cloud_only
                ? el('div',
                    el('p.set-good', `이 PC 에 있는 공문 파일 ${r.local_supported ?? 0}개는 그대로 읽습니다. 새로 저장하는 공문도 읽습니다.`),
                    el('p.set-bad', `나머지 ${r.cloud_only}개는 ${r.cloud_hint}`),
                    el('p.small.muted', '읽을 수 없는 파일은 한 번 기록해 두고 다시 시도하지 않으므로 업무 데스크가 느려지지는 않습니다.'))
                : el('p.small.set-good', '모든 파일이 이 PC 에 있습니다.'),
            !r.saved ? el('p.small', { style: 'color: var(--warn)' }, '아직 저장되지 않은 경로입니다. 칸을 고친 뒤 다른 곳을 누르면 저장됩니다.') : '',
            el('details', el('summary.small', '자세한 결과 (개발 담당용)'),
                el('p.small', '아래 결과에는 메시지 내용이 없고 파일 이름·형식만 있습니다.'),
                el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(JSON.stringify(r, null, 2)); toast('진단 결과를 복사했습니다'); }) }, '결과 복사'),
                el('pre.text', JSON.stringify(r, null, 2))));
    });
    return el('div', el('div.row', el('button.btn.small', { type: 'button', onclick: run }, '폴더 확인')), box);
}

// 텔레그램 연결 상태 + 연결 코드 + 시험 알림 (상태가 바뀌면 그 자리만 다시 그림)
export function telegramStatus() {
    const box = el('div.tg-status');
    const act = action => guarded(async () => {
        await api.post('/api/telegram', { action });
        await loadState();
        if (action === 'test') toast('시험 알림을 보냈습니다');
        draw();
    });
    const draw = () => {
        const s = store.settings;
        const tg = store.state?.telegram || {};
        box.dataset.sig = `${tg.linked}|${tg.status}|${s.telegram_token_set}|${tg.pair_code}`;
        clear(box,
            el('p', '상태: ', el('b', tg.status || '-'), tg.last_poll ? ` · 마지막 확인 ${tg.last_poll}` : '',
                tg.linked && s.telegram_token_set && tg.alive === false ? ' · ⚠ 수신이 멈춤 (앱을 다시 켜 보세요)' : '',
                tg.linked ? ' · ✅ 연결됨' : ''),
            !tg.linked && s.telegram_token_set && tg.pair_code && el('p', '만든 봇에게 이 글을 보내세요: ',
                el('code.set-code', `/start ${tg.pair_code}`)),
            tg.last_error ? el('p.small.muted', `최근 오류: ${tg.last_error}`) : null,
            el('div.row',
                tg.linked && el('button.btn.small', { type: 'button', onclick: act('test') }, '시험 알림'),
                tg.linked && el('button.btn.small.danger', { type: 'button', onclick: act('unpair') }, '연결 끊기'),
                !tg.linked && s.telegram_token_set && el('button.btn.small', { type: 'button', onclick: act('renew') }, '코드 새로 만들기')));
    };
    box.redraw = draw;
    draw();
    return box;
}

// 화면에 있는 텔레그램 상태 칸을 상태가 바뀔 때만 다시 그림 (입력 중인 칸은 건드리지 않음)
export function refreshTelegramBoxes() {
    for (const box of document.querySelectorAll('.tg-status')) {
        const s = store.settings;
        const tg = store.state?.telegram || {};
        if (box.dataset.sig !== `${tg.linked}|${tg.status}|${s.telegram_token_set}|${tg.pair_code}`) box.redraw?.();
    }
}

// LM Studio [연결 확인]
export function aiCheck() {
    const box = el('div');
    const run = guarded(async () => {
        clear(box, el('p.small.muted', '확인 중…'));
        try {
            const base = document.querySelector('.pane.on [data-key="ai_base"]')?.value || store.settings.ai_base;
            const r = await api.post('/api/ai/check', { base });
            clear(box, el('p.set-good', 'LM Studio 연결됨'),
                el('p.small', `불러온 모델: ${r.models.join(', ')}`),
                el('p.small.muted', '모델 이름 칸을 비워 두면 첫 모델을 씁니다.'),
                r.ttl_unsupported && el('p.small.set-bad', '이 서버는 자동 해제(ttl)를 지원하지 않아 모델이 메모리에 계속 남습니다. LM Studio를 최신 버전으로 올리면 해결될 수 있습니다.'));
        } catch (e) {
            clear(box, el('p.set-bad', e.message));
        }
    });
    return el('div', el('div.row', el('button.btn.small', { type: 'button', onclick: run }, 'LM Studio 연결 확인')), box);
}

// 0.5.98 자동 AI 요약을 돌리는 때 (내 수업 시간) — '다음 자동 요약: 오늘 3교시 (10:50)'
export function aiWindowLine() {
    const box = el('p.small.muted', '자동 요약 시간 확인 중…');
    api.get('/api/ai/auto-window').then(w => {
        clear(box, w.text, w.pending ? ` · 요약을 기다리는 쪽지·공문 ${w.pending}건` : '');
        box.className = `small ${w.mode === 'no_timetable' ? 'set-bad' : 'muted'}`;
    }).catch(() => clear(box, ''));
    return box;
}

export const TELEGRAM_STEPS = () => el('ol.small.set-steps',
    el('li', '텔레그램에서 @BotFather 를 찾아 /newbot 으로 봇을 만들고 토큰을 받습니다.'),
    el('li', '아래 봇 토큰 칸에 넣고 [저장]을 누릅니다.'),
    el('li', '화면에 나오는 연결 코드를 만든 봇에게 보내면 선생님 계정만 연결됩니다.'));
