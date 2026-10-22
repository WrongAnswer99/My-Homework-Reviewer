'use strict';
const md = markdownit({html: true, breaks: true, linkify: false})
    .use(texmath, {engine: katex, delimiters: ['dollars', 'brackets', 'beg_end'],
        katexOptions: {throwOnError: false, trust: false, strict: 'warn', maxExpand: 1000}});
// Blanks in original questions are literal underscores; answers use normal Markdown.
const questionMd = markdownit({html: true, breaks: true, linkify: false})
    .use(texmath, {engine: katex, delimiters: ['dollars', 'brackets', 'beg_end'],
        katexOptions: {throwOnError: false, trust: false, maxExpand: 1000}});
questionMd.inline.ruler.at('emphasis', function(state, silent) {
    if (state.src.charCodeAt(state.pos) === 0x5F) return false;
    return md.inline.ruler.__rules__.find(r => r.name === 'emphasis').fn(state, silent);
});
function normalizeDisplayMath(text) {
    const lines = text.split(/\r?\n/);
    let fence = null;
    let open = null;
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (open !== null) {
            if (/^[ \t]{0,3}\\\][ \t]*$/.test(line)) {
                lines[open] = '$$';
                lines[i] = '$$';
                open = null;
            }
            continue;
        }
        const marker = line.match(/^[ \t]{0,3}(`{3,}|~{3,})(.*)$/);
        if (fence) {
            if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length
                    && !marker[2].trim()) fence = null;
            continue;
        }
        if (marker) {
            fence = marker[1];
        } else if (/^[ \t]{0,3}\\\[[ \t]*$/.test(line)) {
            open = i;
        }
    }
    return lines.join('\n');
}
function renderSections(sections, scrollBottom=false) {
    const root = document.getElementById('content');
    root.replaceChildren();
    for (const section of sections) {
        const article = document.createElement('article');
        article.className = 'section' + (section.role === 'user' ? ' user' : '');
        if (section.label) {
            const label = document.createElement('div');
            label.className = 'role';
            label.textContent = section.label;
            article.appendChild(label);
        }
        const body = document.createElement('div');
        const parser = section.question ? questionMd : md;
        const raw = section.question ? (section.text || '').replace(/^(\s*)(_{3,})(\s*)$/gm,
            (_, lead, blanks, tail) => lead + blanks.split('').map(() => '\\_').join('') + tail) : (section.text || '');
        const text = normalizeDisplayMath(raw);
        body.innerHTML = DOMPurify.sanitize(parser.render(text), {
            USE_PROFILES: {html: true, mathMl: true, svg: true},
            FORBID_TAGS: ['style', 'input', 'button', 'form'],
            FORBID_ATTR: ['srcset'],
        });
        // Preview is a reader: no navigation, remote images or media requests.
        for (const image of body.querySelectorAll('img')) {
            if (!/^data:image\/(png|jpeg|gif|webp|bmp);base64,/.test(image.getAttribute('src') || '')) {
                image.replaceWith(document.createTextNode('[图片不可用]'));
            } else {
                image.onload = () => {
                    if (image.naturalHeight > 85 || image.naturalWidth > 420) image.classList.add('diagram');
                };
            }
        }
        for (const link of body.querySelectorAll('a')) link.removeAttribute('href');
        article.appendChild(body);
        root.appendChild(article);
    }
    window.previewReady = true;
    if (scrollBottom) window.scrollTo(0, document.body.scrollHeight);
    else window.scrollTo(0, 0);
    return true;
}
