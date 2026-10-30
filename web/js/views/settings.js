// ⚙ 설정: 학교·학급, 수집 폴더·시각, 텔레그램, 공유 페이지
import { api } from '../api.js';
import { el, clear, toast, guarded, download } from '../ui.js';
import { registerView, openTab, findTab, on, toggleLayoutMode } from '../shell.js';
import { store, loadState, saveSettings } from '../store.js';
import { openAI } from './ai.js';
import { checkUpdate, startUpdate } from '../update.js';

function textField(key, label, hint, attrs = {}) {
    const input = el('input', { type: 'text', value: store.settings[key] ?? '', ...attrs });
    input.dataset.key = key;
    return el('label.field', el('span', label), input, hint && el('span.hint', hint));
}

// 경로 칸 + 📂 찾아보기 (윈도우 찾아보기 창으로 고르면 칸에 채워짐)
function pathField(key, label, hint, purpose, placeholder = '') {
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
    return el('div.field', el('span', label), el('div.row', input, browse), hint && el('span.hint', hint));
}

// 켬/끔 칸 (저장할 때 collectForm 이 checked 를 읽음)
function checkField(key, label, hint) {
    const input = el('input', { type: 'checkbox', checked: !!store.settings[key] });
    input.dataset.key = key;
    return el('label.check', input, label, hint && el('span.hint', hint));
}

function section(title, ...children) {
    return el('section.card', { style: 'margin-bottom: 16px' }, el('h3', title), children);
}

function aiSetup(pane) {
    const result = el('div');
    const check = el('button.btn', { onclick: guarded(async () => {
        check.disabled = true;
        clear(result, el('p.muted', '이 PC의 CPU·메모리·그래픽과 여유 공간을 확인하는 중…'));
        try {
            const spec = await api.post('/api/ai/setup/spec');
            const recommendation = spec.recommendation;
            const model = el('select', recommendation.models.map(m => el('option', {
                value: m.id, disabled: !m.eligible, selected: m.id === recommendation.selected,
            }, `${m.name}${m.eligible ? '' : ' — ' + m.reasons.join(', ')}`)));
            const port = el('input', { type: 'number', min: 1024, max: 65535, value: 1234 });
            const notice = el('p.small');
            const selectedModel = () => recommendation.models.find(m => m.id === model.value);
            const explain = () => { notice.textContent = selectedModel()?.description || ''; };
            model.onchange = explain;
            explain();
            const message = el('div');
            const apply = el('button.btn', { disabled: !recommendation.selected, onclick: guarded(async () => {
                const selection = { model: model.value, port: Number(port.value) };
                apply.disabled = true;
                model.disabled = true;
                port.disabled = true;
                pane.dataset.setupBusy = '1';
                clear(message, el('p.muted', '로컬 모델의 짧은 답변까지 확인하는 중… 모델에 따라 1~2분 걸릴 수 있습니다. 학교 자료는 보내지 않습니다.'));
                try {
                    const r = await api.post('/api/ai/setup/apply', selection);
                    for (const [key, value] of Object.entries({
                        ai_base: r.settings.ai_base, ai_model: r.settings.ai_model, ai_input_size: r.settings.ai_input_size,
                    })) {
                        const input = pane.querySelector(`[data-key="${key}"]`);
                        if (input) input.value = value;
                    }
                    pane.querySelector('[data-key="ai_enabled"]').checked = true;
                    await loadState();
                    clear(message, el('p', '연결과 설정 저장을 마쳤습니다. 브리핑에서 요약하거나 텔레그램으로 질문하세요.'));
                } catch (e) {
                    clear(message, el('p', { style: 'color: var(--danger)' }, e.message));
                } finally {
                    apply.disabled = false;
                    model.disabled = false;
                    port.disabled = false;
                    delete pane.dataset.setupBusy;
                }
            }) }, '3. 연결 확인 후 적용');
            clear(result,
                el('p.small', `CPU: ${spec.cpu} · ${spec.cores}개 논리 코어`),
                el('p.small', `메모리 ${spec.ram_gb}GB / 현재 사용 가능 ${spec.free_gb}GB · 남은 디스크 ${spec.disk_gb}GB`),
                el('p.small', `그래픽: ${(spec.gpus || []).join(', ') || '확인 안 됨'}`),
                el('p.small', spec.lms_installed ? 'LM Studio 명령 도구가 있습니다. 기존 설치를 재사용합니다.' : 'LM Studio가 없으면 배치파일에서 설치 여부를 먼저 묻습니다.'),
                el('label.field', el('span', '추천 모델'), model), notice,
                !recommendation.selected && el('p', '현재 여유 사양으로는 권장할 모델이 없습니다. 다른 프로그램을 닫고 다시 확인하거나 기존 AI 설정을 사용하세요.'),
                el('label.field', el('span', 'AI 서버 포트 (업무 데스크와 달라야 함)'), port),
                el('p.small.muted', recommendation.note),
                el('button.btn', { disabled: !recommendation.selected, onclick: guarded(async () => {
                    const res = await api.post('/api/ai/setup/batch', { model: model.value, port: Number(port.value) });
                    download(await res.blob(), 'setup-local-ai.bat');
                    clear(message, el('p', '받은 배치파일을 더블클릭하세요. 모델은 수 GB라 오래 걸릴 수 있습니다. 설치 직후에는 LM Studio를 한 번 열어 초기 설정을 마친 뒤 배치파일을 다시 실행하세요.'));
                }) }, '2. 설치·실행 배치파일 받기'), apply, message);
        } catch (e) {
            clear(result, el('p', { style: 'color: var(--danger)' }, e.message));
        } finally {
            check.disabled = false;
        }
    }) }, '1. PC 사양 확인·모델 추천');
    return el('div', el('p.small', '다운로드할 때만 인터넷을 사용합니다. 학교 자료는 이 PC의 AI로만 보냅니다. 기존 AI 설정은 마지막 [적용] 전까지 바꾸지 않습니다.'), check, result);
}

