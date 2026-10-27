// 학생 폰에서 조퇴증·외출증 보여 주기 (주소 # 뒤의 내용을 읽음, 서버에 아무것도 보내지 않음)
(function () {
    'use strict';
    const card = document.getElementById('card');
    const WD = '일월화수목금토';

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    function row(label, value, cls) {
        const r = el('div', 'row');
        r.append(el('dt', null, label), el('dd', cls || null, value));
        return r;
    }

    function dateLabel(iso) {
        const [y, m, d] = iso.split('-').map(Number);
        const dt = new Date(y, m - 1, d);
        return `${y}년 ${m}월 ${d}일 (${WD[dt.getDay()]})`;
    }

    const p = PassCodec.decode(location.hash.slice(1));
    card.textContent = '';
    if (!p) {
        card.append(el('div', 'empty', '올바른 조퇴증·외출증 링크가 아닙니다. 담임 선생님께 다시 받으세요.'));
        return;
    }
    document.title = `${p.kindName}증 - ${p.name}`;
    card.style.setProperty('--kind', p.kind === 'out' ? 'var(--out)' : 'var(--early)');

    const head = el('div', 'head');
    head.append(el('div', 'school', [p.school, p.className].filter(Boolean).join(' ')),
        el('h1', null, `${p.kindName}증`));

    const valid = p.date === PassCodec.localToday();
    const status = el('div', 'status ' + (valid ? 'ok' : 'bad'));
    const clock = el('span', 'clock');
    status.append(el('span', 'dot'), el('span', null, valid ? '오늘 발급 · 유효' : '날짜가 지난 증입니다'), clock);
    const tick = () => { clock.textContent = new Date().toLocaleTimeString('ko-KR', { hour12: false }); };
    tick();
    setInterval(tick, 1000);

    const dl = el('dl');
    dl.append(row('이름', (p.number != null ? `${p.number}번 ` : '') + p.name, 'name'));
    dl.append(row('날짜', dateLabel(p.date)));
    if (p.timeOut) dl.append(row(p.kind === 'out' ? '나가는 시각' : '조퇴 시각', p.timeOut));
    if (p.timeReturn) dl.append(row('복귀 예정', p.timeReturn));
    if (p.reason) dl.append(row('사유', p.reason));
    if (p.teacher) dl.append(row('담임', p.teacher));
    if (p.serial) dl.append(row('발급 번호', p.serial));

    const foot = el('div', 'foot',
        '교문에서 이 화면을 보여 주세요. 시계와 줄무늬가 움직이지 않으면 캡처한 화면입니다. ' +
        '이 링크의 내용은 휴대폰에만 있고 서버에 저장되지 않습니다.');
    card.append(head, status, dl, foot);
})();
