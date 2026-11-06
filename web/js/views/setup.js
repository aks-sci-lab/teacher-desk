// 🧭 첫 실행 설정 도우미 (0.5.79): 처음 쓰는 선생님이 꼭 필요한 설정을 차례대로 채움.
// ① 학교·학급 ② 쿨메신저·에듀파인 ③ 시간표·학사일정·명단 ④ 텔레그램(선택) ⑤ AI(선택) ⑥ 완료
// - 칸은 설정 화면과 같은 부품이라 고치면 바로 저장됩니다(다음을 누르지 않아도 남음).
// - 도우미 상태는 설정 키 setup_wizard 하나: '' 처음 · 'later' 나중에 · 'done' 마침 · 'notified' 기존 사용자에게 한 번 안내함
import { el, clear, toast, guarded } from '../ui.js';
import { openTab, findTab, closeTab, showView } from '../shell.js';
import { store, loadState, saveSettings } from '../store.js';
import { openTimetable } from './timetable.js';
import { openSchoolCal } from './schoolcal.js';
import { openRoster } from './passes.js';
import { openSettings } from './settings.js';
import {
    item, textField, pathField, checkField, selectField, secretField, autoSave, saveStateBox,
    coolConnect, folderCheck, telegramStatus, aiCheck, TELEGRAM_STEPS,
} from './setfields.js';
import { setupAction } from '../setup-rules.js';

const has = v => !!(v && String(v).trim());

