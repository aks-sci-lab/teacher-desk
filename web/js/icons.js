// 선 아이콘 (이모지 대신 한 가지 모양·색으로). 24×24, 선 굵기 1.8
const PATHS = {
    today: 'M4 5h16v14H4z M4 10h16 M9 10v9',
    inbox: 'M4 13l2.5-7h11L20 13v6H4z M4 13h5l1 2h4l1-2h5',
    calendar: 'M4 6h16v14H4z M4 10h16 M8 3v5 M16 3v5',
    week: 'M4 5h16v15H4z M4 9h16 M8 12v5 M12 12v5 M16 12v5',
    timetable: 'M4 4h16v16H4z M4 9h16 M4 14h16 M9 4v16 M14.5 4v16',
    passes: 'M4 7h16v4a2 2 0 0 0 0 4v4H4v-4a2 2 0 0 0 0-4z M14 7v12',
    attendance: 'M4 5h16v14H4z M8 9h3 M8 13l2 2 5-5',
    search: 'M10.5 4a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13z M15.5 15.5L20 20',
    ai: 'M4 4h16v13H9l-5 4z M8 8h8 M8 12h5',
    settings: 'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z M12 3v2.5 M12 18.5V21 M3 12h2.5 M18.5 12H21 M5.6 5.6l1.8 1.8 M16.6 16.6l1.8 1.8 M5.6 18.4l1.8-1.8 M16.6 7.4l1.8-1.8',
    plus: 'M12 5v14 M5 12h14',
    refresh: 'M20 11a8 8 0 0 0-14.5-4.5L4 8 M4 4v4h4 M4 13a8 8 0 0 0 14.5 4.5L20 16 M20 20v-4h-4',
    doc: 'M6 3h8l4 4v14H6z M14 3v4h4 M9 12h6 M9 16h6',
    open: 'M14 4h6v6 M20 4l-9 9 M18 14v6H4V6h6',
    check: 'M5 12.5l4.5 4.5L19 7',
    edit: 'M4 20h4L19 9l-4-4L4 16z M13.5 6.5l4 4',
    trash: 'M5 7h14 M10 7V4h4v3 M7 7l1 13h8l1-13',
    archive: 'M4 5h16v4H4z M5 9v10h14V9 M10 13h4',
    spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z',
    clock: 'M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z M12 8v4l3 2',
    printer: 'M7 9V4h10v5 M5 9h14v7h-3 M8 16H5V9 M8 13h8v7H8z',
    copy: 'M9 9h11v11H9z M5 15H4V4h11v1',
    x: 'M6 6l12 12 M18 6L6 18',
};

export function icon(name, cls = '') {
    const d = PATHS[name];
    if (!d) return null;
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', `ic${cls ? ' ' + cls : ''}`);
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d);
    svg.append(path);
    return svg;
}
