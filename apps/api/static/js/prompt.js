/**
 * prompt.js - 개인 시스템 프롬프트 관리 (CRUD)
 */

let _currentUserSub = null;

async function _loadCurrentUser() {
    if (_currentUserSub) return _currentUserSub;
    try {
        const res = await fetch('/user/me');
        if (res.ok) {
            const me = await res.json();
            _currentUserSub = me.sub;
        }
    } catch (e) {
        console.error('Failed to load current user', e);
    }
    return _currentUserSub;
}

function _formatDate(iso) {
    if (!iso) return '-';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch (e) {
        return iso;
    }
}

function _escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

async function searchPrompts() {
    const titleEl = document.getElementById('prompt-search-title');
    const includeEl = document.getElementById('prompt-search-include-others');
    const tbody = document.getElementById('prompt-data-body');
    if (!tbody) return;

    const title = titleEl ? titleEl.value.trim() : '';
    const includeOthers = includeEl ? includeEl.checked : false;

    const params = new URLSearchParams();
    if (title) params.set('title', title);
    if (includeOthers) params.set('include_others', 'true');

    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center">조회 중...</td></tr>';

    try {
        const res = await fetch(`/api/prompts?${params.toString()}`);
        if (!res.ok) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:red">조회 실패</td></tr>';
            return;
        }
        const data = await res.json();
        if (!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:#94a3b8;">결과가 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = '';
        data.forEach((item, index) => {
            const tr = document.createElement('tr');
            const ownerLabel = item.is_owner
                ? `<span class="source-badge" style="background:#0369a1;">나</span>`
                : `<span class="source-badge">${_escapeHtml(item.username || item.user_id)}</span>`;
            const publicBadge = item.is_public
                ? '<span class="source-badge" style="background:#10b981;">공개</span>'
                : '<span class="source-badge" style="background:#475569;">비공개</span>';

            const applyBtn = `<button class="btn-secondary" style="padding: 5px 10px; font-size: 0.75rem; border-radius: 4px; background: #10b981; color:white;" title="Chat 의 시스템 프롬프트로 적용" onclick="applyPromptToChat(${item.id})">💬 적용</button>`;
            const actions = item.is_owner
                ? `
                    ${applyBtn}
                    <button class="btn-secondary" style="padding: 5px 10px; font-size: 0.75rem; border-radius: 4px; background: #3b82f6;" onclick="openPromptSidebar('show', ${item.id})">View</button>
                    <button class="btn-secondary" style="padding: 5px 10px; font-size: 0.75rem; border-radius: 4px;" onclick="openPromptSidebar('edit', ${item.id})">Edit</button>
                    <button class="btn-danger" style="padding: 5px 10px; font-size: 0.75rem; border-radius: 4px;" onclick="deletePrompt(${item.id})">Delete</button>
                `
                : `
                    ${applyBtn}
                    <button class="btn-secondary" style="padding: 5px 10px; font-size: 0.75rem; border-radius: 4px; background: #3b82f6;" onclick="openPromptSidebar('show', ${item.id})">View</button>
                `;

            tr.innerHTML = `
                <td style="text-align: center; font-weight: bold; color: #94a3b8;">${index + 1}</td>
                <td><div class="text-truncate" title="${_escapeHtml(item.title)}">${_escapeHtml(item.title)}</div></td>
                <td>${ownerLabel}</td>
                <td style="text-align:center;">${publicBadge}</td>
                <td style="font-family: monospace; font-size: 0.8rem; color:#94a3b8;">${_formatDate(item.created_at)}</td>
                <td style="font-family: monospace; font-size: 0.8rem; color:#94a3b8;">${_formatDate(item.updated_at)}</td>
                <td>${actions}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('searchPrompts error', e);
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:red">오류가 발생했습니다.</td></tr>';
    }
}

async function openPromptSidebar(mode, promptId) {
    const titleEl = document.getElementById('prompt-sidebar-title');
    const formMode = document.getElementById('prompt-form-mode');
    const editIdEl = document.getElementById('prompt-edit-id');
    const titleInput = document.getElementById('prompt-form-title');
    const contentInput = document.getElementById('prompt-form-content');
    const publicInput = document.getElementById('prompt-form-public');
    const ownerGroup = document.getElementById('prompt-form-owner-group');
    const ownerEl = document.getElementById('prompt-form-owner');
    const saveBtn = document.getElementById('btn-save-prompt');
    const cancelBtn = document.getElementById('btn-cancel-prompt');

    formMode.value = mode;
    editIdEl.value = promptId || '';

    let titleText = '신규 Prompt 등록';
    if (mode === 'edit') titleText = 'Prompt 수정';
    if (mode === 'show') titleText = 'Prompt 조회';
    titleEl.innerText = titleText;

    if (mode === 'create') {
        titleInput.value = '';
        contentInput.value = '';
        publicInput.checked = true;
        titleInput.readOnly = false;
        contentInput.readOnly = false;
        publicInput.disabled = false;
        ownerGroup.style.display = 'none';
        saveBtn.style.display = 'block';
        cancelBtn.innerText = '취소';
        cancelBtn.style.flex = 'initial';
    } else {
        try {
            const res = await fetch(`/api/prompts/${promptId}`);
            if (!res.ok) {
                alert('Prompt 를 불러올 수 없습니다.');
                return;
            }
            const item = await res.json();

            titleInput.value = item.title || '';
            contentInput.value = item.content || '';
            publicInput.checked = !!item.is_public;
            ownerGroup.style.display = 'block';
            ownerEl.innerText = item.is_owner ? `${item.username || item.user_id} (나)` : (item.username || item.user_id);

            const isShow = (mode === 'show');
            titleInput.readOnly = isShow;
            contentInput.readOnly = isShow;
            publicInput.disabled = isShow;

            saveBtn.style.display = isShow ? 'none' : 'block';
            cancelBtn.innerText = isShow ? '닫기' : '취소';
            cancelBtn.style.flex = isShow ? '1' : 'initial';
        } catch (e) {
            console.error('openPromptSidebar error', e);
            alert('서버 통신 오류가 발생했습니다.');
            return;
        }
    }

    document.getElementById('prompt-sidebar').classList.add('active');
    document.getElementById('prompt-sidebar-overlay').classList.add('active');
}

function closePromptSidebar() {
    document.getElementById('prompt-sidebar').classList.remove('active');
    document.getElementById('prompt-sidebar-overlay').classList.remove('active');
}

async function savePrompt(event) {
    event.preventDefault();
    const mode = document.getElementById('prompt-form-mode').value;
    const promptId = document.getElementById('prompt-edit-id').value;

    const payload = {
        title: document.getElementById('prompt-form-title').value.trim(),
        content: document.getElementById('prompt-form-content').value,
        is_public: document.getElementById('prompt-form-public').checked,
    };

    if (!payload.title) {
        alert('제목을 입력하세요.');
        return;
    }
    if (!payload.content || !payload.content.trim()) {
        alert('Prompt 내용을 입력하세요.');
        return;
    }

    try {
        let res;
        if (mode === 'edit' && promptId) {
            res = await fetch(`/api/prompts/${promptId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        } else {
            res = await fetch('/api/prompts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        }

        if (res.ok) {
            alert(mode === 'edit' ? '수정되었습니다.' : '등록되었습니다.');
            closePromptSidebar();
            searchPrompts();
        } else {
            let detail = '저장 실패';
            try {
                const err = await res.json();
                if (err.detail) detail = err.detail;
            } catch (_) { /* ignore */ }
            alert(detail);
        }
    } catch (e) {
        console.error('savePrompt error', e);
        alert('서버 통신 오류가 발생했습니다.');
    }
}

/**
 * 선택한 Prompt 를 Chat 의 시스템 프롬프트 입력창에 복사하고 Chat 화면으로 이동.
 * - 본인/타인(공개) 모두 적용 가능
 * - 시스템 프롬프트 입력창은 자동으로 펼쳐서 적용된 본문이 보이도록 함
 */
async function applyPromptToChat(promptId) {
    try {
        const res = await fetch(`/api/prompts/${promptId}`);
        if (!res.ok) {
            let detail = 'Prompt 를 불러올 수 없습니다.';
            try {
                const err = await res.json();
                if (err.detail) detail = err.detail;
            } catch (_) { /* ignore */ }
            alert(detail);
            return;
        }
        const item = await res.json();

        const sysInput = document.getElementById('system-prompt-input');
        if (!sysInput) {
            alert('Chat 화면이 준비되지 않았습니다.');
            return;
        }
        sysInput.value = item.content || '';
        sysInput.style.display = 'block';

        // SPA 라우터를 통해 Chat 화면으로 이동
        if (typeof showView === 'function') {
            showView('chat', { push: true });
        } else {
            window.location.href = '/';
        }
    } catch (e) {
        console.error('applyPromptToChat error', e);
        alert('서버 통신 오류가 발생했습니다.');
    }
}

async function deletePrompt(promptId) {
    if (!confirm('정말 삭제하시겠습니까?')) return;
    try {
        const res = await fetch(`/api/prompts/${promptId}`, { method: 'DELETE' });
        if (res.ok) {
            searchPrompts();
        } else {
            let detail = '삭제 실패';
            try {
                const err = await res.json();
                if (err.detail) detail = err.detail;
            } catch (_) { /* ignore */ }
            alert(detail);
        }
    } catch (e) {
        console.error('deletePrompt error', e);
        alert('서버 통신 오류가 발생했습니다.');
    }
}

function initPrompts() {
    _loadCurrentUser();

    const titleInput = document.getElementById('prompt-search-title');
    if (titleInput) {
        titleInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                searchPrompts();
            }
        });
    }

    searchPrompts();
}