// 단계: 제목, 한 줄 설명, 선택 단계인지, 채워졌는지(완료 요약용), 내용
export const STEPS = [
    {
        id: 'school', title: '학교·학급', desc: '조퇴증·외출증·결석계에 인쇄되고, 시간표에서 내 수업과 우리 반을 찾을 때 씁니다.',
        done: s => has(s.school_name) && has(s.teacher_name),
        summary: s => [s.school_name, s.grade && s.class_no && `${s.grade}학년 ${s.class_no}반`, s.teacher_name].filter(has).join(' · '),
        build: () => [
            el('div.grid2',
                textField('school_name', '학교 이름', null, { placeholder: '예: ○○고등학교' }),
                textField('teacher_name', '담임 이름', null, { placeholder: '예: 홍길동' }),
                textField('grade', '학년', '숫자만', { placeholder: '예: 2', inputmode: 'numeric' }),
                textField('class_no', '반', '숫자만', { placeholder: '예: 3', inputmode: 'numeric' })),
        ],
    },
    {
        id: 'collect', title: '쪽지·공문 수집', desc: '쿨메신저 쪽지와 에듀파인 공문을 읽어 날짜·마감을 찾아 줍니다. 쓰는 것만 채우세요.',
        done: s => has(s.coolmessenger_name) || has(s.coolmessenger_path) || has(s.edufine_folder),
        summary: s => [(has(s.coolmessenger_name) || has(s.coolmessenger_path)) && '쿨메신저', has(s.edufine_folder) && '에듀파인 폴더'].filter(Boolean).join(' · '),
        build: () => [
            coolConnect(),
            pathField('edufine_folder', '에듀파인 공문 저장 폴더',
                '에듀파인에서 공문(한글·PDF)을 저장하는 폴더를 고르세요. [찾아보기]로 고르면 바로 저장됩니다.',
                'edufine', '예: C:\\Users\\선생님\\Documents\\공문', folderCheck('edufine')),
            el('p.small.muted', '쿨메신저 파일 위치를 직접 고르거나 수집 시각을 바꾸는 것은 나중에 ⚙ 설정 → 수집 → 고급에서 할 수 있습니다.'),
        ],
    },
    {
        id: 'schedule', title: '시간표·학사일정·명단', desc: '파일을 불러오는 일이라 각 화면이 열립니다. 이 도우미 탭은 위 탭 줄에 그대로 남아 있습니다.',
        done: s => (store.state?.counts?.students || 0) > 0 || has(s.neis_school?.name),
        summary: s => [(store.state?.counts?.students || 0) > 0 && `학생 ${store.state.counts.students}명`, s.neis_school?.name && `나이스 ${s.neis_school.name}`, s.neis_key_set && !s.neis_school?.name && '나이스 인증키'].filter(Boolean).join(' · '),
        build: () => [
            item('', el('div.setup-cards',
                el('div.card', el('b', '▦ 시간표'), el('p.small', '학교 시간표 엑셀을 불러오면 브리핑에 오늘 수업이 나옵니다.'),
                    el('button.btn.small', { type: 'button', onclick: openTimetable }, '시간표 불러오기')),
                el('div.card', el('b', '📅 학사일정'), el('p.small', '학사일정 파일이나 나이스에서 가져오면 달력과 결석계 수업일 계산에 씁니다.'),
                    el('button.btn.small', { type: 'button', onclick: openSchoolCal }, '학사일정 가져오기')),
                el('div.card', el('b', '☑ 학생 명단'), el('p.small', `지금 ${store.state?.counts?.students ?? 0}명. 엑셀에서 번호·이름을 복사해 붙여 넣습니다.`),
                    el('button.btn.small', { type: 'button', onclick: openRoster }, '명단 붙여넣기')))),
            secretField('neis_key', '(선택) 나이스 인증키',
                '나이스에서 학사일정을 가져올 때만 필요합니다. open.neis.go.kr 에 가입하면 무료로 받습니다.', 'open.neis.go.kr 에서 받은 인증키'),
        ],
    },
    {
        id: 'telegram', title: '텔레그램', optional: true, desc: '폰으로 새 자료 알림을 받고, 조퇴증 발급·검색 같은 명령을 보냅니다. 쓰지 않으면 건너뛰세요.',
        done: s => !!store.state?.telegram?.linked || !!s.telegram_token_set,
        summary: () => (store.state?.telegram?.linked ? '연결됨' : store.settings.telegram_token_set ? '토큰 저장됨 · 연결 코드 대기' : ''),
        build: () => [
            TELEGRAM_STEPS(),
            secretField('telegram_token', '봇 토큰', '토큰은 이 PC 의 설정 파일에만 저장되고 화면에 다시 보이지 않습니다.', 'BotFather 가 준 토큰'),
            telegramStatus(),
            selectField('telegram_detail', '수집 알림에 담을 내용',
                [['counts', '개수만 (가장 안전)'], ['titles', '제목까지 (기본)'], ['summary', '요약까지']],
                '사진·원문 전달 같은 나머지 텔레그램 설정은 ⚙ 설정 → 텔레그램에 있습니다.', 'titles'),
        ],
    },
    {
        id: 'ai', title: 'AI 요약', optional: true, desc: '이 PC 의 LM Studio 로 브리핑과 쪽지·공문을 요약합니다. 학교 자료는 PC 밖으로 나가지 않습니다.',
        done: s => !!s.ai_enabled,
        summary: s => (s.ai_enabled ? '켜짐' : ''),
        build: () => [
            item('', el('p.small', 'LM Studio 를 아직 설치하지 않았다면 건너뛰고, 나중에 ⚙ 설정 → AI → 설치 도우미에서 PC 사양 확인·모델 추천을 받으세요.')),
            item('', aiCheck()),
            checkField('ai_enabled', 'AI 요약 쓰기', 'LM Studio 서버가 켜져 있어야 합니다. 위 [LM Studio 연결 확인]으로 먼저 확인하세요.'),
        ],
    },
];

function notice(text) {
    toast(el('span.setup-toast', text, ' ', el('button.btn.small', { type: 'button', onclick: () => openSetup() }, '설정 도우미 열기')), 'info', 12000);
}

export async function maybeStartSetup(s) {
    const action = setupAction(s);
    if (action === 'open') {
        openSetup();
        await saveSettings({ setup_wizard: 'later' }).catch(() => {});   // 도중에 닫으면 다음에는 안내만
    } else if (action === 'notify') {
        notice('새 설정 도우미가 생겼습니다. 지금 설정은 그대로이며, 필요하면 단계별로 다시 확인할 수 있습니다.');
        await saveSettings({ setup_wizard: 'notified' }).catch(() => {});
    } else if (action === 'remind') {
        notice('설정을 마치지 않았습니다. 이어서 하려면 설정 도우미를 여세요.');
    }
}

let current = 0;
const skipped = new Set();

