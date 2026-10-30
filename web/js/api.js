// 로컬 서버와 주고받기. 서버가 화면에 심어 준 토큰을 함께 보냅니다.
const TOKEN = document.querySelector('meta[name="desk-token"]').content;

// 이 화면의 번호: 서버가 '열려 있는 화면'과 '쓰는 화면'을 알 수 있게 모든 요청에 실어 보냄 (자동 인쇄 창은 제외)
export const CLIENT_ID = new URLSearchParams(location.search).has('silent-print') ? null : crypto.randomUUID();
const clientHeaders = () => (CLIENT_ID ? { 'X-Desk-Client': CLIENT_ID } : {});

// 창이 닫힐 때 서버에 알림 (닫는 도중에도 끝까지 보내려고 keepalive)
export function sendBye() {
    if (!CLIENT_ID) return;
    try {
        fetch('/api/ui/bye', { method: 'POST', keepalive: true,
            headers: { 'X-Desk-Token': TOKEN, 'Content-Type': 'application/json', ...clientHeaders() },
            body: JSON.stringify({ id: CLIENT_ID }) });
    } catch (e) { /* 이미 닫히는 중 */ }
}

export class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

async function request(method, path, body) {
    const opts = { method, headers: { 'X-Desk-Token': TOKEN, ...clientHeaders() } };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    let res;
    try {
        res = await fetch(path, opts);
    } catch (e) {
        throw new ApiError(0, '업무 데스크 프로그램이 꺼져 있습니다. start-desk.bat 을 다시 실행하세요.');
    }
    const type = res.headers.get('Content-Type') || '';
    if (!type.includes('application/json')) {
        if (!res.ok) throw new ApiError(res.status, `요청 실패 (${res.status})`);
        return res;
    }
    const data = await res.json();
    if (!res.ok) throw new ApiError(res.status, data.error || `요청 실패 (${res.status})`);
    return data;
}

export const api = {
    get: path => request('GET', path),
    post: (path, body = {}) => request('POST', path, body),
    put: (path, body = {}) => request('PUT', path, body),
    patch: (path, body = {}) => request('PATCH', path, body),
    del: path => request('DELETE', path),
};
