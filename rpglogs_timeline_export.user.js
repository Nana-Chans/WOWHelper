// ==UserScript==
// @name         rpglogs Timeline 复制器
// @namespace    wowhelper
// @version      1.0
// @description  一键复制 rpglogs 时间轴 HTML 到剪贴板，供 parse_timeline.py / timeline_gui.py 解析
// @author       WOWHelper
// @match        *://*.rpglogs.cn/reports/*
// @match        *://*.warcraftlogs.com/reports/*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    // ====== 配置 ======
    const BTN_COPY_TEXT = '📋 复制 Timeline';
    const BTN_SWITCH_TEXT = '🔄 切换时间轴视图';
    const CHECK_INTERVAL = 1000; // 检测按钮是否已插入的间隔(ms)
    const INIT_DELAY = 1200;     // 首次插入延迟(等 SPA 渲染)

    // ====== 获取时间轴 HTML ======
    function getTimelineHTML() {
        // 优先 .timeline-lines（含标尺 + 所有玩家行 + timeline-box）
        // mCustomScrollBox 可能产生克隆，取含 timeline-box 最多的那个
        const candidates = document.querySelectorAll('.timeline-lines');
        let best = null, bestCount = 0;
        candidates.forEach(el => {
            const n = el.querySelectorAll('.timeline-box').length;
            if (n > bestCount) { bestCount = n; best = el; }
        });
        if (best && bestCount > 0) return best.outerHTML;

        // 回退：mCSB_container
        const container = document.querySelector('.mCSB_container');
        if (container && container.querySelector('.timeline-box')) {
            return container.outerHTML;
        }
        return null;
    }

    // ====== 复制到剪贴板 ======
    async function copyToClipboard(text) {
        // 现代剪贴板 API（需 HTTPS + 用户手势）
        if (navigator.clipboard && navigator.clipboard.writeText) {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch (e) {
                // 失败则走回退
            }
        }
        // 回退：textarea + execCommand
        try {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            const ok = document.execCommand('copy');
            ta.remove();
            return ok;
        } catch (e) {
            return false;
        }
    }

    // ====== Toast 提示 ======
    function showToast(msg, color) {
        const t = document.createElement('div');
        t.textContent = msg;
        t.setAttribute('style', [
            'position:fixed', 'left:50%', 'top:24px', 'transform:translateX(-50%)',
            'z-index:999999', 'padding:10px 20px', 'border-radius:6px',
            'font-size:14px', 'color:#fff',
            'background:' + (color || '#19be6b'),
            'box-shadow:0 2px 10px rgba(0,0,0,0.35)',
        ].join(';'));
        document.body.appendChild(t);
        setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .4s'; }, 1600);
        setTimeout(() => t.remove(), 2100);
    }

    // ====== 校验：当前是否为简体中文站点 ======
    function isZhCNSite() {
        const h = location.hostname;
        return h.indexOf('cn.') === 0 || h.indexOf('.cn', h.length - 3) >= 0;
    }

    // ====== 轮询条件，超时返回 false ======
    function waitFor(cond, timeoutMs) {
        return new Promise(resolve => {
            const start = Date.now();
            (function check() {
                if (cond()) return resolve(true);
                if (Date.now() - start >= timeoutMs) return resolve(false);
                setTimeout(check, 200);
            })();
        });
    }

    // ====== 切换到时间轴视图 ======
    async function switchToTimeline() {
        // 1. 语言：非简中站点则跳转到 cn 站点同报告页
        if (!isZhCNSite()) {
            const cnLink = document.querySelector(
                '#header-language-picker-element a.header-language-picker__dropdown-item[hreflang="cn"]'
            );
            if (cnLink && cnLink.getAttribute('href')) {
                showToast('正在切换到简体中文站点…', '#2d8cf0');
                location.href = cnLink.href; // 整页跳转，后续点 tab 由跳转后的脚本实例完成
                return;
            }
            showToast('未找到简体中文链接，请在右上角手动切换', '#ed4014');
            return;
        }

        // 2. 已在 施法+时间轴 → 完成
        const castsTabNow = document.getElementById('filter-casts-tab');
        if (castsTabNow && castsTabNow.classList.contains('selected')) {
            showToast('已在时间轴视图');
            return;
        }

        // 3. 点击 时间轴 tab（若尚未选中）
        const tlTab = document.getElementById('filter-timeline-tab');
        if (tlTab && !tlTab.classList.contains('selected')) {
            tlTab.click();
            showToast('已点击「时间轴」，等待加载…', '#2d8cf0');
        }

        // 4. 轮询等待 casts tab 出现且其 href 含 view=timeline
        const waited = await waitFor(() => {
            const c = document.getElementById('filter-casts-tab');
            return c && /view=timeline/.test(c.getAttribute('href') || '');
        }, 8000);

        // 5. 点击 施法 tab
        const casts = document.getElementById('filter-casts-tab');
        if (!casts) {
            showToast('找不到「施法」标签，请稍后再试', '#ed4014');
            return;
        }
        casts.click();
        showToast(
            waited ? '已点击「施法」，时间轴即将显示' : '已点击「施法」（未确认时间轴视图）',
            waited ? '#19be6b' : '#ff9900'
        );
    }

    // ====== 点击处理 ======
    async function onCopyClick(btn) {
        if (!isZhCNSite()) {
            showToast('请先在右上角将 Language 切换为简体中文（cn. 站点）后再复制', '#ed4014');
            return;
        }
        btn.disabled = true;
        const old = btn.textContent;
        btn.textContent = '⏳ 复制中...';
        try {
            const html = getTimelineHTML();
            if (!html) {
                showToast('未找到时间轴！请点上方「切换时间轴视图」', '#ed4014');
                return;
            }
            const ok = await copyToClipboard(html);
            if (ok) {
                const boxCount = (html.match(/timeline-box/g) || []).length;
                showToast(`已复制 ${boxCount} 个施法事件到剪贴板`);
            } else {
                showToast('复制失败，请检查浏览器剪贴板权限', '#ed4014');
            }
        } catch (e) {
            showToast('出错：' + e.message, '#ed4014');
        } finally {
            btn.disabled = false;
            btn.textContent = old;
        }
    }

    // ====== 插入按钮（容器内纵向排列两个按钮）======
    function ensureButton() {
        if (document.getElementById('__tl_container')) return;
        const container = document.createElement('div');
        container.id = '__tl_container';
        container.setAttribute('style', [
            'position:fixed', 'right:18px', 'bottom:18px', 'z-index:999999',
            'display:flex', 'flex-direction:column', 'gap:8px',
        ].join(';'));
        document.body.appendChild(container);

        // 切换视图按钮
        const switchBtn = document.createElement('button');
        switchBtn.textContent = BTN_SWITCH_TEXT;
        switchBtn.title = '切换到 施法→时间轴 视图（修改 URL hash）';
        switchBtn.setAttribute('style', [
            'padding:9px 16px', 'font-size:14px', 'cursor:pointer',
            'background:#19be6b', 'color:#fff', 'border:none', 'border-radius:8px',
            'box-shadow:0 2px 10px rgba(0,0,0,0.35)', 'transition:background .15s',
        ].join(';'));
        switchBtn.addEventListener('mouseenter', () => switchBtn.style.background = '#47cb89');
        switchBtn.addEventListener('mouseleave', () => switchBtn.style.background = '#19be6b');
        switchBtn.addEventListener('click', switchToTimeline);
        container.appendChild(switchBtn);

        // 复制按钮
        const copyBtn = document.createElement('button');
        copyBtn.id = '__tl_copy_btn';
        copyBtn.textContent = BTN_COPY_TEXT;
        copyBtn.title = '复制当前时间轴 HTML 到剪贴板，供 timeline_gui.py 解析';
        copyBtn.setAttribute('style', [
            'padding:9px 16px', 'font-size:14px', 'cursor:pointer',
            'background:#2d8cf0', 'color:#fff', 'border:none', 'border-radius:8px',
            'box-shadow:0 2px 10px rgba(0,0,0,0.35)', 'transition:background .15s',
        ].join(';'));
        copyBtn.addEventListener('mouseenter', () => copyBtn.style.background = '#5cadff');
        copyBtn.addEventListener('mouseleave', () => copyBtn.style.background = '#2d8cf0');
        copyBtn.addEventListener('click', () => onCopyClick(copyBtn));
        container.appendChild(copyBtn);
    }

    // ====== 启动：SPA 下持续确保按钮存在 ======
    setTimeout(function loop() {
        ensureButton();
        setTimeout(loop, CHECK_INTERVAL);
    }, INIT_DELAY);
})();
