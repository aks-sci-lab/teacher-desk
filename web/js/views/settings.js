// ⚙ 설정 (0.5.79 개편): 왼쪽 묶음 목록 + 오른쪽 내용, 검색 칸, 항목마다 한 줄 설명, 고급은 접기.
// 칸은 고치면 바로 저장되고(저장 상태는 위에 표시), 잘못된 값은 칸 아래에 바로 안내합니다.
// 설정 키 이름·config.json 형식은 예전 그대로입니다 (새 키는 설정 도우미 상태 setup_wizard 하나).
import { api } from '../api.js';
import { el, clear, toast, guarded, download } from '../ui.js';
import { registerView, openTab, findTab, on, toggleLayoutMode, menuList, setMenuOn } from '../shell.js';
import { store, loadState, saveSettings } from '../store.js';
import { openAI } from './ai.js';
import { checkUpdate, startUpdate } from '../update.js';
import { openTimetable } from './timetable.js';
import { openSchoolCal } from './schoolcal.js';
import { openRoster } from './passes.js';
import { openSetup } from './setup.js';
import { openShareExport, openShareImport } from './share.js';
import { cloudBox, cloudLine } from './cloud.js';
import {
    item, textField, pathField, checkField, selectField, secretField, autoSave, saveStateBox,
    coolConnect, folderCheck, telegramStatus, refreshTelegramBoxes, aiCheck, TELEGRAM_STEPS,
} from './setfields.js';
import { matches } from '../setup-rules.js';

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
                    el('p.small', '에듀파인(*.use.go.kr)·나이스(*.neis.go.kr) 화면에서 단축키를 누르면 지금 보이는 글을 이 PC 의 업무 데스크로 보내 수신함에 넣고, 날짜·마감을 찾습니다. 읽은 글은 이 PC 밖으로 나가지 않습니다. 켜기 전에는 어떤 화면도 받지 않습니다.'),
                    el('div.row', el('button.btn.small.primary', { onclick: act('enable') }, '연결 켜기'))));
    };
    guarded(async () => draw(await api.get('/api/capture')))();
    return box;
}

// 선생님 본인 구글 캘린더와 일정 공유 (0.5.91): 가져오기(비공개 iCal 주소) · 올리기(내 계정의 Apps Script 웹 앱)
function gcalStatusLine(g) {
    const st = g.status || {};
    const lines = [];
    if (g.import_on) {
        const s = st.import || {};
        lines.push(el('p.small.gcal-st', { className: `small gcal-st${s.ok === false ? ' bad' : ''}` },
            !g.ical_set ? '가져오기: 주소를 넣어 주세요'
                : s.ok === false ? `가져오기 실패: ${s.error} (${s.retry || ''})`
                    : s.at ? `가져오기: ${s.at} 에 ${s.count ?? 0}건 가져옴 (30분마다)` : '가져오기: 곧 가져옵니다'));
    }
    if (g.push_on) {
        const s = st.push || {};
        lines.push(el('p.small.gcal-st', { className: `small gcal-st${s.ok === false ? ' bad' : ''}` },
            !(g.push_set && g.key_set) ? '올리기: 스크립트를 만들고 웹 앱 주소를 넣어 주세요'
                : s.ok === false ? `올리기 실패: ${s.error} — 보낼 것 ${g.pending ?? '?'}건은 ${s.retry || '잠시 뒤'} 다시 보냅니다`
                    : `올리기: ${s.at ? `${s.at} 에 마지막으로 보냄` : '아직 보내지 않음'} · 보낼 것 ${g.pending ?? 0}건`));
    }
    if (!lines.length) lines.push(el('p.small.muted', '꺼져 있습니다. 아래에서 켜면 그때부터 선생님 구글 계정과 주고받습니다.'));
    return lines;
}

