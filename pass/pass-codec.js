// 조퇴증·외출증 공유 링크 만들기/읽기
// 증의 내용은 주소의 # 뒤에만 들어갑니다. # 뒤는 서버로 전송되지 않아 사이트에 학생 정보가 저장·기록되지 않습니다.
// 교사 업무 데스크(teacher-desk/)와 공개 페이지(pass/index.html)가 함께 씁니다.
(function (root) {
    'use strict';

    const KINDS = { early: '조퇴', out: '외출' };

    function toBase64Url(bytes) {
        let bin = '';
        for (const b of bytes) bin += String.fromCharCode(b);
        const b64 = typeof btoa === 'function' ? btoa(bin) : Buffer.from(bin, 'binary').toString('base64');
        return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    function fromBase64Url(s) {
        const b64 = s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4);
        const bin = typeof atob === 'function' ? atob(b64) : Buffer.from(b64, 'base64').toString('binary');
        return Uint8Array.from(bin, c => c.charCodeAt(0));
    }

    // 발급 기록 + 학교 정보 → 짧은 키로 줄인 객체
    function pack(p, school) {
        const d = {
            v: 1,
            k: p.kind === 'out' ? 'o' : 'e',
            id: p.serial,
            nm: p.student_name,
            d: p.date,
        };
        if (p.student_number != null) d.no = p.student_number;
        if (p.time_out) d.t = p.time_out;
        if (p.kind === 'out' && p.time_return) d.r = p.time_return;
        if (p.share_reason && p.reason) d.why = p.reason;
        if (school) {
            if (school.school_name) d.s = school.school_name;
            const c = [school.grade && `${school.grade}학년`, school.class_no && `${school.class_no}반`].filter(Boolean).join(' ');
            if (c) d.c = c;
            if (school.teacher_name) d.tn = school.teacher_name;
        }
        return d;
    }

    function encode(p, school) {
        const json = JSON.stringify(pack(p, school));
        return toBase64Url(new TextEncoder().encode(json));
    }

    function decode(s) {
        try {
            const d = JSON.parse(new TextDecoder().decode(fromBase64Url(s)));
            if (!d || d.v !== 1 || !d.nm || !/^\d{4}-\d{2}-\d{2}$/.test(d.d || '')) return null;
            return {
                kind: d.k === 'o' ? 'out' : 'early',
                kindName: KINDS[d.k === 'o' ? 'out' : 'early'],
                serial: String(d.id || ''),
                name: String(d.nm),
                number: d.no != null ? Number(d.no) : null,
                date: d.d,
                timeOut: d.t || '',
                timeReturn: d.r || '',
                reason: d.why || '',
                school: d.s || '',
                className: d.c || '',
                teacher: d.tn || '',
            };
        } catch (e) {
            return null;
        }
    }

    function shareUrl(base, p, school) {
        return base.replace(/#.*$/, '') + '#' + encode(p, school);
    }

    // 오늘 날짜 'YYYY-MM-DD' (기기 시각 기준)
    function localToday(now) {
        const t = now || new Date();
        const z = n => String(n).padStart(2, '0');
        return `${t.getFullYear()}-${z(t.getMonth() + 1)}-${z(t.getDate())}`;
    }

    const api = { KINDS, encode, decode, shareUrl, localToday };
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.PassCodec = api;
})(typeof self !== 'undefined' ? self : this);
