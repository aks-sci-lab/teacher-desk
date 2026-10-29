// 브라우저가 제공하는 기기 내 모델만 사용. 외부 AI로 자동 전환하지 않음.
const OPTIONS = {
    expectedInputs: [{ type: 'text', languages: ['en'] }],
    expectedOutputs: [{ type: 'text', languages: ['en'] }],
};

function modelAPI() {
    if (!globalThis.isSecureContext || !globalThis.LanguageModel) {
        throw new Error('이 브라우저에서는 내장 AI를 사용할 수 없습니다. 지원되는 최신 데스크톱 브라우저 또는 로컬 AI를 사용하세요.');
    }
    return globalThis.LanguageModel;
}

export async function browserAvailability() {
    return modelAPI().availability(OPTIONS);
}

export async function askBrowser(question, { signal, progress = () => {} } = {}) {
    if (typeof question !== 'string' || !question.trim() || question.length > 4000) {
        throw new Error('일반 질문은 1~4000자로 입력하세요.');
    }
    if (/[가-힣ㄱ-ㅎㅏ-ㅣ]/u.test(question)) {
        throw new Error('현재 브라우저 내장 AI의 한국어 지원은 보장되지 않습니다. 영어로 질문하거나 로컬·외부 API를 선택하세요.');
    }
    // create는 사용자의 클릭 안에서 호출해야 모델 다운로드를 승인받을 수 있음.
    const session = await modelAPI().create({
        ...OPTIONS, signal,
        monitor(monitor) {
            monitor.addEventListener('downloadprogress', event => {
                progress(`브라우저 모델 다운로드 ${Math.round(event.loaded * 100)}%`);
            });
        },
    });
    try {
        const answer = await session.prompt(question, { signal });
        if (typeof answer !== 'string' || !answer.trim()) throw new Error('브라우저 내장 AI가 답변을 반환하지 않았습니다.');
        return answer;
    } finally {
        session.destroy();
    }
}