function gcalBox() {
    const status = el('div');
    const scriptArea = el('div');
    const types = el('div.gcal-types');
    let g = null;
    const draw = data => {
        g = data;
        clear(status, gcalStatusLine(g));
        clear(types, g.type_labels.map(([k, label]) => el('label.check',
            el('input', { type: 'checkbox', checked: g.types.includes(k), onchange: guarded(async ev => {
                const on = new Set(g.types);
                if (ev.target.checked) on.add(k); else on.delete(k);
                await saveSettings({ gcal_push_types: [...on] });
                toast(`${label}: ${ev.target.checked ? '올림' : '올리지 않음'}`, 'info', 1500);
                draw(await api.get('/api/gcal'));
            }) }),
            el('span', label))));
    };
    const refresh = guarded(async () => draw(await api.get('/api/gcal')));
    const act = (action, done) => guarded(async () => {
        const r = await api.post('/api/gcal', { action });
        draw(r);
        done?.(r);
    });
    const checkBtn = el('button.btn.small', { type: 'button', onclick: act('check', r => {
        const c = r.check || {};
        const msg = [
            c.import && (c.import.ok === true ? `가져오기 성공 (${c.import.count}건)` : c.import.ok === false ? `가져오기 실패: ${c.import.error}` : '가져오기는 꺼져 있음'),
            c.push && (c.push.ok ? `올리기 연결 성공 (캘린더: ${c.push.calendar})` : `올리기 실패: ${c.push.error}`),
        ].filter(Boolean).join(' · ');
        toast(msg, c.push?.ok === false || c.import?.ok === false ? 'error' : 'info', 7000);
    }) }, '연결 확인');
    const makeScript = guarded(async () => {
        if (g?.key_set && !confirm('비밀 키를 새로 만들면 이미 배포한 스크립트는 더 이상 쓸 수 없습니다. 새 스크립트를 붙여 넣고 "새 버전"으로 다시 배포해야 합니다. 새로 만들까요?')) return;
        const r = await api.post('/api/gcal', { action: 'script' });
        draw(r);
        const ta = el('textarea.gcal-script', { readonly: true, onclick: e => e.target.select() }, r.script);
        clear(scriptArea,
            el('p.small', '아래 글 전체를 복사해 Apps Script 편집기에 붙여 넣으세요. 이 글에는 비밀 키가 들어 있어 이 화면을 닫으면 다시 보여 주지 않습니다 (필요하면 새로 만들기).'),
            ta,
            el('div.row', el('button.btn.small.primary', { type: 'button', onclick: guarded(async () => {
                await navigator.clipboard.writeText(r.script);
                toast('스크립트를 복사했습니다');
            }) }, '스크립트 복사')));
    });
    refresh();
    return el('div.gcal-box',
        item('구글 캘린더 상태 연결 확인 동기화', el('div.field', el('span', '지금 상태')), status,
            el('div.row', checkBtn,
                el('button.btn.small', { type: 'button', onclick: act('import', () => toast('구글 캘린더 일정을 가져왔습니다')) }, '지금 가져오기'),
                el('button.btn.small', { type: 'button', onclick: act('push', r => toast(`${r.result.sent}건 보냈습니다${r.result.pending ? ` (남은 것 ${r.result.pending}건)` : ''}`)) }, '지금 보내기'))),
        el('h3', '① 가져오기: 구글 캘린더 → 업무 데스크 달력'),
        checkField('gcal_import', '구글 캘린더 일정을 달력에 보이기',
            '켜면 30분마다(켤 때도) 선생님 구글 캘린더를 읽어 달력에 "구글" 표시로 보여 줍니다. 읽기만 하며, 업무 데스크가 올린 일정은 다시 가져오지 않습니다.'),
        item('비공개 주소 iCal 구글 캘린더 설정',
            el('ol.small',
                el('li', '컴퓨터에서 구글 캘린더(calendar.google.com)를 엽니다.'),
                el('li', '왼쪽 "내 캘린더"에서 내 이름 캘린더 옆 ⋮ → "설정 및 공유"를 누릅니다.'),
                el('li', '아래쪽 "캘린더 통합"의 "비공개 주소(iCal 형식)" 옆 복사 단추를 누릅니다. ("공개 주소"가 아닙니다.)'),
                el('li', '아래 칸에 붙여 넣고 [저장]을 누릅니다. 이 주소를 아는 사람은 일정을 볼 수 있으니 다른 곳에 붙여 넣지 마세요.'))),
        secretField('gcal_ical_url', '비공개 주소(iCal 형식)', '이 PC 의 설정 파일에만 저장되고 화면에 다시 보이지 않습니다.',
            'https://calendar.google.com/calendar/ical/…/basic.ics', refresh),
        el('h3', '② 올리기: 업무 데스크 일정 → 선생님 구글 캘린더'),
        checkField('gcal_push', '업무 데스크 일정을 내 구글 캘린더에 올리기',
            '새로 만들기·고치기·지우기를 1분 안에 반영합니다. 인터넷이 끊겨도 업무 데스크는 그대로 쓰고, 못 보낸 것은 나중에 저절로 다시 보냅니다. 선생님 본인 계정에만 올라갑니다.'),
        item('Apps Script 웹 앱 배포 스크립트 비밀 키',
            el('ol.small',
                el('li', ['아래 [스크립트 만들기]를 누르고 [스크립트 복사]를 누릅니다.']),
                el('li', '내 구글 계정으로 script.google.com 을 열고 "새 프로젝트"를 누릅니다. 이름은 "업무 데스크 일정" 처럼 정합니다.'),
                el('li', '편집기에 원래 있던 글을 모두 지우고 복사한 스크립트를 붙여 넣은 뒤 저장(💾)합니다.'),
                el('li', '오른쪽 위 "배포" → "새 배포" → 톱니(⚙) → "웹 앱"을 고릅니다.'),
                el('li', '"다음 사용자 인증 정보로 실행: 나", "액세스 권한이 있는 사용자: 모든 사용자"로 하고 "배포"를 누릅니다.'),
                el('li', '"액세스 승인" → 내 계정 → "고급" → "(안전하지 않음)으로 이동" → "허용"을 누릅니다. 내가 만든 스크립트라 이렇게 나옵니다.'),
                el('li', '나온 "웹 앱 URL"(…/exec)을 복사해 아래 칸에 붙여 넣고 [저장] → 위의 [연결 확인]을 누릅니다.')),
            el('div.row', el('button.btn.small', { type: 'button', onclick: makeScript }, '스크립트 만들기 (새 비밀 키)')),
            scriptArea),
        secretField('gcal_push_url', '웹 앱 URL', '이 PC 의 설정 파일에만 저장되고 화면에 다시 보이지 않습니다.',
            'https://script.google.com/macros/s/…/exec', refresh),
        item('올릴 일정 종류 학사일정 마감 회의 직접 추가',
            el('div.field', el('span', '올릴 일정 종류'),
                el('span.hint', '한 일정이 여러 종류에 들면 하나만 켜져 있어도 올립니다. 끄면 그 종류는 구글에서도 지웁니다. 수신함에서 체크하지 않은 일정 후보, 출결·상담 기록은 올리지 않습니다.')),
            types),
        checkField('gcal_mask', '올릴 때 학생 이름 가리기',
            '켜면 일정 제목·설명의 학생 이름(명단에 있는 이름)을 ○○○ 로 바꿔 올립니다. 선생님 본인 계정이라 기본은 끔.'),
        el('div.row', el('button.btn.small', { type: 'button', onclick: act('clear_ical', () => toast('가져오기 주소를 지웠습니다')) }, '가져오기 주소 지우기'),
            el('button.btn.small', { type: 'button', onclick: act('clear_push', () => toast('웹 앱 주소를 지웠습니다')) }, '웹 앱 주소 지우기')));
}

