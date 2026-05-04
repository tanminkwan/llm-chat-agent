/**
 * app.js - SPA 라우터 및 초기화
 *
 *  - 인증/설정 로드
 *  - History API 기반 라우팅 (/, /rag, /bulk, /admin)
 *  - 뷰 전환 시 DOM은 유지하고 display 만 토글하므로 작업 상태가 보존됨
 */

const VIEW_IDS = ['chat', 'rag', 'prompts', 'bulk', 'admin'];
const viewInitialized = { chat: false, rag: false, prompts: false, bulk: false, admin: false };

window.addEventListener('DOMContentLoaded', () => {
    checkLogin();
    loadConfig();
    setupNavigation();

    // 초기 뷰 결정 (URL 기반)
    const { view, tab } = parseLocation();
    showView(view, { tab, push: false });
});

window.addEventListener('popstate', () => {
    const { view, tab } = parseLocation();
    showView(view, { tab, push: false });
});

function parseLocation() {
    const path = window.location.pathname;
    let view = 'chat';
    if (path.startsWith('/rag')) view = 'rag';
    else if (path.startsWith('/prompts')) view = 'prompts';
    else if (path.startsWith('/bulk')) view = 'bulk';
    else if (path.startsWith('/admin')) view = 'admin';

    const params = new URLSearchParams(window.location.search);
    const tab = params.get('tab');
    return { view, tab };
}

function setupNavigation() {
    document.querySelectorAll('a.nav-link[data-view]').forEach(link => {
        link.addEventListener('click', (e) => {
            // 새 탭 열기 등 기본 브라우저 동작은 유지
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || link.target === '_blank') return;
            e.preventDefault();
            const view = link.dataset.view;
            const tab = link.dataset.tab || null;
            const url = link.getAttribute('href');
            showView(view, { tab, push: true, url });
        });
    });
}

/**
 * 지정한 뷰를 활성화하고, 필요 시 lazy 초기화 수행.
 *
 * @param {string} view  - 'chat' | 'rag' | 'bulk' | 'admin'
 * @param {object} opts  - { tab?: string, push?: boolean, url?: string }
 */
function showView(view, opts = {}) {
    if (!VIEW_IDS.includes(view)) view = 'chat';
    const { tab, push = false, url } = opts;

    VIEW_IDS.forEach(id => {
        const el = document.getElementById(`view-${id}`);
        if (el) el.classList.toggle('active', id === view);
    });

    document.body.dataset.view = view;

    // 사이드바 nav-link active 토글
    document.querySelectorAll('a.nav-link[data-view]').forEach(link => {
        const isActive = (link.dataset.view === view) &&
            (view !== 'admin' || !link.dataset.tab || link.dataset.tab === (tab || 'collection'));
        link.classList.toggle('active', isActive);
    });

    // URL 갱신 (브라우저 히스토리에 push)
    if (push) {
        const newUrl = url || pathFor(view, tab);
        if (newUrl !== window.location.pathname + window.location.search) {
            window.history.pushState({}, '', newUrl);
        }
    }

    // 뷰별 lazy 초기화
    initView(view, tab);
}

function pathFor(view, tab) {
    if (view === 'chat') return '/';
    if (view === 'admin') return `/admin?tab=${tab || 'collection'}`;
    return `/${view}`;
}

function initView(view, tab) {
    if (view === 'chat') {
        if (!viewInitialized.chat) {
            if (typeof initChat === 'function') initChat();
            viewInitialized.chat = true;
        }
    } else if (view === 'rag') {
        if (!viewInitialized.rag) {
            if (typeof initRag === 'function') initRag();
            viewInitialized.rag = true;
        }
    } else if (view === 'prompts') {
        if (!viewInitialized.prompts) {
            if (typeof initPrompts === 'function') initPrompts();
            viewInitialized.prompts = true;
        } else {
            if (typeof searchPrompts === 'function') searchPrompts();
        }
    } else if (view === 'bulk') {
        if (!viewInitialized.bulk) {
            if (typeof initBulk === 'function') initBulk();
            viewInitialized.bulk = true;
        }
    } else if (view === 'admin') {
        if (!viewInitialized.admin) {
            if (typeof initAdmin === 'function') initAdmin();
            viewInitialized.admin = true;
        }
        if (typeof switchTab === 'function') {
            switchTab(tab || 'collection', { push: false });
        }
    }
}

async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        if (response.ok) {
            const config = await response.json();

            const appTitle = config.app_name + " 서비스";
            document.title = appTitle;
            document.getElementById('app-logo').innerText = appTitle;

            const modelSelect = document.getElementById('model-select');
            if (modelSelect) {
                modelSelect.innerHTML = `
                    <option value="chat" style="background:#1e293b;">${config.chat_label} (${config.chat_model})</option>
                    <option value="reasoning" style="background:#1e293b;">${config.reasoning_label} (${config.reasoning_model})</option>
                `;
            }
        }
    } catch (err) {
        console.error('설정을 불러오는데 실패했습니다:', err);
    }
}

async function checkLogin() {
    try {
        const response = await fetch('/user/me');
        if (response.ok) {
            const user = await response.json();
            document.getElementById('login-section').style.display = 'none';
            document.getElementById('user-info').style.display = 'block';
            document.getElementById('username').innerText = user.preferred_username;

            const is_admin = user.groups && user.groups.includes('Admin');
            document.getElementById('role-badge').innerText = is_admin ? 'Admin' : 'User';
            document.getElementById('role-badge').style.background = is_admin ? '#ef4444' : '#10b981';

            if (is_admin) {
                document.getElementById('admin-panel').style.display = 'flex';
            }
        }
    } catch (err) {
        console.log('Not logged in');
    }
}
