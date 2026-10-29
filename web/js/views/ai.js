// 일반 질문 전용: 수신함·명단·출결·브리핑 자료를 자동으로 붙이지 않음.
import { api } from '../api.js';
import { askBrowser, browserAvailability } from '../browser-ai.js';
import { el, clear, guarded, toast } from '../ui.js';
import { registerView, openTab } from '../shell.js';
import { store, saveSettings } from '../store.js';

const MODES = [['local', '로컬 — 이 PC의 LM Studio'], ['browser', '브라우저 내장 — 기기 내 실행'],
    ['openai', '외부 API — OpenAI (GPT)'], ['anthropic', '외부 API — Claude'], ['gemini', '외부 API — Gemini']];
const LINKS = { openai: 'https://platform.openai.com/api-keys', anthropic: 'https://console.anthropic.com/',
    gemini: 'https://aistudio.google.com/apikey' };

export function openAI() {
    let controller;
    return openTab({ id: 'ai', title: 'AI 질문', onClose: () => controller?.abort(), render(pane) {
        const mode = el('select', MODES.map(([value, name]) =>
            el('option', { value, selected: store.settings.ai_provider === value }, name)));
        const model = el('input', { type: 'text', maxLength: 120, autocomplete: 'off' });
        const key = el('input', { type: 'password', maxLength: 512, autocomplete: 'new-password' });
        const keyHint = el('span.hint');
        const link = el('a', { target: '_blank', rel: 'noopener noreferrer' }, '공식 API 키 관리 페이지');
        const notice = el('p.small');
        const details = el('div', el('label.field', el('span', 'API 모델 이름 (서비스에서 제공하는 정확한 이름)'), model),
            el('label.field', el('span', 'API 키 (이 PC에 저장, 화면에는 다시 표시하지 않음)'), key, keyHint), link);
        const status = el('p.small', { role: 'status', 'aria-live': 'polite' });
        const answer = el('pre.text', { style: 'white-space: pre-wrap; overflow-wrap: anywhere', role: 'status' });
        const question = el('textarea', { rows: 5, maxLength: 4000, placeholder: '공개 자료나 일반 지식 질문을 입력하세요.' });
        const consent = el('input', { type: 'checkbox' });
        question.addEventListener('input', () => { consent.checked = false; });
        const consentLabel = el('label.check', consent, '학생 정보·비공개 업무자료가 없는 공개 질문이며 외부 전송·요금 발생을 확인했습니다 (매번 확인).');
        const busy = value => {
            for (const control of [mode, model, key, save, send, probe, removeKey, question, consent]) control.disabled = value;
        };
        const update = () => {
            const external = !!LINKS[mode.value];
            details.hidden = !external;
            consentLabel.hidden = !external;
            consent.checked = false;
            key.value = '';
            model.value = store.settings[`ai_${mode.value}_model`] || '';
            keyHint.textContent = store.settings[`ai_${mode.value}_key_set`] ? '키가 저장되어 있습니다. 비워 두면 유지합니다. 삭제 단추로 지울 수 있습니다.' : '아직 키를 저장하지 않았습니다.';
            link.href = LINKS[mode.value] || '#';
            notice.textContent = external
                ? '입력한 질문만 해당 회사로 전송됩니다. 별도 API 요금과 제공사의 보관 정책이 적용됩니다. ChatGPT·Claude 구독과 API 요금은 별개입니다. 개인정보·내부 공문·쪽지를 붙여넣지 마세요. 보조 검사는 모든 민감정보를 찾아내지 못합니다.'
                : mode.value === 'browser'
                    ? '지원되는 브라우저·PC에서만 동작합니다. 모델 최초 다운로드에는 인터넷과 저장 공간이 필요합니다. 현재는 영어 질문만 제공합니다. 브라우저가 닫히면 실행할 수 없으며 텔레그램 자동 응답에는 쓸 수 없습니다.'
                    : '설정의 LM Studio 주소·모델을 사용합니다. 업무자료 요약·검색은 이 선택과 관계없이 항상 로컬 AI를 사용합니다.';
            clear(status);
            clear(answer);
        };
        const save = el('button.btn', { onclick: guarded(async () => {
            const changes = { ai_provider: mode.value };
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
        details.append(removeKey);
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
            if (LINKS[selected] && (!consent.checked || key.value.trim() || model.value !== (store.settings[`ai_${selected}_model`] || ''))) {
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