// 고급 항목 접기 (보통은 그대로 두는 칸)
function advanced(...children) {
    return el('details.set-adv', el('summary', '고급 — 보통은 그대로 두세요'), el('div', children));
}

const onOff = v => (v ? '켜짐' : '꺼짐');

// 설정 묶음: 왼쪽 목록에 이름·지금 상태, 오른쪽에 내용
const GROUPS = [
    {
        id: 'general', icon: '🏫', title: '일반', desc: '학교·학급·담임 이름, 창 열기 방식, 사용할 메뉴',
        status: s => (s.school_name ? `${s.school_name}${s.grade && s.class_no ? ` ${s.grade}-${s.class_no}` : ''}` : '학교 정보 없음'),
        build: () => [
            el('div.grid2',
                textField('school_name', '학교 이름', '조퇴증·외출증·결석계에 인쇄됩니다.', { placeholder: '예: ○○고등학교' }),
                textField('teacher_name', '담임 이름', '증 인쇄와 시간표에서 내 수업을 찾을 때 씁니다.'),
                textField('grade', '학년', '숫자만 (예: 2)', { placeholder: '예: 2', inputmode: 'numeric' }),
                textField('class_no', '반', '숫자만 (예: 3)', { placeholder: '예: 3', inputmode: 'numeric' })),
            checkField('app_window', '전용 창으로 열기 (권장)',
                '주소창 없는 전용 창으로 엽니다. 끄면 기본 브라우저의 새 탭. 다음에 켤 때부터 적용.'),
            item('사용할 메뉴 왼쪽 막대 숨기기',
                el('div.field', el('span', '사용할 메뉴'),
                    el('span.hint', '쓰지 않는 메뉴는 끄면 왼쪽 막대에서 숨겨집니다. 설정 메뉴는 항상 보입니다.')),
                el('div.set-menus', menuList().map(m => el('label.check',
                    el('input', { type: 'checkbox', checked: m.on, disabled: m.id === 'settings',
                        onchange: e => { setMenuOn(m.id, e.target.checked); toast(`${m.label} 메뉴 ${e.target.checked ? '켬' : '끔'}`, 'info', 1500); } }),
                    el('span', m.label))))),
            // 학교 공통 설정 파일 (0.5.92): 같은 학교 선생님께 나눠 줄 때
            item('학교 공통 설정 내보내기 불러오기 다른 선생님 나눠 주기 파일 시간표 학사일정 교시',
                el('div.field', el('span', '학교 공통 설정 (다른 선생님께 나눠 주기)'),
                    el('span.hint', '학교 이름·교시 시간·학사일정·전체 시간표만 한 파일로 주고받습니다. 학생 명단·비밀값·쪽지·공문은 담기지 않습니다.')),
                el('div.row',
                    el('button.btn.small', { type: 'button', onclick: () => openShareExport() }, '학교 공통 설정 내보내기'),
                    el('button.btn.small', { type: 'button', onclick: () => openShareImport() }, '학교 공통 설정 불러오기'))),
        ],
    },
    {
        id: 'collect', icon: '📥', title: '수집', desc: '쿨메신저 쪽지·에듀파인 공문을 읽어 오는 곳',
        status: s => [(s.coolmessenger_name || s.coolmessenger_path) && '쿨메신저', s.edufine_folder && '에듀파인'].filter(Boolean).join('·') || '수집할 곳 없음',
        build: st => [
            coolConnect(),
            pathField('edufine_folder', '에듀파인 공문 저장 폴더',
                '에듀파인에서 공문(한글·PDF)을 저장하는 폴더. 새 공문이 생기면 날짜·마감을 저절로 찾습니다.',
                'edufine', '예: C:\\Users\\선생님\\Documents\\공문', folderCheck('edufine')),
            checkField('watch_enabled', '실시간 감시 (권장)',
                '쿨메신저 쪽지 파일과 공문 폴더가 바뀌면 바로 읽습니다. PC 에 부담이 거의 없습니다.'),
            advanced(
                textField('collect_times', '하루 수집 시각',
                    '쉼표로 구분 (예: 09:00, 12:00, 15:00). 실시간 감시를 켜 두면 거의 필요 없습니다.',
                    { value: (store.settings.collect_times || []).join(', ') }),
                pathField('coolmessenger_path', '쪽지 파일 직접 지정',
                    '이름으로 쪽지 파일을 찾지 못할 때만 .udb 파일을 고르세요. 지정하면 이름보다 우선합니다.',
                    'cool', '예: D:\\CoolMessenger\\Memo\\홍길동.udb'),
                pathField('coolmessenger_folder', '쿨메신저 받은 파일 폴더',
                    '쿨메신저로 받은 한글·PDF 파일이 저장되는 폴더. 비워 두면 쪽지만 읽습니다.', 'coolfolder', '', folderCheck('cool')),
                item('PDF 읽기 pypdf', el('p.small.muted', `PDF 읽기: ${st.pdf_ready ? '준비됨' : '꺼짐 — 명령 프롬프트에서  pip install pypdf  실행 후 다시 시작'}`))),
        ],
    },
    {
        id: 'schedule', icon: '📅', title: '일정·시간표', desc: '시간표 엑셀, 학사일정(파일·나이스), 내 구글 캘린더와 공유',
        status: s => [s.neis_school?.name ? `나이스: ${s.neis_school.name}` : (s.neis_key_set ? '나이스 인증키 있음' : '파일로 불러오기'),
            (s.gcal_import || s.gcal_push) && '구글 캘린더'].filter(Boolean).join(' · '),
        build: () => [
            item('시간표 엑셀 불러오기 내 수업 수업 알림',
                el('div.field', el('span', '시간표'),
                    el('span.hint', '학교 시간표 엑셀을 불러오면 브리핑에 오늘 수업이 나오고 시간표 교체·같이 비는 시간을 찾습니다.')),
                el('div.row', el('button.btn.small', { type: 'button', onclick: openTimetable }, '시간표 화면 열기'))),
            item('학사일정 가져오기 파일 나이스 휴업일 공휴일',
                el('div.field', el('span', '학사일정'),
                    el('span.hint', '학사일정 파일(엑셀·한글)이나 나이스에서 가져오면 달력·결석계 수업일 계산에 씁니다.')),
                el('div.row', el('button.btn.small', { type: 'button', onclick: openSchoolCal }, '학사일정 가져오기'))),
            secretField('neis_key', '나이스 인증키',
                '나이스에서 학사일정을 가져올 때만 필요합니다. 교육정보 개방 포털(open.neis.go.kr)에 가입하면 무료로 받습니다.',
                'open.neis.go.kr 에서 받은 인증키'),
            item('나이스 학교', el('p.small.muted', store.settings.neis_school?.name
                ? `고른 학교: ${store.settings.neis_school.name} (학사일정 가져오기 → 🏫 나이스 에서 바꿉니다)`
                : '학교는 학사일정 가져오기 → 🏫 나이스 에서 고릅니다.')),
            el('h3.gcal-head', '📆 내 구글 캘린더와 공유 (선택, 기본 끔)'),
            el('p.small.muted', '선생님 본인 구글 계정에만 주고받습니다. 다른 사람·공개 캘린더·다른 서버로는 보내지 않습니다.'),
            gcalBox(),
        ],
    },
    {
        id: 'attendance', icon: '☑', title: '출결', desc: '학생 명단, 조퇴증·외출증 공유 페이지',
        status: () => `학생 ${store.state?.counts?.students ?? 0}명`,
        build: st => [
            item('학생 명단 붙여넣기 번호 이름 학부모 연락처',
                el('div.field', el('span', '학생 명단'),
                    el('span.hint', `지금 ${st.counts?.students ?? 0}명. 엑셀에서 번호·이름(·학부모 연락처)을 복사해 붙여 넣습니다. 명단은 이 PC 에만 저장됩니다.`)),
                el('div.row', el('button.btn.small', { type: 'button', onclick: openRoster }, '학생 명단 열기'))),
            advanced(textField('pass_page_url', '학생 공유 페이지 주소', '조퇴증·외출증 QR 이 가리키는 주소 (보통 바꿀 필요 없음)')),
        ],
    },
    {
        id: 'telegram', icon: '📱', title: '텔레그램', desc: '폰으로 알림 받고 명령 보내기 (선택)',
        status: s => (store.state?.telegram?.linked ? '연결됨' : s.telegram_token_set ? '연결 코드 대기' : '사용 안 함'),
        build: () => [
            item('텔레그램 연결 방법 BotFather 봇 만들기', TELEGRAM_STEPS()),
            secretField('telegram_token', '봇 토큰', '토큰은 이 PC 의 설정 파일에만 저장되고 화면에 다시 보이지 않습니다.', 'BotFather 가 준 토큰'),
            item('텔레그램 상태 연결 코드 시험 알림 연결 끊기', telegramStatus()),
            selectField('telegram_detail', '수집 알림에 담을 내용',
                [['counts', '개수만 (가장 안전)'], ['titles', '제목까지 (기본)'], ['summary', '요약까지']],
                '새 자료가 들어왔을 때 저절로 보내는 알림. 공문 내용이 텔레그램 서버를 거치는 것이 걱정되면 "개수만".', 'titles'),
            checkField('telegram_photos', '쿨메신저 쪽지에 사진이 있으면 텔레그램으로도 보내기',
                '새 쪽지의 사진을 한 번에 10장까지 보냅니다. 사진 속 내용은 가릴 수 없으니 학생 정보가 든 사진이 걱정되면 끄세요.'),
            checkField('telegram_cool_text', '쿨메신저 쪽지의 원문도 텔레그램으로 보내기',
                '새 쪽지의 원문을 그대로 보냅니다 (한 번에 최대 5건). 원문 안의 이름·내용도 그대로 나갑니다. 끄면 제목·요약만.'),
            checkField('telegram_calllog', '통화·문자 기록 파일을 텔레그램으로 받기',
                '폰의 백업 앱이 내보낸 통화·문자 파일(calls.xml·sms.xml·CSV)을 봇에게 보내면 학부모 번호와 맞는 것만 상담 초안으로 만들어 PC 의 상담 화면에 쌓아 둡니다(14일 뒤 지움, PC 에서 확인해야 저장). 연결된 선생님 채팅에서 온 파일만 받고, 처리한 뒤 봇이 그 파일 메시지를 지웁니다. 답장은 건수만. 단, 보낸 파일은 텔레그램 서버를 거치며 그쪽 보관은 막을 수 없습니다.'),
            checkField('telegram_mask_names', '텔레그램에서 학생 이름 가리기',
                '켜면 텔레그램 글의 학생 이름을 "김○○" 처럼 가립니다. 선생님 본인 채팅으로만 가므로 기본은 끔(이름 그대로). 조퇴·외출 사유는 설정과 관계없이 보내지 않습니다.'),
            item('봇에게 보낼 수 있는 말 명령 도움말', el('details.tg-help.small', el('summary', '봇에게 보낼 수 있는 말'),
                el('ul',
                    el('li', '브리핑 — 오늘 일정·마감 + AI 요약'),
                    el('li', '그냥 묻기 — "다음 주 연수 언제야?" 처럼. 맞는 자료를 찾아 이 PC 의 AI(LM Studio)가 읽고 답합니다'),
                    el('li', '검색 낱말 — 제목·요약과 [원문]·[파일] 단추. 원문·원본 파일은 단추를 누르거나 "원문 2"·"파일 2" 를 보낼 때만 갑니다'),
                    el('li', '조퇴 7 13:30 · 외출 7 13:30 14:30 — 번호로 발급, 사유는 단추로. 사유는 텔레그램에 쓰지 않습니다'),
                    el('li', '메뉴 · 도움말 · 취소 · 상태')))),
        ],
    },
    {
        id: 'ai', icon: '🤖', title: 'AI', desc: '이 PC 의 LM Studio 로 브리핑·수신 항목 요약 (선택)',
        status: s => (s.ai_enabled ? `켜짐 · 자동 요약 ${onOff(s.ai_auto_item !== false)}` : '꺼짐'),
        build: (st, pane) => [
            item('AI 개인정보 LM Studio 127.0.0.1', el('p.small', '쪽지·공문은 이 PC 의 LM Studio 로만 보냅니다 (주소는 127.0.0.1 만 허용, 학생 이름은 가려서 보냄).')),
            checkField('ai_enabled', 'AI 요약 쓰기', '브리핑의 요약과 수신함 항목 요약을 만듭니다. LM Studio 서버가 켜져 있어야 합니다.'),
            checkField('ai_auto_item', '새 수신 항목을 자동으로 AI 요약 (권장)',
                '최근 7일 안에 받은 쪽지·공문을 뒤에서 한 건씩 요약합니다 (한 건에 20~30초). 끄면 [AI 로 요약하기]를 눌렀을 때만.'),
            item('LM Studio 연결 확인', aiCheck()),
            item('LM Studio 설치 도우미 PC 사양 모델 추천 배치파일',
                el('details.set-adv', el('summary', 'LM Studio 설치 도우미 (PC 사양 확인·모델 추천)'), el('div', aiSetup(pane)))),
            advanced(
                textField('ai_base', 'LM Studio 서버 주소', '기본 http://127.0.0.1:1234/v1 — 이 PC 주소만 됩니다.'),
                textField('ai_model', '모델 이름', '다운로드한 모델의 고정 이름. 비워 두면 첫 대화 모델'),
                selectField('ai_input_size', '보내는 양',
                    [['normal', '보통 — 최대 14,000자 (더 꼼꼼, 4~5분)'], ['short', '짧게 — 최대 6,000자 (빠름, 2분 안팎)']],
                    '오래 걸리면 "짧게"를 고르세요.', 'normal'),
                item('처음 한 번 필요한 LM Studio 설정 메모리 해제 JIT',
                    el('details.small', el('summary', '처음 한 번 필요한 LM Studio 설정'),
                        el('ol',
                            el('li', 'LM Studio를 최신 버전으로 업데이트하고 Developer → Server Settings에서 Just-in-Time Model Loading(필요할 때 모델 불러오기)을 켜세요.'),
                            el('li', '필요할 때 불러온 모델은 5분 동안 작업이 없으면 메모리에서 내립니다. 다음 요청은 조금 느릴 수 있습니다.'),
                            el('li', '설치 도우미를 썼다면 [연결 확인 후 적용]을 눌러 모델 이름을 갱신하세요.')))),
                item('일반 질문 AI 외부 API 브라우저 내장',
                    el('p.small', '일반 질문(공개 자료만)은 로컬·브라우저 내장·외부 API 중에서 고를 수 있습니다. 업무자료는 언제나 이 PC 의 AI 로만 처리합니다.'),
                    el('button.btn.small', { type: 'button', onclick: openAI }, 'AI 선택·일반 질문 열기'))),
        ],
    },
    {
        id: 'capture', icon: '🌐', title: '브라우저 연결', desc: '에듀파인·나이스 화면을 단축키로 보내기 (선택)',
        status: s => (s.capture_key_set ? '켜짐' : '꺼짐'),
        build: () => [item('에듀파인 나이스 화면 보내기 브라우저 단축키 확장 프로그램 연결 키', captureBox())],
    },
    {
        id: 'cloud', icon: '☁', title: '드라이브 (여러 PC)', desc: '내 구글 드라이브에 암호화해 여러 PC 이어 쓰기·백업 (선택)',
        status: () => cloudLine(store.state?.cloud) || '사용 안 함',
        build: () => [cloudBox()],
    },
    {
        id: 'update', icon: '🔧', title: '업데이트·고급', desc: '새 버전, 화면 배치, 프로그램 종료',
        status: s => `${store.state?.version || ''} · 자동 업데이트 ${onOff(s.auto_update)}`,
        build: st => [
            checkField('auto_update', '켤 때 새 버전 확인',
                '켤 때 새 버전이 있으면 자동으로 바꾼 뒤 다시 켭니다 (학생 명단·기록·설정은 그대로).'),
            item('업데이트 새 버전 확인 지금 업데이트 버전', updateBox(st)),
            item('설정 도우미 다시 하기 처음 설정',
                el('div.field', el('span', '설정 도우미'), el('span.hint', '처음 설정을 단계별로 다시 확인합니다. 지금 설정은 그대로 남습니다.')),
                el('div.row', el('button.btn.small', { type: 'button', onclick: () => openSetup() }, '설정 도우미 다시 하기'))),
            advanced(
                item('화면 배치 모드', el('div.field', el('span', '화면 배치'), el('span.hint', '왼쪽·아래 영역의 크기와 위치를 바꿉니다.')),
                    el('div.row', el('button.btn.small', { type: 'button', onclick: toggleLayoutMode }, '화면 배치 모드 열기'))),
                item('프로그램 종료 끄기 백그라운드',
                    el('div.field', el('span', '프로그램 종료'),
                        el('span.hint', '브라우저를 닫아도 수집·텔레그램은 뒤에서 계속 동작합니다. 완전히 끄려면 [프로그램 종료].')),
                    el('div.row', el('button.btn.small.danger', { type: 'button', onclick: guarded(async () => {
                        if (!window.confirm('업무 데스크를 종료할까요? 자동 수집·텔레그램·수업 알림도 멈춥니다.')) return;
                        await api.post('/api/shutdown', {});
                        toast('업무 데스크를 종료했습니다. 다시 쓰려면 start-desk.bat을 실행하세요.');
                    }) }, '프로그램 종료')))),
        ],
    },
];

