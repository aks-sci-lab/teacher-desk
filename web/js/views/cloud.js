// ☁ 여러 PC 이어 쓰기 (0.5.96): 선생님 본인 구글 드라이브의 숨은 앱 폴더에 암호화해 올리고 받기
// 설정 → 드라이브 묶음과 설정 도우미(선택 단계)가 함께 씀. 동작은 서버(desk/cloudsync.py)가 하고 화면은 상태·단추만.
import { api } from '../api.js';
import { el, clear, toast, guarded } from '../ui.js';
import { on } from '../shell.js';
import { store, loadState } from '../store.js';
import { item, textField, checkField, secretField } from './setfields.js';

const MODE = {
    off: '꺼짐', starting: '확인 중…', ok: '이어 쓰는 중', readonly: '읽기 전용 (다른 PC 사용 중)',
    conflict: '고르기 필요', error: '연결 문제 (이 PC 에서 계속 씀)',
};

// 아래 상태 막대·설정 목록에 보일 한 줄
export function cloudLine(c) {
    if (!c || !c.enabled) return '';
    if (c.mode === 'ok') return `☁ 이어 쓰는 중${c.last_up ? ` · 올림 ${c.last_up.slice(5)}` : ''}`;
    if (c.mode === 'readonly') return `☁ 읽기 전용 — ${c.holder?.pc_name || '다른 PC'} 사용 중`;
    if (c.mode === 'conflict') return '☁ 고르기 필요';
    return `☁ ${MODE[c.mode] || c.mode}`;
}

// 배포자(업무 데스크를 나눠 주는 선생님)가 처음 한 번 하는 일 (0.5.100 준비 도우미)
// 구글 콘솔은 사람이 눌러야 하는 화면(동의·약관)이 있어 완전 자동은 안 됨 → 각 단계 화면을 [열기]로 바로 열고,
// 마지막에 받은 JSON 파일을 넣으면 ID·비밀번호가 저절로 들어가고 로그인 창이 바로 열림.
const CONSOLE = 'https://console.cloud.google.com';
export const SCOPE = 'https://www.googleapis.com/auth/drive.appdata';
export const OWNER_STEPS = [
    { title: '새 프로젝트 만들기', url: `${CONSOLE}/projectcreate`,
      how: '프로젝트 이름에 "업무 데스크" → [만들기]. 처음이면 약관 동의 창이 먼저 뜹니다. 만든 뒤 화면 위쪽 프로젝트 이름이 "업무 데스크"인지 확인하세요 (아니면 눌러서 고름).' },
    { title: 'Google Drive API 켜기', url: `${CONSOLE}/apis/library/drive.googleapis.com`,
      how: '파란 [사용] 단추를 누릅니다. 이미 "관리"로 보이면 켜져 있는 것입니다.' },
    { title: '로그인 화면(앱 이름) 만들기', url: `${CONSOLE}/auth/overview`,
      how: '[시작하기] → 앱 이름 "업무 데스크", 사용자 지원 이메일(내 계정) → [다음] → 대상 [외부] → [다음] → 연락처 이메일(내 계정) → [다음] → 동의 체크 → [만들기]. 이미 만들어져 있으면 건너뜁니다.' },
    { title: '권한 범위 넣기', url: `${CONSOLE}/auth/scopes`, copy: SCOPE,
      how: '[범위 추가 또는 삭제] → 창 맨 아래 "직접 범위 추가" 칸에 아래 [범위 복사]로 복사한 글을 붙여 넣기 → [표에 추가] → [업데이트] → 화면 아래 [저장].' },
    { title: '앱 게시하기', url: `${CONSOLE}/auth/audience`,
      how: '게시 상태 "테스트" 아래 [앱 게시] → [확인]. ("테스트"로 두면 7일마다 다시 로그인해야 합니다. 숨은 앱 폴더만 쓰는 좁은 권한이라 구글 심사 없이 게시됩니다.)' },
    { title: '로그인 클라이언트 만들고 JSON 받기', url: `${CONSOLE}/auth/clients/create`,
      how: '애플리케이션 유형 [데스크톱 앱] → 이름 "업무 데스크" → [만들기] → 뜬 창에서 [JSON 다운로드]. 받은 client_secret_….json 파일을 아래 7단계에 넣습니다. (보안 비밀번호는 이 창에서만 보이니 꼭 받아 두세요.)' },
];