function fieldValue(input) {
    if (input.type === 'checkbox') return input.checked;
    if (input.dataset.key === 'collect_times') return input.value.split(',').map(t => t.trim()).filter(Boolean);
    return input.value;
}

function collectForm(pane) {
    const out = {};
    for (const input of pane.querySelectorAll('[data-key]')) out[input.dataset.key] = fieldValue(input);
    return out;
}

// 칸을 고치고 다른 곳을 누르면(또는 Enter) 그 칸만 바로 저장 — [설정 저장] 을 잊어도 남게 (사용자 지적: 저장 안 됨)
function autoSave(pane) {
    if (pane.dataset.autosave) return;   // 다시 그려도 같은 pane 이라 한 번만
    pane.dataset.autosave = '1';
    pane.addEventListener('change', guarded(async ev => {
        const input = ev.target.closest?.('[data-key]');
        if (!input || !pane.contains(input)) return;
        const key = input.dataset.key;
        const label = input.closest('.field, .check')?.querySelector('span')?.textContent || '설정';
        try {
            await saveSettings({ [key]: fieldValue(input) });
        } finally {
            delete pane.dataset.dirty;
        }
        loadState();
        toast(`${label} 저장됨`, 'info', 1800);
    }));
    pane.addEventListener('input', () => { pane.dataset.dirty = '1'; });
}

// 업데이트: 지금 버전·배포된 최신 버전·[새 버전 확인]·[지금 업데이트] (프로그램을 따로 끌 필요 없음)
function updateBox(st) {
    const box = el('div');
    const draw = u => {
        clear(box,
            el('p.small', `지금 버전: ${st.version || ''}` + (u?.latest ? ` · 배포된 최신: ${u.latest}` : '')
                + (u?.checked_at ? ` · 확인 ${u.checked_at}` : '')),
            u?.error && el('p.small', { style: 'color: var(--danger)' }, u.error),
            u?.available
                ? el('p.small', { style: 'color: var(--ok); font-weight: 700' }, `새 버전 ${u.latest} 이 있습니다.`)
                : (u?.checked_at && !u.error && el('p.small', { style: 'color: var(--ok)' }, '최신 버전입니다.')),
            u && !u.portable && el('p.small.muted', '개발용 폴더라 화면에서 업데이트할 수 없습니다 (git 으로 받으세요).'),
            el('div.row',
                el('button.btn.small', { onclick: guarded(async () => draw(await checkUpdate())) }, '새 버전 확인'),
                u?.available && u.portable && el('button.btn.small.primary', { onclick: startUpdate }, '지금 업데이트')));
    };
    draw(store.state?.update);
    return box;
}