const GROUP_KEY = 'desk.settings.group';
let activeGroup = (() => { try { return localStorage.getItem(GROUP_KEY) || 'general'; } catch { return 'general'; } })();
if (!GROUPS.some(g => g.id === activeGroup)) activeGroup = 'general';

function showGroup(pane, id) {
    activeGroup = id;
    try { localStorage.setItem(GROUP_KEY, id); } catch { /* 저장 못 해도 괜찮음 */ }
    for (const b of pane.querySelectorAll('.set-nav button')) {
        const on = b.dataset.group === id;
        b.classList.toggle('on', on);
        b.setAttribute('aria-current', on ? 'true' : 'false');
    }
    for (const sec of pane.querySelectorAll('.set-sec')) sec.hidden = sec.dataset.group !== id;
    pane.querySelector('.set-main')?.scrollTo?.(0, 0);
}

// 검색: 항목 이름·설명에 낱말이 든 칸만 묶음에 상관없이 보여 줌 (지우면 원래 묶음으로)
function applySearch(pane, query) {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const note = pane.querySelector('.set-found');
    pane.classList.toggle('searching', !!words.length);
    if (!words.length) {
        for (const it of pane.querySelectorAll('.set-item')) it.hidden = false;
        clear(note);
        showGroup(pane, activeGroup);
        return;
    }
    let found = 0;
    for (const sec of pane.querySelectorAll('.set-sec')) {
        const head = sec.dataset.search;
        let n = 0;
        // 맨 바깥 칸만 셈 (고급 묶음 안의 칸도 각자 셈)
        for (const it of sec.querySelectorAll('.set-item')) {
            const hit = matches(`${it.dataset.search} ${head}`, query);
            it.hidden = !hit;
            if (hit) n++;
        }
        for (const adv of sec.querySelectorAll('details.set-adv')) {
            if ([...adv.querySelectorAll('.set-item')].some(it => !it.hidden)) adv.open = true;
        }
        sec.hidden = !n;
        found += n;
    }
    for (const b of pane.querySelectorAll('.set-nav button')) b.classList.remove('on');
    clear(note, found ? `찾은 항목 ${found}개` : '찾는 항목이 없습니다. 다른 낱말로 찾아 보세요 (예: 텔레그램, 폴더, 시각).');
}

