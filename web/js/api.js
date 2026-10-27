// 로컬 서버와 주고받기. 서버가 화면에 심어 준 토큰을 함께 보냅니다.
const TOKEN = document.querySelector('meta[name="desk-token"]').content;

export class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

async function request(method, path, body) {
    const opts = { method, headers: { 'X-Desk-Token': TOKEN } };
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