// 브라우저 단축키로 화면 보내기: 연결 켜기·연결 키·확장 프로그램 설치 안내
function captureBox() {
    const box = el('div');
    const act = action => guarded(async () => { draw(await api.post('/api/capture', { action })); });
    const draw = c => {
        clear(box,
            c.enabled
                ? el('div',
                    el('p', { style: 'color: var(--ok); font-weight: 700' }, '✅ 연결이 켜져 있습니다'),
                    el('label.field', el('span', '연결 키 (확장 프로그램 옵션에 붙여 넣기)'),
                        el('input', { type: 'text', readonly: true, value: c.key, onclick: ev => ev.target.select() }),
                        el('span.hint', `업무 데스크 주소: ${c.url} — 이 키를 아는 프로그램만 화면을 보낼 수 있습니다. 다른 사람에게 알려 주지 마세요.`)),
                    el('div.row',
                        el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(c.key); toast('연결 키를 복사했습니다'); }) }, '키 복사'),
                        el('button.btn.small', { onclick: act('renew') }, '키 새로 만들기'),
                        el('button.btn.small.danger', { onclick: act('disable') }, '연결 끄기')),
                    el('ol.small', { style: 'margin: 12px 0 0; padding-left: 1.2em' },
                        el('li', '크롬 또는 엣지 주소창에  chrome://extensions  (엣지는 edge://extensions) 를 입력해 엽니다.'),
                        el('li', '오른쪽 위 "개발자 모드"를 켭니다.'),
                        el('li', ['"압축해제된 확장 프로그램을 로드합니다"(엣지: "압축 풀린 항목 로드")를 누르고 이 폴더를 고릅니다: ',
                            el('code', c.folder)]),
                        el('li', '확장 프로그램 "업무 데스크로 보내기"의 옵션(세부 정보 → 확장 프로그램 옵션)에 위 연결 키를 붙여 넣고 [저장하고 연결 확인]을 누릅니다.'),
                        el('li', '에듀파인 화면에서 Alt+Shift+S 를 누르면 지금 보이는 글이 수신함(출처: 에듀파인)으로 들어옵니다. 글을 드래그로 고른 뒤 누르면 고른 글만 보냅니다.')),
                    !c.folder_exists && el('p.small', { style: 'color: var(--danger)' }, '확장 프로그램 폴더를 찾지 못했습니다. 업데이트 뒤에 다시 확인하세요.'))
                : el('div',
                    el('p.small', '에듀파인(*.use.go.kr) 화면에서 단축키를 누르면 지금 보이는 글을 이 PC 의 업무 데스크로 보내 수신함에 넣고, 날짜·마감을 찾습니다. 읽은 글은 이 PC 밖으로 나가지 않습니다. 켜기 전에는 어떤 화면도 받지 않습니다.'),
                    el('div.row', el('button.btn.small.primary', { onclick: act('enable') }, '연결 켜기'))));
    };
    guarded(async () => draw(await api.get('/api/capture')))();
    return box;
}

