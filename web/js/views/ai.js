// 일반 질문 전용: 수신함·명단·출결·브리핑 자료를 자동으로 붙이지 않음.
import { api } from '../api.js';
import { askBrowser, browserAvailability } from '../browser-ai.js';
import { el, clear, guarded, toast } from '../ui.js';
import { registerView, openTab } from '../shell.js';
import { store, saveSettings } from '../store.js';
import { modelPicker } from './setfields.js';

const MODES = [['local', '로컬 — 이 PC의 LM Studio'], ['browser', '브라우저 내장 — 기기 내 실행'],
    ['openai', '외부 API — OpenAI (GPT)'], ['anthropic', '외부 API — Claude'], ['gemini', '외부 API — Gemini'],
    ['claude_cli', 'CLI — Claude Code (이 PC에 설치·로그인)'], ['gemini_cli', 'CLI — Gemini CLI (이 PC에 설치·로그인)'],
    ['codex_cli', 'CLI — Codex CLI (이 PC에 설치·로그인)']];
// 0.6.24 CLI 모델: API 키 없이 이 PC 에 설치·로그인한 명령줄 AI 를 부름 (질문은 그 회사로 나가므로 외부 AI 와 같이 매번 확인)
const CLI = { claude_cli: 'claude', gemini_cli: 'gemini', codex_cli: 'codex' };
const LINKS = { openai: 'https://platform.openai.com/api-keys', anthropic: 'https://console.anthropic.com/',
    gemini: 'https://aistudio.google.com/apikey' };