// 받은 JSON 파일(또는 붙여 넣은 두 값)을 서버에 보내 저장 → 바로 로그인 창 열기
async function saveClientAndLogin(payload) {
    await api.post('/api/cloud', { action: 'client', ...payload });
    await loadState();
    const r = await api.post('/api/cloud', { action: 'login' });
    window.open(r.url, '_blank', 'noopener');
    toast('저장했습니다. 새 창에서 구글 계정을 고르고 [허용]을 누른 뒤 이 화면으로 돌아오세요. ("확인되지 않은 앱" 경고가 뜨면 [고급] → [업무 데스크(으)로 이동])', 'info', 12000);
    return r;
}

function clientDrop() {
    const msg = el('p.small.muted', 'client_secret_….json 파일을 이 칸에 끌어다 놓거나 [파일 고르기]를 누르세요.');
    const take = guarded(async file => {
        if (!file) return;
        if (file.size > 20000) throw new Error('이 파일은 구글 클라이언트 JSON 이 아닌 것 같습니다 (너무 큼)');
        await saveClientAndLogin({ json: await file.text() });
        clear(msg, '✅ 클라이언트를 넣었습니다. 로그인 창이 열렸습니다.');
    });
    const input = el('input', { type: 'file', accept: '.json,application/json', style: 'display:none',
        onchange: e => { take(e.target.files[0]); e.target.value = ''; } });
    const zone = el('div.cloud-drop', {
        ondragover: e => { e.preventDefault(); zone.classList.add('over'); },
        ondragleave: () => zone.classList.remove('over'),
        ondrop: e => { e.preventDefault(); zone.classList.remove('over'); take(e.dataTransfer.files[0]); },
    }, el('b', '📄 JSON 파일 넣기'), msg,
        el('button.btn.small.primary', { type: 'button', onclick: () => input.click() }, '파일 고르기'), input);
    const id = el('input', { type: 'text', autocomplete: 'off', placeholder: '…apps.googleusercontent.com', style: 'flex: 1' });
    const sec = el('input', { type: 'password', autocomplete: 'off', placeholder: '클라이언트 보안 비밀번호', style: 'flex: 1' });
    const typed = el('details.small', el('summary', '파일이 없으면: 두 값을 직접 붙여 넣기'),
        el('div.row', id), el('div.row', sec,
            el('button.btn.small', { type: 'button', onclick: guarded(async () => {
                await saveClientAndLogin({ client_id: id.value, client_secret: sec.value });
                id.value = ''; sec.value = '';
            }) }, '저장하고 로그인')));
    return el('div', zone, typed);
}

const checkDrive = guarded(async () => {
    const r = await api.post('/api/cloud', { action: 'check' });
    toast(`✅ 드라이브 연결이 됩니다 (숨은 앱 폴더 파일 ${r.check.files}개). 이제 복구 키를 만들고 켜세요.`, 'info', 9000);
});

function ownerGuide(open = false) {
    const steps = OWNER_STEPS.map((s, i) => el('li',
        el('b', `${i + 1}. ${s.title} `),
        el('a.btn.small', { href: s.url, target: '_blank', rel: 'noopener' }, '열기 ↗'),
        s.copy && el('button.btn.small', { type: 'button', onclick: guarded(async () => { await navigator.clipboard.writeText(s.copy); toast('범위를 복사했습니다. 구글 화면 "직접 범위 추가" 칸에 붙여 넣으세요.'); }) }, '범위 복사'),
        el('div.small', s.how)));
    steps.push(el('li', el('b', `${OWNER_STEPS.length + 1}. 받은 JSON 넣고 로그인 `), el('div.small', '넣으면 ID·보안 비밀번호가 저절로 저장되고 구글 로그인 창이 바로 열립니다.'), clientDrop()));
    steps.push(el('li', el('b', `${OWNER_STEPS.length + 2}. 연결 시험 `),
        el('button.btn.small', { type: 'button', onclick: checkDrive }, '시험하기'),
        el('div.small', '로그인 뒤 누르면 Drive API 가 켜졌는지, 권한이 맞는지 바로 알려 줍니다.')));
    return el('details.small.cloud-owner', { open },
        el('summary', '준비 도우미: 구글 로그인 앱 만들기 (처음 한 번, 약 5분)'),
        el('p', '업무 데스크를 나눠 주는 선생님이 한 번만 하면 됩니다. 각 단계의 [열기]를 누르면 구글 클라우드 콘솔의 그 화면이 바로 열립니다 (선생님 구글 계정으로 로그인). 구글 화면의 단추 이름은 조금 다를 수 있습니다.'),
        el('ol.cloud-steps', steps));
}