function navStatus(g) {
    try { return g.status(store.settings) || ''; } catch { return ''; }
}

function renderSettings(pane) {
    const st = store.state || {};
    const search = el('input.set-search', { type: 'search', placeholder: '설정 찾기 (예: 텔레그램, 폴더, 수집 시각)', 'aria-label': '설정 찾기',
        oninput: e => applySearch(pane, e.target.value),
        onkeydown: e => { if (e.key === 'Escape') { e.target.value = ''; applySearch(pane, ''); } } });
    const nav = el('nav.set-nav', { 'aria-label': '설정 묶음' }, GROUPS.map(g => el('button', {
        type: 'button', dataset: { group: g.id },
        onclick: () => { if (search.value) { search.value = ''; applySearch(pane, ''); } showGroup(pane, g.id); },
    }, el('span.set-ico', g.icon), el('span.set-nt', el('b', g.title), el('span.set-ns', navStatus(g))))));
    const sections = GROUPS.map(g => el('section.set-sec', { dataset: { group: g.id, search: `${g.title} ${g.desc}`.toLowerCase() } },
        el('h2', `${g.icon} ${g.title}`), el('p.set-desc', g.desc), g.build(st, pane)));
    pane.classList.add('set-pane');
    pane.append(
        el('div.set-head',
            el('div', el('h1', '설정'),
                el('p.sub', `업무 데스크 ${st.version || ''} · 모든 자료는 이 PC 의 data 폴더에만 저장됩니다.`)),
            el('div.set-tools', saveStateBox(),
                el('button.btn.small', { type: 'button', onclick: () => openSetup() }, '🧭 설정 도우미'))),
        el('div.set-searchrow', search, el('span.small.muted.set-found', { role: 'status' })),
        el('div.set-layout', nav, el('div.set-main', sections)));
    showGroup(pane, activeGroup);
    autoSave(pane);
}

