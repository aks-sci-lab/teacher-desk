// ⚙ 설정: 학교·학급, 수집 폴더·시각, 텔레그램, 공유 페이지
import { api } from '../api.js';
import { el, clear, toast, guarded } from '../ui.js';
import { registerView, openTab, findTab, on, toggleLayoutMode } from '../shell.js';
import { store, loadState, saveSettings } from '../store.js';

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
                input.dispatchEvent(new Event('change'));
            }
        } finally {
            browse.disabled = false;
        }
    }) }, '📂 찾아보기');
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

function collectForm(pane) {
    const out = {};
    for (const input of pane.querySelectorAll('[data-key]')) {
        out[input.dataset.key] = input.type === 'checkbox' ? input.checked : input.value;
    }
    return out;
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
            clear(aiBox, el('p', { style: 'color: var(--ok); font-weight: 700' }, '✅ LM Studio 연결됨'),
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
                el('p', { style: 'color: var(--ok); font-weight: 700' }, `✅ 연결됨 — 쪽지 ${d.rows.toLocaleString()}개`),
                el('p.small.muted', `읽는 곳: 표 ${d.table} · 내용 ${d.body}${d.title ? ' · 제목 ' + d.title : ''}${d.date ? ' · 날짜 ' + d.date : ' · 날짜 열 없음'}${d.sender ? ' · 보낸 사람 ' + d.sender : ''}`),
                el('p.small', '최근 쪽지:'),
                el('ul.small', r.recent.map(m => el('li', `${m.when || ''} ${m.sender ? '[' + m.sender + '] ' : ''}${m.title}`))),
                el('p.small.muted', '맞으면 아래 "설정 저장"을 누르세요. 처음 수집 때 최근 30일 쪽지를 가져옵니다.'))
              : el('p', { style: 'color: var(--danger)' }, '쪽지 표를 찾지 못했습니다. 아래 구조를 복사해 개발 담당에게 보내 주세요.'),
            el('details', el('summary.small', '파일 구조 (내용 없음)'),
                el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(JSON.stringify(r.tables, null, 2)); toast('구조를 복사했습니다'); }) }, '📋 복사'),
                el('pre.text', JSON.stringify(r.tables, null, 2))));
    });

    const save = guarded(async () => {
        const change = collectForm(pane);
        change.collect_times = change.collect_times.split(',').map(t => t.trim()).filter(Boolean);
        if (token.value.trim()) change.telegram_token = token.value.trim();
        if (neisKey.value.trim()) change.neis_key = neisKey.value.trim();
        await saveSettings(change);
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
        const r = await api.get(`/api/diagnose?source=${source}`);
        clear(diagBox,
            el('p.small', '아래 결과에는 메시지 내용이 없고 파일 이름·형식만 있습니다. 쿨메신저 전용 읽기 기능을 만들 때 이 결과를 Claude Code 에게 전달하세요.'),
            el('button.btn.small', { onclick: guarded(async () => { await navigator.clipboard.writeText(JSON.stringify(r, null, 2)); toast('진단 결과를 복사했습니다'); }) }, '📋 결과 복사'),
            el('pre.text', JSON.stringify(r, null, 2)));
    });

    pane.append(
        el('h1', '설정'),
        el('p.sub', `업무 데스크 ${st.version || ''} · 모든 자료는 이 PC 의 teacher-desk/data 폴더에만 저장됩니다.`),
        section('🏫 학교·학급 (조퇴증·외출증에 인쇄)',
            el('div.grid2',
                textField('school_name', '학교 이름', null, { placeholder: '예: ○○고등학교' }),
                textField('teacher_name', '담임 이름'),
                textField('grade', '학년', null, { placeholder: '2' }),
                textField('class_no', '반', null, { placeholder: '3' }))),
        section('💬 쿨메신저 연동',
            el('label.field', el('span', '쿨메신저 이름'), coolName, coolList,
                el('span.hint', '쿨메신저는 쪽지를 C:\\Users\\(윈도우 사용자)\\AppData\\Local\\CoolMessenger\\Memo\\(이름).udb 에 저장합니다. 윈도우 사용자 폴더는 자동으로 찾습니다.')),
            coolPathField,
            el('div.row', el('button.btn.small', { onclick: coolCheck }, '🔌 연결 확인')),
            coolBox),
        section('🔄 자동 수집',
            checkField('watch_enabled', '👁 실시간 감시 (권장)',
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
        section('📱 텔레그램',
            el('ol.small', { style: 'margin: 0 0 12px; padding-left: 1.2em' },
                el('li', '텔레그램에서 @BotFather 를 찾아 /newbot 으로 봇을 만들고 토큰을 받습니다.'),
                el('li', '아래에 토큰을 넣고 저장합니다.'),
                el('li', '만든 봇에게 연결 코드를 보내면 선생님 계정만 연결됩니다.')),
            el('label.field', el('span', '봇 토큰'), token),
            el('p', '상태: ', el('b', tg.status || '-'),
                tg.linked ? ' · ✅ 연결됨' : (s.telegram_token_set ? ` · 봇에게 보낼 연결 코드: ` : ''),
                !tg.linked && s.telegram_token_set ? el('code', { style: 'font-size: 1.3em; font-weight: 800' }, `/start ${tg.pair_code}`) : null),
            el('label.field', el('span', '알림에 담을 내용'), detail,
                el('span.hint', '학생 이름·조퇴 사유는 어느 경우에도 보내지 않습니다. 공문 내용이 텔레그램 서버를 거치는 것이 걱정되면 "개수만"을 고르세요.')),
            el('div.row',
                tg.linked && el('button.btn.small', { onclick: tgAction('test') }, '🔔 시험 알림'),
                tg.linked && el('button.btn.small.danger', { onclick: tgAction('unpair') }, '연결 끊기'),
                !tg.linked && s.telegram_token_set && el('button.btn.small', { onclick: tgAction('renew') }, '코드 새로 만들기'))),
        section('📅 나이스 학사일정',
            el('p.small', '달력 → 📥 학사일정 → 🏫 나이스 에서 학교를 골라 학사일정을 가져올 때 씁니다. 인증키는 교육정보 개방 포털(open.neis.go.kr)에 가입하면 무료로 받습니다.'),
            el('label.field', el('span', '나이스 인증키'), neisKey),
            s.neis_school && s.neis_school.name ? el('p.small', `고른 학교: ${s.neis_school.name}`) : null),
        section('🤖 AI 요약 (이 PC 의 LM Studio)',
            el('p.small', '브리핑에서 지난 1주일 동안 받은 쿨메신저 쪽지·업무포털 공문을 AI 로 요약합니다. AI 는 이 PC 의 LM Studio 에서 돌기 때문에 쪽지·공문이 PC 밖으로 나가지 않습니다 (주소는 127.0.0.1 만 허용, 학생 이름은 가려서 보냄).'),
            el('ol.small', { style: 'margin: 0 0 12px; padding-left: 1.2em' },
                el('li', 'LM Studio 에서 한국어가 되는 모델을 받아 불러옵니다 (예: Qwen3 8B, Gemma 3 12B, EXAONE 3.5 7.8B).'),
                el('li', 'LM Studio 왼쪽 Developer(개발자) 화면에서 "Start Server" 를 켭니다.'),
                el('li', '아래 🔌 연결 확인 → AI 요약 켜기 → 설정 저장.')),
            checkField('ai_enabled', '브리핑에서 AI 요약 쓰기'),
            textField('ai_base', 'LM Studio 서버 주소', '기본 http://127.0.0.1:1234/v1'),
            textField('ai_model', '(선택) 모델 이름', '비워 두면 LM Studio 에 불러온 첫 모델'),
            el('div.row', el('button.btn.small', { onclick: aiCheck }, '🔌 연결 확인')),
            aiBox),
        section('⬆ 업데이트',
            checkField('auto_update', '켤 때 새 버전 확인',
                '켤 때 새 버전이 있으면 자동으로 바꾼 뒤 다시 켭니다 (학생 명단·기록·설정은 그대로). 없으면 바로 켜집니다.'),
            el('p.small.muted', `지금 버전: ${st.version || ''}`)),
        section('🔗 학생 공유 페이지',
            textField('pass_page_url', '공유 페이지 주소', '조퇴증·외출증 QR 이 가리키는 주소 (저장소의 pass/ 폴더가 배포된 곳)')),
        section('🧩 화면 배치',
            el('p.small', '목록 패널 위치·글자 크기·색상을 바꾸고, 바꾸고 싶은 곳을 번호로 Claude Code 에게 전달할 수 있습니다.'),
            el('button.btn', { onclick: toggleLayoutMode }, '🧩 화면 배치 모드 열기')),
        el('div.row', { style: 'position: sticky; bottom: 0; background: var(--surface); padding: 10px 0' },
            el('button.btn.primary', { onclick: save }, '💾 설정 저장')));
}

export function openSettings() {
    openTab({ id: 'settings', title: '⚙ 설정', render: renderSettings });
}

registerView({
    id: 'settings', icon: '⚙', label: '설정', bottom: true,
    renderSide(body) {
        body.append(el('ul.list',
            el('li', { onclick: openSettings }, el('span.ttl', '⚙ 설정 열기')),
            el('li', { onclick: toggleLayoutMode }, el('span.ttl', '🧩 화면 배치 모드'))));
    },
    open: openSettings,
});

on('state', () => {
    // 텔레그램 연결 상태가 바뀌면 설정 화면을 새로 그림 (입력 중일 때는 건드리지 않음)
    const tab = findTab('settings');
    if (tab && !tab.pane.contains(document.activeElement)) {
        const linked = tab.pane.dataset.linked;
        const now = String(!!store.state.telegram.linked) + store.state.telegram.status;
        if (linked !== undefined && linked !== now) tab.rerender();
        tab.pane.dataset.linked = now;
    }
});
