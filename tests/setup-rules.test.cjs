// 설정 도우미 규칙 (0.5.79): 새 사용자만 자동으로 열고, 기존 사용자는 한 번 안내
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'js', 'setup-rules.js'), 'utf8');
const modulePromise = import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('켤 때 도우미: 새 사용자 열기 · 기존 사용자 한 번 안내 · 마친 뒤 조용히', async () => {
    const { setupAction } = await modulePromise;
    assert.equal(setupAction({}), 'open');
    assert.equal(setupAction({ school_name: '  ', setup_wizard: '' }), 'open');
    assert.equal(setupAction({ school_name: '가나고' }), 'notify');
    assert.equal(setupAction({ coolmessenger_name: '홍길동' }), 'notify');
    assert.equal(setupAction({ telegram_token_set: true }), 'notify');
    assert.equal(setupAction({ school_name: '가나고', setup_wizard: 'notified' }), 'none');
    assert.equal(setupAction({ setup_wizard: 'done' }), 'none');
    assert.equal(setupAction({ setup_wizard: 'later' }), 'remind');
});

test('칸 값 바로 검사', async () => {
    const { checkValue } = await modulePromise;
    assert.equal(checkValue('collect_times', ['09:00', '12:00', '9']), '');
    assert.match(checkValue('collect_times', ['25:00']), /25:00/);
    assert.match(checkValue('collect_times', ['아홉시']), /잘못/);
    assert.equal(checkValue('grade', '2'), '');
    assert.equal(checkValue('grade', ''), '');
    assert.match(checkValue('class_no', '3반'), /숫자/);
    assert.equal(checkValue('pass_page_url', 'https://aks-sci-lab.github.io/teacher-desk/'), '');
    assert.match(checkValue('pass_page_url', 'http://x'), /https/);
    assert.equal(checkValue('school_name', '아무 글'), '');
});

test('설정 검색은 낱말이 모두 있어야 맞음', async () => {
    const { matches } = await modulePromise;
    assert.ok(matches('텔레그램 봇 토큰', '토큰'));
    assert.ok(matches('LM Studio 연결 확인', 'lm 연결'));
    assert.ok(!matches('에듀파인 공문 폴더', '텔레그램'));
    assert.ok(matches('무엇이든', ''));
});