function renderSetup(pane) {
    pane.classList.add('setup-pane');
    const progress = el('ol.setup-progress', { 'aria-label': '진행 단계' });
    const body = el('div.setup-body');
    const foot = el('div.setup-foot');
    const total = STEPS.length + 1;   // 마지막 '완료'

    const go = n => { current = Math.max(0, Math.min(total - 1, n)); draw(); };
    const later = guarded(async () => {
        await saveSettings({ setup_wizard: 'later' });
        toast('언제든 ⚙ 설정 → 🧭 설정 도우미에서 이어서 할 수 있습니다.', 'info', 5000);
        closeTab('setup');
    });
    const finish = guarded(async () => {
        await saveSettings({ setup_wizard: 'done' });
        await loadState();
        toast('설정을 마쳤습니다. 바꿀 것이 있으면 ⚙ 설정에서 언제든 고치세요.', 'info', 5000);
        closeTab('setup');
        showView('today');
    });

    const draw = () => {
        const s = store.settings;
        clear(progress, [...STEPS, { id: 'end', title: '완료' }].map((st, i) => el('li', {
            className: [i === current && 'on', i < current && 'past', st.done?.(s) && 'filled', skipped.has(st.id) && 'skip'].filter(Boolean).join(' '),
        }, el('button', { type: 'button', 'aria-current': i === current ? 'step' : 'false', onclick: () => go(i) },
            el('span.n', st.done?.(s) ? '✓' : String(i + 1)), el('span.t', st.title), st.optional && el('span.opt', '선택')))));

        if (current < STEPS.length) {
            const st = STEPS[current];
            clear(body,
                el('h2', `${current + 1}. ${st.title}`, st.optional ? el('span.set-badge', '선택') : null),
                el('p.set-desc', st.desc),
                st.build());
            clear(foot,
                el('button.btn', { type: 'button', disabled: current === 0, onclick: () => go(current - 1) }, '◀ 이전'),
                el('span.grow'),
                el('button.btn.small', { type: 'button', onclick: later }, '나중에 하기'),
                el('button.btn', { type: 'button', onclick: () => { skipped.add(st.id); go(current + 1); } }, '건너뛰기'),
                el('button.btn.primary', { type: 'button', onclick: () => { skipped.delete(st.id); go(current + 1); } }, '다음 ▶'));
        } else {
            clear(body,
                el('h2', `${total}. 완료`),
                el('p.set-desc', '아래는 지금 설정입니다. 비어 있는 단계는 눌러서 다시 채우거나, 나중에 ⚙ 설정에서 할 수 있습니다.'),
                el('ul.setup-summary', STEPS.map((st, i) => {
                    const ok = st.done(s);
                    return el('li', { className: ok ? 'ok' : 'empty' },
                        el('span.mark', ok ? '✓' : '–'),
                        el('b', st.title),
                        el('span.muted', ok ? (st.summary(s) || '설정됨') : (st.summary(s) ? `${st.summary(s)} (덜 채움)` : (skipped.has(st.id) ? '건너뜀' : '비어 있음') + (st.optional ? ' (선택)' : ''))),
                        el('button.btn.small', { type: 'button', onclick: () => go(i) }, ok ? '고치기' : '채우기'));
                })),
                el('p.small.muted', '이 도우미는 ⚙ 설정 → 🧭 설정 도우미에서 언제든 다시 열 수 있습니다.'));
            clear(foot,
                el('button.btn', { type: 'button', onclick: () => go(current - 1) }, '◀ 이전'),
                el('span.grow'),
                el('button.btn.small', { type: 'button', onclick: () => openSettings() }, '설정 화면 열기'),
                el('button.btn.primary', { type: 'button', onclick: finish }, '설정 마치기'));
        }
        pane.scrollTo?.(0, 0);
    };

    pane.append(
        el('div.set-head',
            el('div', el('h1', '🧭 설정 도우미'),
                el('p.sub', '처음 한 번만 차례대로 채우면 됩니다. 칸을 고치면 바로 저장되고, 모르는 단계는 건너뛰어도 됩니다.')),
            el('div.set-tools', saveStateBox())),
        progress, el('div.card.setup-card', body), foot);
    autoSave(pane);
    draw();
}

export function openSetup(step = 0) {
    current = step;
    const tab = findTab('setup');
    if (tab) { tab.rerender(); }
    return openTab({ id: 'setup', title: '설정 도우미', render: renderSetup });
}
