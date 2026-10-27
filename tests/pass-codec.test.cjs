const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../pass/pass-codec.js');

const school = { school_name: '한빛고등학교', grade: '2', class_no: '3', teacher_name: '김선생' };
const pass = {
    kind: 'out', serial: '20261026-01', student_name: '나하늘', student_number: 2,
    date: '2026-10-26', time_out: '12:00', time_return: '13:00', reason: '병원 진료', share_reason: 0,
};

test('공유 링크를 만들고 다시 읽는다 (한글 포함)', () => {
    const url = C.shareUrl('https://aks-sci-lab.web.app/pass/', pass, school);
    assert.match(url, /^https:\/\/aks-sci-lab\.web\.app\/pass\/#[A-Za-z0-9_-]+$/);
    const p = C.decode(url.split('#')[1]);
    assert.equal(p.kind, 'out');
    assert.equal(p.kindName, '외출');
    assert.equal(p.name, '나하늘');
    assert.equal(p.number, 2);
    assert.equal(p.timeReturn, '13:00');
    assert.equal(p.className, '2학년 3반');
    assert.equal(p.school, '한빛고등학교');
    assert.equal(p.teacher, '김선생');
});

test('사유는 선택했을 때만 링크에 들어간다', () => {
    assert.equal(C.decode(C.encode(pass, school)).reason, '');
    assert.equal(C.decode(C.encode({ ...pass, share_reason: 1 }, school)).reason, '병원 진료');
});

test('조퇴증에는 복귀 시각을 넣지 않는다', () => {
    const p = C.decode(C.encode({ ...pass, kind: 'early' }, school));
    assert.equal(p.kindName, '조퇴');
    assert.equal(p.timeReturn, '');
});

test('잘못된 링크는 null', () => {
    assert.equal(C.decode(''), null);
    assert.equal(C.decode('!!!'), null);
    assert.equal(C.decode(Buffer.from('{"v":2}').toString('base64url')), null);
    assert.equal(C.decode(Buffer.from('{"v":1,"nm":"a","d":"어제"}').toString('base64url')), null);
});

test('주소의 기존 # 은 바꾼다', () => {
    assert.equal(C.shareUrl('https://x.test/pass/#old', pass, null).split('#').length, 2);
});

test('localToday 는 기기 시각 기준', () => {
    assert.equal(C.localToday(new Date(2026, 0, 5, 23, 59)), '2026-01-05');
});