function statusBox() {
    const box = el('div.cloud-status');
    const act = (action, extra = {}, done) => guarded(async ev => {
        ev?.target && (ev.target.disabled = true);
        try {
            const r = await api.post('/api/cloud', { action, ...extra });
            await loadState();
            draw(r);
            done?.(r);
        } finally {
            if (ev?.target) ev.target.disabled = false;
        }
    });
    const draw = c => {
        box.dataset.sig = JSON.stringify([c.mode, c.message, c.last_up, c.last_down, c.holder, c.dirty, c.client, c.logged_in]);
        clear(box,
            el('p', '상태: ', el('b', { className: `cloud-mode ${c.mode}` }, MODE[c.mode] || c.mode),
                c.message ? el('span.small', ` — ${c.message}`) : null),
            el('p.small.muted', [`이 PC 이름: ${c.pc_name}`, c.last_up && `마지막 올림 ${c.last_up}`, c.last_down && `마지막 받음 ${c.last_down}`,
                c.remote_at && `드라이브 최신: ${c.remote_pc || ''} ${c.remote_at}`].filter(Boolean).join(' · ')),
            c.mode === 'conflict' && el('div.cloud-choice',
                el('p', '이 PC 에도, 드라이브에도 서로 다른 변경이 있습니다. 어느 쪽을 쓸까요? 고르지 않은 쪽도 이 PC 의 data\\cloud-backups 폴더에 남습니다.'),
                el('div.row',
                    el('button.btn.primary', { type: 'button', onclick: (e) => confirm('드라이브 자료로 이 PC 자료를 바꿀까요? (지금 이 PC 자료는 보관해 둡니다)') && act('resolve', { choice: 'drive' }, () => location.reload())(e) }, '드라이브 자료 쓰기'),
                    el('button.btn', { type: 'button', onclick: (e) => confirm('이 PC 자료를 드라이브에 올릴까요? (지금 드라이브 자료는 이 PC 에 보관해 둡니다)') && act('resolve', { choice: 'local' })(e) }, '이 PC 자료 쓰기'))),
            c.mode === 'readonly' && el('div.cloud-choice',
                el('p', `${c.holder?.pc_name || '다른 PC'} 에서 쓰는 중입니다 (마지막 사용 ${c.holder?.active_at || '-'}). 이 PC 는 보기만 하고 저장·수집·텔레그램은 쉽니다.`),
                el('button.btn.primary', { type: 'button', onclick: (e) => confirm('이 PC 에서 쓰기로 가져올까요? 다른 PC 는 곧 읽기 전용이 되고, 그 PC 에서 아직 올리지 않은 변경은 반영되지 않을 수 있습니다.') && act('takeover', {}, () => location.reload())(e) }, '이 PC 에서 쓰기 (강제로 가져오기)')),
            el('div.row',
                c.mode === 'ok' && el('button.btn.small', { type: 'button', onclick: act('sync', {}, () => toast('드라이브에 올렸습니다')) }, '지금 올리기'),
                (c.mode === 'error' || c.mode === 'off') && c.enabled && el('button.btn.small', { type: 'button', onclick: act('sync') }, '다시 연결')));
    };
    box.redraw = draw;
    draw(store.state?.cloud || { mode: 'off', pc_name: '' });
    return box;
}

function loginBox() {
    const box = el('div');
    const draw = c => {
        clear(box,
            c.logged_in
                ? el('p', '✅ 구글 로그인됨 ',
                    el('button.btn.small', { type: 'button', onclick: guarded(async () => {
                        if (!confirm('구글 로그아웃할까요? 이 PC 의 드라이브 이어 쓰기가 꺼집니다. 드라이브에 올린 자료는 남습니다.')) return;
                        draw(await api.post('/api/cloud', { action: 'logout' }));
                        await loadState();
                    }) }, '로그아웃'),
                    el('button.btn.small', { type: 'button', onclick: checkDrive }, '연결 시험'))
                : el('div',
                    !c.client && el('p.set-bad.small', '구글 로그인 정보(클라이언트 ID)가 없습니다. 아래 준비 도우미를 따라 만들고 받은 JSON 파일을 넣으면 됩니다 (업무 데스크를 나눠 준 선생님께 받아도 됩니다).'),
                    !c.client && ownerGuide(true),
                    el('button.btn.primary', { type: 'button', disabled: !c.client, onclick: guarded(async () => {
                        const r = await api.post('/api/cloud', { action: 'login' });
                        window.open(r.url, '_blank', 'noopener');
                        toast('브라우저에서 구글 계정을 고르고 [허용]을 누르세요. 끝나면 이 화면으로 돌아오세요.', 'info', 9000);
                    }) }, '구글로 로그인'),
                    el('p.small.muted', '업무 데스크는 드라이브의 "숨은 앱 전용 폴더"만 씁니다. 선생님 드라이브의 다른 파일은 보지도 바꾸지도 못합니다.')));
    };
    box.redraw = draw;
    draw(store.state?.cloud || {});
    return box;
}