// 왼쪽 목록의 상태 한 줄만 새로 씀 (입력 중인 칸은 건드리지 않음)
function refreshNav() {
    const tab = findTab('settings');
    if (!tab) return;
    for (const g of GROUPS) {
        const s = tab.pane.querySelector(`.set-nav button[data-group="${g.id}"] .set-ns`);
        if (s) s.textContent = navStatus(g);
    }
}

export function openSettings(group) {
    if (group && GROUPS.some(g => g.id === group)) activeGroup = group;
    const tab = openTab({ id: 'settings', title: '설정', menu: true, render: renderSettings });
    if (group && tab?.pane.querySelector('.set-nav')) showGroup(tab.pane, activeGroup);
    return tab;
}

registerView({
    id: 'settings', icon: '⚙', label: '설정', bottom: true, noSide: true,
    renderSide(body) {
        body.append(el('ul.list',
            el('li', { onclick: () => openSettings() }, el('span.ttl', '설정 열기')),
            el('li', { onclick: () => openSetup() }, el('span.ttl', '설정 도우미')),
            el('li', { onclick: toggleLayoutMode }, el('span.ttl', '화면 배치 모드'))));
    },
    open: () => openSettings(),
});

// 텔레그램 연결 상태 등이 바뀌면 그 자리만 다시 그림 (예전처럼 화면 전체를 다시 그리지 않아 입력·검색이 지워지지 않음)
on('state', () => { refreshTelegramBoxes(); refreshNav(); });
on('settings', refreshNav);