export function openAI() {
    let controller;
    return openTab({ id: 'ai', title: 'AI 질문', menu: true, onClose: () => controller?.abort(), render(pane) {
        const mode = el('select', MODES.map(([value, name]) =>
            el('option', { value, selected: store.settings.ai_provider === value }, name)));
        const model = el('input', { type: 'text', maxLength: 120, autocomplete: 'off' });
        const key = el('input', { type: 'password', maxLength: 512, autocomplete: 'new-password' });
        const keyHint = el('span.hint');
        const link = el('a', { target: '_blank', rel: 'noopener noreferrer' }, '공식 API 키 관리 페이지');
        const notice = el('p.small');
        // 0.6.4 모델 목록: [목록 불러오기]를 누를 때만 그 회사에 API 키로 목록을 물어봄 (질문·학교 자료는 보내지 않음)
        const picker = modelPicker(model, async () =>
            (await api.post('/api/ai/models', { provider: mode.value, key: CLI[mode.value] ? '' : key.value })).models, { label: '모델 목록' });
        const keyBox = el('div',
            el('label.field', el('span', 'API 키 (이 PC에 저장, 화면에는 다시 표시하지 않음)'), key, keyHint), el('div.row', { style: 'margin-bottom: 12px' }, link));
        const modelLabel = el('span', 'API 모델 (목록에서 고르기)');
        const modelHint = el('p.small.muted');
        const details = el('div', keyBox,
            el('div.field', modelLabel, picker, modelHint),
            el('label.field', el('span', '목록에 없으면 직접 입력 (서비스에서 제공하는 정확한 이름)'), model));
        const status = el('p.small', { role: 'status', 'aria-live': 'polite' });
        const answer = el('pre.text', { style: 'white-space: pre-wrap; overflow-wrap: anywhere', role: 'status' });
        const question = el('textarea', { rows: 5, maxLength: 4000, placeholder: '공개 자료나 일반 지식 질문을 입력하세요.' });
        const consent = el('input', { type: 'checkbox' });
        question.addEventListener('input', () => { consent.checked = false; });
        const consentLabel = el('label.check', consent, '학생 정보·비공개 업무자료가 없는 공개 질문이며 외부 전송·요금 발생을 확인했습니다 (매번 확인).');
        const busy = value => {
            for (const control of [mode, model, key, save, send, probe, removeKey, question, consent, ...picker.querySelectorAll('select, button')]) control.disabled = value;
        };
        const update = () => {
            const cli = !!CLI[mode.value];
            const external = !!LINKS[mode.value] || cli;
            details.hidden = !external;
            keyBox.hidden = cli;
            modelLabel.textContent = cli ? 'CLI 모델 (목록에서 고르기, 비우면 그 CLI 의 기본 모델)' : 'API 모델 (목록에서 고르기)';
            modelHint.textContent = cli
                ? `[목록 불러오기]는 이 PC 에 '${CLI[mode.value]}' 명령이 설치돼 있는지 보고 자주 쓰는 모델 이름을 보여 줍니다. 질문은 보내지 않습니다. 고른 뒤 [선택·API 설정 저장]을 누르세요.`
                : '목록은 입력한(또는 저장한) API 키로 그 회사에서 받아 옵니다. 질문은 보내지 않습니다. 고른 뒤 [선택·API 설정 저장]을 누르세요.';
            consentLabel.hidden = !external;
            consent.checked = false;
            key.value = '';
            model.value = store.settings[`ai_${mode.value}_model`] || '';
            picker.reset();
            keyHint.textContent = store.settings[`ai_${mode.value}_key_set`] ? '키가 저장되어 있습니다. 비워 두면 유지합니다. 삭제 단추로 지울 수 있습니다.' : '아직 키를 저장하지 않았습니다.';
            link.href = LINKS[mode.value] || '#';
            notice.textContent = cli
                ? `이 PC 에 설치하고 로그인해 둔 명령줄 AI(${CLI[mode.value]})를 부릅니다. API 키는 필요 없고 그 서비스 구독의 이용 한도를 씁니다. 입력한 질문만 해당 회사로 전송되고 제공사의 보관 정책이 적용됩니다. 개인정보·내부 공문·쪽지를 붙여넣지 마세요. 업무자료 요약·검색은 이 선택과 관계없이 항상 이 PC 의 LM Studio 를 씁니다.`
                : external
                ? '입력한 질문만 해당 회사로 전송됩니다. 별도 API 요금과 제공사의 보관 정책이 적용됩니다. ChatGPT·Claude 구독과 API 요금은 별개입니다. 개인정보·내부 공문·쪽지를 붙여넣지 마세요. 보조 검사는 모든 민감정보를 찾아내지 못합니다.'
                : mode.value === 'browser'
                    ? '지원되는 브라우저·PC에서만 동작합니다. 모델 최초 다운로드에는 인터넷과 저장 공간이 필요합니다. 현재는 영어 질문만 제공합니다. 브라우저가 닫히면 실행할 수 없으며 텔레그램 자동 응답에는 쓸 수 없습니다.'
                    : '설정의 LM Studio 주소·모델을 사용합니다. 업무자료 요약·검색은 이 선택과 관계없이 항상 로컬 AI를 사용합니다.';
            clear(status);
            clear(answer);
        };
        const save = el('button.btn', { onclick: guarded(async () => {
            const changes = { ai_provider: mode.value };
            if (CLI[mode.value]) changes[`ai_${mode.value}_model`] = model.value;
            if (LINKS[mode.value]) {
                changes[`ai_${mode.value}_model`] = model.value;
                if (key.value.trim()) changes[`ai_${mode.value}_key`] = key.value;
            }
            busy(true);
            try {
                await saveSettings(changes);
                update();
                toast('AI 사용 방식을 저장했습니다.');
            } finally { busy(false); }
        }) }, '선택·API 설정 저장');
        const removeKey = el('button.btn.small.danger', { onclick: guarded(async () => {
            if (!window.confirm('이 서비스의 저장된 API 키를 지울까요?')) return;
            busy(true);
            try {
                await saveSettings({ [`ai_${mode.value}_key`]: '' });
                key.value = '';
                keyHint.textContent = 'API 키를 삭제했습니다.';
            } finally { busy(false); }
        }) }, '이 서비스 API 키 삭제');
        link.after(removeKey);
        const probe = el('button.btn', { onclick: guarded(async () => {
            status.textContent = '브라우저 내장 AI 지원 여부를 확인하는 중…';
            try {
                const availability = await browserAvailability();
                const labels = { available: '사용 가능', downloadable: '첫 질문 시 모델 다운로드 필요',
                    downloading: '모델 다운로드 중', unavailable: '이 PC 또는 브라우저에서 사용 불가' };
                status.textContent = labels[availability] || `지원 상태: ${availability}`;
            } catch (e) { status.textContent = e.message; }
        }) }, '브라우저 지원 확인');
        const send = el('button.btn.primary', { onclick: guarded(async () => {
            const selected = mode.value;
            const text = question.value.trim();
            if (!text) throw new Error('질문을 입력하세요.');
            if ((LINKS[selected] || CLI[selected]) && (!consent.checked || key.value.trim() || model.value !== (store.settings[`ai_${selected}_model`] || ''))) {
                throw new Error('API 설정을 먼저 저장하고 외부 전송 확인을 체크하세요.');
            }
            controller = new AbortController();
            busy(true);
            status.textContent = '답변을 만드는 중… (자동 재시도하지 않습니다)';
            clear(answer);
            const confirmed = consent.checked;
            consent.checked = false;
            try {
                answer.textContent = selected === 'browser'
                    ? await askBrowser(text, { signal: controller.signal, progress: message => { status.textContent = message; } })
                    : (await api.post('/api/ai/question', { provider: selected, question: text, confirmed })).text;
                status.textContent = '완료 · AI 답변은 틀릴 수 있으니 중요한 내용은 확인하세요. 질문·답변은 기록에 저장하지 않습니다.';
            } catch (e) {
                status.textContent = e.name === 'AbortError' ? '브라우저 AI 실행을 중단했습니다.' : e.message;
            } finally { busy(false); controller = null; }
        }) }, '질문 보내기');
        mode.onchange = update;
        update();
        pane.append(el('h1', 'AI 질문'),
            el('p.sub', '일반 질문용 AI를 선택합니다. 수신함·학생 명단·출결 자료를 자동으로 보내지 않으며 이전 대화도 붙이지 않습니다.'),
            el('section.card', el('label.field', el('span', 'AI 사용 방식'), mode), notice, details,
                el('div.row', save, probe)),
            el('section.card', el('label.field', el('span', '일반 질문'), question), consentLabel, send, status, answer));
    } });
}

registerView({ id: 'ai', icon: 'AI', label: 'AI 질문', noSide: true, renderSide() {}, open: openAI });