function keyBox() {
    const box = el('div');
    const input = el('input', { type: 'password', autocomplete: 'off', placeholder: 'XXXXX-XXXXX-XXXXX-XXXXX-XXXXX', style: 'flex: 1' });
    const shown = el('div');
    const save = guarded(async () => {
        const r = await api.post('/api/cloud', { action: 'key', passphrase: input.value });
        input.value = '';
        clear(shown);
        await loadState();
        draw(r);
        toast('복구 키를 저장했습니다', 'info');
    });
    const draw = c => {
        clear(box,
            el('p', c.key_set ? '✅ 복구 키 저장됨 (이 PC 에만, 화면에 다시 보이지 않음)' : '복구 키가 아직 없습니다.'),
            el('p.small', '드라이브에 올리는 자료는 이 복구 키로 잠급니다. ', el('b', '복구 키를 잃으면 드라이브 자료를 아무도 풀 수 없습니다'),
                ' (구글도, 개발자도). 종이에 적어 안전한 곳에 두세요. 다른 PC 에서는 같은 키를 넣습니다.'),
            !c.key_set && el('div.row',
                el('button.btn.small', { type: 'button', onclick: guarded(async () => {
                    const r = await api.post('/api/cloud', { action: 'newkey' });
                    input.type = 'text';
                    input.value = r.key;
                    clear(shown, el('p.cloud-key', r.key),
                        el('p.small.set-bad', '지금 이 키를 종이에 적으세요. [이 키로 저장]을 누른 뒤에는 다시 보여 주지 않습니다.'),
                        el('button.btn.small', { type: 'button', onclick: guarded(async () => { await navigator.clipboard.writeText(r.key); toast('복사했습니다. 메모장 등에 붙여 두고 종이에도 적으세요.'); }) }, '복사'));
                }) }, '복구 키 만들기 (첫 PC)')),
            shown,
            el('div.field', el('span', c.key_set ? '다른 키 넣기 (드라이브 자료가 그 키로 풀릴 때만 바뀝니다)' : '복구 키 (방금 만든 키, 또는 첫 PC 에서 적어 둔 키)'),
                el('div.row', input, el('button.btn.small.primary', { type: 'button', onclick: save }, '이 키로 저장')),
                el('span.hint', '드라이브에 이미 자료가 있으면 그 자료가 이 키로 풀리는지 먼저 확인합니다. 직접 정한 10자 이상의 암호문도 됩니다.')));
    };
    input.addEventListener('keydown', e => { if (e.key === 'Enter') save(); });
    draw(store.state?.cloud || {});
    return box;
}

// 드라이브 백업 (0.5.97): 날짜별 목록 → [이 날짜로 되살리기]
function backupBox() {
    const list = el('div');
    const kb = n => (n > 1048576 ? `${(n / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`);
    const show = guarded(async () => {
        clear(list, el('p.small.muted', '드라이브에서 백업 목록을 읽는 중…'));
        const r = await api.post('/api/cloud', { action: 'backups' });
        const rows = r.backups || [];
        clear(list, rows.length
            ? el('ul.cloud-backups', rows.map(b => el('li',
                el('b', b.date), b.kind === 'restore' ? el('span.small.muted', ' (되살리기 직전 모습)') : null,
                el('span.small.muted', ` · ${kb(b.size)}`),
                r.mode === 'ok' && el('button.btn.small', { type: 'button', onclick: guarded(async ev => {
                    if (!confirm(`${b.date} 백업으로 되살릴까요?\n\n지금 자료는 이 PC(data\\cloud-backups)와 드라이브("되살리기 직전 모습")에 남겨 두고, 되살린 자료를 드라이브에 새 판으로 올립니다. 다른 PC 는 다음에 켤 때 이 자료를 받습니다.`)) return;
                    ev.target.disabled = true;
                    try {
                        await api.post('/api/cloud', { action: 'restore', name: b.name });
                        toast(`${b.date} 백업으로 되살렸습니다. 화면을 새로 고칩니다.`, 'info', 4000);
                        setTimeout(() => location.reload(), 1200);
                    } finally {
                        ev.target.disabled = false;
                    }
                }) }, '이 날짜로 되살리기'))))
            : el('p.small.muted', '아직 백업이 없습니다. 날짜가 바뀐 뒤 처음 올릴 때 전날 마지막 모습이 저절로 백업됩니다. 지금 하나 만들려면 [지금 백업].'),
            r.mode !== 'ok' && rows.length ? el('p.small', '되살리기는 이 PC 에서 이어 쓰는 중일 때만 됩니다.') : null);
    });
    return el('div',
        el('p.small', '드라이브에 날짜별 백업이 저절로 남습니다: 최근 14일은 날마다(그날 마지막 모습), 그보다 오래된 것은 달마다 하나씩 12달. 백업도 복구 키로 잠겨 있습니다.'),
        el('div.row',
            el('button.btn.small', { type: 'button', onclick: show }, '백업 목록 보기'),
            el('button.btn.small', { type: 'button', onclick: guarded(async ev => {
                ev.target.disabled = true;
                try {
                    await api.post('/api/cloud', { action: 'backup' });
                    toast('오늘 날짜로 백업했습니다', 'info');
                    await show();
                } finally {
                    ev.target.disabled = false;
                }
            }) }, '지금 백업')),
        list);
}