function renderSettings(pane, tab) {
    const s = store.settings;
    const st = store.state || {};
    const tg = st.telegram || {};
    const detail = el('select', { dataset: { key: 'telegram_detail' } },
        [['counts', '개수만 (가장 안전)'], ['titles', '제목까지 (기본)'], ['summary', '요약까지']]
            .map(([v, l]) => el('option', { value: v, selected: s.telegram_detail === v }, l)));
    const neisKey = el('input', { type: 'password', placeholder: s.neis_key_set ? '설정됨 (바꿀 때만 입력)' : 'open.neis.go.kr 에서 받은 인증키', autocomplete: 'off' });
    const token = el('input', { type: 'password', placeholder: s.telegram_token_set ? '설정됨 (바꿀 때만 입력)' : 'BotFather 가 준 토큰', autocomplete: 'off' });
    const diagBox = el('div');
    const aiBox = el('div');
    const aiCheck = guarded(async () => {
        clear(aiBox, el('p.small.muted', '확인 중…'));
        try {
            const base = pane.querySelector('[data-key="ai_base"]').value;
            const r = await api.post('/api/ai/check', { base });
            clear(aiBox, el('p', { style: 'color: var(--ok); font-weight: 700' }, 'LM Studio 연결됨'),
                el('p.small', `불러온 모델: ${r.models.join(', ')}`),
                el('p.small.muted', '모델 이름 칸을 비워 두면 첫 모델을 씁니다.'));
        } catch (e) {
            clear(aiBox, el('p', { style: 'color: var(--danger)' }, e.message));
        }
    });
    const coolBox = el('div');
    const coolList = el('datalist#cool-names');
    const coolPathField = pathField('coolmessenger_path', '(선택) 쪽지 파일 직접 지정',
        '쿨메신저를 다른 곳에 설치했거나 이름으로 찾지 못할 때 .udb 파일을 직접 고르세요. 지정하면 이름보다 우선합니다.',
        'cool', '예: D:\\CoolMessenger\\Memo\\홍길동.udb');
    const coolPath = coolPathField.querySelector('input');
    const coolName = el('input', { type: 'text', value: s.coolmessenger_name || '', placeholder: '쿨메신저에 로그인하는 이름 (예: 홍길동)',
        list: 'cool-names', dataset: { key: 'coolmessenger_name' } });
    // 이 PC 의 쿨메신저 Memo 폴더에서 찾은 이름을 보기로 보여 줌
    guarded(async () => {
        const r = await api.get('/api/cool');
        clear(coolList, r.names.map(n => el('option', { value: n })));
        coolBox.dataset.dir = r.dir;
        if (!coolBox.childElementCount) {
            clear(coolBox, el('p.small.muted', r.names.length
                ? `이 PC 에서 찾은 이름: ${r.names.join(', ')}`
                : `쿨메신저 쪽지 폴더를 찾지 못했습니다: ${r.dir}`));
        }
    })();
    const coolCheck = guarded(async () => {
        clear(coolBox, el('p.small.muted', '확인 중…'));
        let r;
        try {
            r = await api.post('/api/cool/check', { name: coolName.value, path: coolPath.value });
        } catch (e) {
            clear(coolBox, el('p', { style: 'color: var(--danger)' }, e.message));
            return;
        }
        const d = r.detected;
        clear(coolBox,
            el('p.small', `파일: ${r.path}`),
            d ? el('div',
                el('p', { style: 'color: var(--ok); font-weight: 700' }, `연결됨 — 쪽지 ${d.rows.toLocaleString()}개`),
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
                el('p.small.muted', '맞으면 아래 "설정 저장"을 누르세요. 처음 수집 때 최근 30일 쪽지를 가져옵니다.'))
              : el('p', { style: 'color: var(--danger)' }, '쪽지 표를 찾지 못했습니다. 아래 구조를 복사해 개발 담당에게 보내 주세요.'),
            el('details', el('summary.small', '파일 구조 (내용 없음)'),
                el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(JSON.stringify(r.tables, null, 2)); toast('구조를 복사했습니다'); }) }, '복사'),
                el('pre.text', JSON.stringify(r.tables, null, 2))));
    });

    const save = guarded(async () => {
        const change = collectForm(pane);
        if (token.value.trim()) change.telegram_token = token.value.trim();
        if (neisKey.value.trim()) change.neis_key = neisKey.value.trim();
        await saveSettings(change);
        delete pane.dataset.dirty;
        await loadState();
        toast('설정을 저장했습니다');
        tab.rerender();
    });
    const tgAction = action => guarded(async () => {
        await api.post('/api/telegram', { action });
        await loadState();
        if (action === 'test') toast('시험 알림을 보냈습니다');
        tab.rerender();
    });
    const diagnose = source => guarded(async () => {
        const key = source === 'edufine' ? 'edufine_folder' : 'coolmessenger_folder';
        const folder = pane.querySelector(`[data-key="${key}"]`)?.value || '';
        const r = await api.get(`/api/diagnose?source=${source}&folder=${encodeURIComponent(folder)}`);
        if (r.error) return clear(diagBox, el('p', { style: 'color: var(--danger)' }, r.error));
        clear(diagBox,
            el('p', { style: 'font-weight: 700' }, `${r.folder}: 파일 ${r.total}개 (읽는 형식 ${r.supported}개)`),
            r.cloud_only
                ? el('div',
                    el('p', { style: 'color: var(--ok)' }, `이 PC 에 있는 공문 파일 ${r.local_supported ?? 0}개는 그대로 읽습니다. 새로 저장하는 공문도 읽습니다.`),
                    el('p', { style: 'color: var(--danger)' }, `나머지 ${r.cloud_only}개는 ${r.cloud_hint}`),
                    el('p.small.muted', '읽을 수 없는 파일은 한 번 기록해 두고 다시 시도하지 않으므로 업무 데스크가 느려지지는 않습니다.'))
                : el('p.small', { style: 'color: var(--ok)' }, '모든 파일이 이 PC 에 있습니다.'),
            !r.saved ? el('p.small', { style: 'color: var(--warn)' }, '아직 저장하지 않은 경로입니다. 아래 💾 설정 저장을 눌러야 이 폴더를 수집합니다.') : '',
            el('details', el('summary.small', '자세한 결과 (개발 담당용)'),
            el('p.small', '아래 결과에는 메시지 내용이 없고 파일 이름·형식만 있습니다. 쿨메신저 전용 읽기 기능을 만들 때 이 결과를 Claude Code 에게 전달하세요.'),
            el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(JSON.stringify(r, null, 2)); toast('진단 결과를 복사했습니다'); }) }, '결과 복사'),
            el('pre.text', JSON.stringify(r, null, 2))));
    });

    pane.append(
        el('h1', '설정'),
        el('p.sub', `업무 데스크 ${st.version || ''} · 모든 자료는 이 PC 의 teacher-desk/data 폴더에만 저장됩니다.`),
        section('학교·학급 (조퇴증·외출증에 인쇄)',
            el('div.grid2',
                textField('school_name', '학교 이름', null, { placeholder: '예: ○○고등학교' }),
                textField('teacher_name', '담임 이름'),
                textField('grade', '학년', null, { placeholder: '2' }),
                textField('class_no', '반', null, { placeholder: '3' }))),
        section('쿨메신저 연동',
            el('label.field', el('span', '쿨메신저 이름'), coolName, coolList,
                el('span.hint', '쿨메신저는 쪽지를 C:\\Users\\(윈도우 사용자)\\AppData\\Local\\CoolMessenger\\Memo\\(이름).udb 에 저장합니다. 윈도우 사용자 폴더는 자동으로 찾습니다.')),
            coolPathField,
            el('div.row', el('button.btn.small', { onclick: coolCheck }, '연결 확인')),
            coolBox),
        section('화면 열기',
            checkField('app_window', '전용 창으로 열기 (권장)',
                '업무 데스크를 주소창 없는 전용 창으로 엽니다. 다른 브라우저 창을 닫아도 같이 닫히지 않고, 이미 열려 있으면 새로 열지 않고 그 창을 앞으로 가져옵니다. 끄면 기본 브라우저의 새 탭으로 엽니다. 바꾼 뒤 다음에 켤 때부터 적용됩니다.')),
        section('자동 수집',
            checkField('watch_enabled', '실시간 감시 (권장)',
                '쿨메신저 쪽지 파일과 아래 폴더가 바뀌면 바로 읽습니다. 몇 초마다 파일의 바뀐 시각·크기만 확인하므로 PC 에 부담이 거의 없습니다.'),
            pathField('edufine_folder', '에듀파인 공문 저장 폴더', '에듀파인에서 공문을 저장하는 폴더', 'edufine', '예: C:\\Users\\선생님\\Documents\\공문'),
            el('div.row', el('button.btn.small', { onclick: diagnose('edufine') }, '폴더 확인')),
            pathField('coolmessenger_folder', '(선택) 쿨메신저 받은 파일 폴더', '쿨메신저로 받은 한글·PDF 파일이 저장되는 폴더. 비워 두면 쪽지만 읽습니다.', 'coolfolder'),
            el('div.row', el('button.btn.small', { onclick: diagnose('cool') }, '폴더 확인')),
            diagBox,
            el('label.field', el('span', '수집 시각'),
                el('input', { type: 'text', value: (s.collect_times || []).join(', '), dataset: { key: 'collect_times' } }),
                el('span.hint', '쉼표로 구분. 예: 09:00, 12:00, 15:00 — 업무 데스크가 켜져 있어야 합니다. 놓친 시각은 켜진 뒤 한 번 수집합니다.')),
            el('p.small.muted', `PDF 읽기: ${st.pdf_ready ? '준비됨' : '꺼짐 — 명령 프롬프트에서  pip install pypdf  실행 후 다시 시작'}`)),
        section('브라우저 단축키로 화면 보내기 (에듀파인)', captureBox()),
        section('텔레그램',
            el('ol.small', { style: 'margin: 0 0 12px; padding-left: 1.2em' },
                el('li', '텔레그램에서 @BotFather 를 찾아 /newbot 으로 봇을 만들고 토큰을 받습니다.'),
                el('li', '아래에 토큰을 넣고 저장합니다.'),
                el('li', '만든 봇에게 연결 코드를 보내면 선생님 계정만 연결됩니다.')),
            el('label.field', el('span', '봇 토큰'), token),
            el('p', '상태: ', el('b', tg.status || '-'),
                tg.linked ? ' · ✅ 연결됨' : (s.telegram_token_set ? ` · 봇에게 보낼 연결 코드: ` : ''),
                !tg.linked && s.telegram_token_set ? el('code', { style: 'font-size: 1.3em; font-weight: 800' }, `/start ${tg.pair_code}`) : null),
            el('label.field', el('span', '수집 알림에 담을 내용'), detail,
                el('span.hint', '새 자료가 들어왔을 때 저절로 보내는 알림의 내용입니다. 공문 내용이 텔레그램 서버를 거치는 것이 걱정되면 "개수만"을 고르세요.')),
            el('div.tg-help.small',
                el('b', '봇에게 보낼 수 있는 말'),
                el('ul',
                    el('li', '브리핑 — 오늘 일정·마감 + 지난 1주일 AI 요약'),
                    el('li', '그냥 묻기 — "다음 주 연수 언제야?" 처럼. 맞는 자료를 찾아 이 PC 의 AI(LM Studio)가 읽고 답합니다 (AI 를 안 켜면 찾은 자료만)'),
                    el('li', '검색 낱말 — 제목·요약과 [원문]·[파일] 단추. 원문·원본 파일은 단추를 누르거나 "원문 2"·"파일 2" 를 보낼 때만 갑니다'),
                    el('li', '조퇴 7 13:30 · 외출 7 13:30 14:30 · "7번 조퇴 1시 반" — 번호로 발급, 사유는 단추로. 답장에는 이름을 "김○○" 로만 쓰고 사유는 쓰지 않습니다. 인쇄는 이 PC 에서'),
                    el('li', '오늘 · 마감 · 새로고침 · 요약 · 도움말'))),
            el('div.row',
                tg.linked && el('button.btn.small', { onclick: tgAction('test') }, '시험 알림'),
                tg.linked && el('button.btn.small.danger', { onclick: tgAction('unpair') }, '연결 끊기'),
                !tg.linked && s.telegram_token_set && el('button.btn.small', { onclick: tgAction('renew') }, '코드 새로 만들기'))),
        section('나이스 학사일정',
            el('p.small', '달력 → 📥 학사일정 → 🏫 나이스 에서 학교를 골라 학사일정을 가져올 때 씁니다. 인증키는 교육정보 개방 포털(open.neis.go.kr)에 가입하면 무료로 받습니다.'),
            el('label.field', el('span', '나이스 인증키'), neisKey),
            s.neis_school && s.neis_school.name ? el('p.small', `고른 학교: ${s.neis_school.name}`) : null),
        section('AI 사용 방식',
            el('p.small', '일반 질문은 로컬·브라우저 내장·외부 API(Claude·GPT·Gemini)를 선택할 수 있습니다. 비공개 업무자료 요약·검색은 언제나 로컬 AI로만 처리합니다.'),
            el('button.btn', { onclick: openAI }, 'AI 선택·일반 질문 열기')),
        section('업무자료 AI 요약 (이 PC 의 LM Studio)',
            aiSetup(pane),
            el('p.small', '브리핑에서 지난 1주일 동안 받은 쿨메신저 쪽지·업무포털 공문을 AI 로 요약합니다. AI 는 이 PC 의 LM Studio 에서 돌기 때문에 쪽지·공문이 PC 밖으로 나가지 않습니다 (주소는 127.0.0.1 만 허용, 학생 이름은 가려서 보냄).'),
            el('ol.small', { style: 'margin: 0 0 12px; padding-left: 1.2em' },
                el('li', 'LM Studio 에서 한국어가 되는 모델을 받아 불러옵니다 (예: Qwen3 8B, Gemma 3 12B, EXAONE 3.5 7.8B).'),
                el('li', 'LM Studio 왼쪽 Developer(개발자) 화면에서 "Start Server" 를 켭니다.'),
                el('li', '아래 🔌 연결 확인 → AI 요약 켜기 → 설정 저장.')),
            checkField('ai_enabled', '브리핑에서 AI 요약 쓰기'),
            checkField('ai_auto_item', '새 수신 항목을 자동으로 AI 요약 (권장)',
                'AI 가 켜져 있으면 최근 7일 안에 받은 쪽지·공문을 뒤에서 한 건씩 요약해 수신함에 보여 줍니다 (한 건에 20~30초, 이 PC 의 LM Studio 로만). 연 항목은 먼저 요약하고, LM Studio 가 꺼져 있으면 조용히 쉬었다가 5분 뒤 다시 시도합니다. 끄면 항목 화면의 [AI 로 요약하기]를 눌렀을 때만 요약합니다.'),
            textField('ai_base', 'LM Studio 서버 주소', '기본 http://127.0.0.1:1234/v1'),
            textField('ai_model', '(선택) 모델 이름', '비워 두면 LM Studio 에 불러온 첫 모델'),
            el('label.field', el('span', '보내는 양'),
                el('select', { dataset: { key: 'ai_input_size' } },
                    [['normal', '보통 — 최대 14,000자 (더 꼼꼼, 4~5분)'], ['short', '짧게 — 최대 6,000자 (빠름, 2분 안팎)']]
                        .map(([v, l]) => el('option', { value: v, selected: (s.ai_input_size || 'normal') === v }, l))),
                el('span.hint', '오래 걸리면 "짧게"를 고르세요. 짧게는 최근 자료부터 한 건에 400자씩 보냅니다.')),
            el('div.row', el('button.btn.small', { onclick: aiCheck }, '연결 확인')),
            aiBox),
        section('업데이트',
            checkField('auto_update', '켤 때 새 버전 확인',
                '켤 때 새 버전이 있으면 자동으로 바꾼 뒤 다시 켭니다 (학생 명단·기록·설정은 그대로). 없으면 바로 켜집니다.'),
            updateBox(st),
            ),
        section('프로그램 실행',
            el('p.small.muted', '브라우저를 닫아도 수집·텔레그램은 백그라운드에서 계속 동작합니다. 완전히 끄려면 아래 단추를 누르세요.'),
            el('button.btn', { onclick: guarded(async () => {
                if (!window.confirm('업무 데스크를 종료할까요? 자동 수집·텔레그램·수업 알림도 멈춥니다.')) return;
                await api.post('/api/shutdown', {});
                toast('업무 데스크를 종료했습니다. 다시 쓰려면 start-desk.bat을 실행하세요.');
            }) }, '프로그램 종료')),
        section('학생 공유 페이지',
            textField('pass_page_url', '공유 페이지 주소', '조퇴증·외출증 QR 이 가리키는 주소 (저장소의 pass/ 폴더가 배포된 곳)')),
        section('화면 배치',
            el('p.small', '목록 패널 위치·글자 크기·색상을 바꾸고, 바꾸고 싶은 곳을 번호로 Claude Code 에게 전달할 수 있습니다.'),
            el('button.btn', { onclick: toggleLayoutMode }, '화면 배치 모드 열기')),
        el('div.row', { style: 'position: sticky; bottom: 0; background: var(--surface); padding: 10px 0' },
            el('button.btn.primary', { onclick: save }, '설정 저장'),
            el('span.small.muted', '칸을 고치면 바로 저장됩니다. 텔레그램 토큰·나이스 인증키는 이 단추로 저장하세요.')));
    autoSave(pane);
}

export function openSettings() {
    openTab({ id: 'settings', title: '설정', render: renderSettings });
}

registerView({
    id: 'settings', icon: '⚙', label: '설정', bottom: true, noSide: true,
    renderSide(body) {
        body.append(el('ul.list',
            el('li', { onclick: openSettings }, el('span.ttl', '설정 열기')),
            el('li', { onclick: toggleLayoutMode }, el('span.ttl', '화면 배치 모드'))));
    },
    open: openSettings,
});

on('state', () => {
    // 텔레그램 연결 상태가 바뀌면 설정 화면을 새로 그림 (입력 중일 때는 건드리지 않음)
    const tab = findTab('settings');
    if (tab && !tab.pane.contains(document.activeElement) && !tab.pane.dataset.dirty && !tab.pane.dataset.setupBusy) {
        const linked = tab.pane.dataset.linked;
        const now = String(!!store.state.telegram.linked) + store.state.telegram.status;
        if (linked !== undefined && linked !== now) tab.rerender();
        tab.pane.dataset.linked = now;
    }
});
