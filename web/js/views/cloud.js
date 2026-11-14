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
        box.dataset.sig = JSON.stringify([c.mode, c.message, c.last_up, c.last_down, c.holder, c.dirty]);
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
                    }) }, '로그아웃'))
                : el('div',
                    !c.client && el('p.set-bad.small', '구글 로그인 정보(클라이언트 ID)가 없습니다. 아래 "고급"에서 넣거나 업무 데스크를 나눠 준 선생님께 여쭈세요.'),
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
        parts.push(el('details.set-adv', el('summary', '고급 — 보통은 그대로 두세요'), el('div',
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
        if (st && st.dataset.sig !== JSON.stringify([c.mode, c.message, c.last_up, c.last_down, c.holder, c.dirty])) w.refresh(c);
    }
});