// 설정 → 드라이브 와 도우미가 쓰는 묶음 (짧게: 도우미는 compact)
export function cloudBox({ compact = false } = {}) {
    const c = store.state?.cloud || {};
    const status = statusBox();
    const login = loginBox();
    const parts = [
        item('드라이브 여러 PC 이어 쓰기 백업 암호화 구글',
            el('p.small', '학교 PC·집 PC 처럼 여러 PC 에서 같은 자료로 이어 쓰고, 백업도 됩니다. 앱은 각 PC 에서 돌고 드라이브는 "창고"입니다. 자료는 이 PC 에서 암호화한 뒤 선생님 본인 구글 드라이브에만 올립니다. 한 번에 한 PC 만 쓰고, 다른 PC 가 쓰는 중이면 이 PC 는 읽기 전용이 됩니다.'),
            !c.crypto && el('p.set-bad.small', '이 PC 에는 암호화 도구가 없어 쓸 수 없습니다 (윈도우용 ZIP 설치본에서 쓰세요).')),
        item('상태 지금 올리기 이 PC 에서 쓰기 충돌', status),
        item('1 구글 로그인 로그아웃', el('h4', '1. 구글 로그인'), login),
        item('2 복구 키 암호문 암호화', el('h4', '2. 복구 키'), keyBox()),
        item('3 켜기', el('h4', '3. 켜기')),
        checkField('gdrive_enabled', '드라이브로 여러 PC 이어 쓰기 켜기',
            '켜면 1분 안에 연결합니다. 켤 때 최신 자료를 받고, 쓰는 동안 10분마다·끌 때 올립니다. 인터넷이 안 되면 이 PC 에서 그냥 쓰고 나중에 올립니다.'),
    ];
    if (!compact) {
        parts.push(item('4 백업 되살리기 날짜별 복원', el('h4', '4. 백업·되살리기'), backupBox()));
        parts.push(el('details.set-adv', el('summary', '고급 — 보통은 그대로 두세요'), el('div',
            ownerGuide(),
            textField('gdrive_pc_name', '이 PC 이름', '다른 PC 에 "○○ 에서 쓰는 중" 으로 보일 이름 (비우면 컴퓨터 이름)', { placeholder: '예: 학교 교무실 PC' }),
            textField('gdrive_client_id', '구글 로그인 클라이언트 ID', '업무 데스크를 나눠 준 선생님(배포자)이 Google Cloud 에서 만든 "데스크톱 앱" 클라이언트. 비우면 기본값.'),
            secretField('gdrive_client_secret', '구글 로그인 클라이언트 보안 비밀', '위 클라이언트와 함께 받은 값', '클라이언트 보안 비밀'))));
    }
    const wrap = el('div.cloud-box', parts);
    wrap.refresh = c2 => { status.redraw?.(c2); login.redraw?.(c2); };
    return wrap;
}

// 상태가 바뀌면 열린 드라이브 칸의 상태만 다시 그림 (입력 중인 칸은 그대로)
on('state', s => {
    for (const w of document.querySelectorAll('.cloud-box')) {
        const st = w.querySelector('.cloud-status');
        const c = s.cloud || {};
        if (st && st.dataset.sig !== JSON.stringify([c.mode, c.message, c.last_up, c.last_down, c.holder, c.dirty, c.client, c.logged_in])) w.refresh(c);
    }
});
