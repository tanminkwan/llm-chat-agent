/**
 * admin.js - 관리자 CRUD 로직 (콜렉션 / 도메인)
 */

/**
 * Admin 뷰 내부의 탭 전환.
 *
 * @param {string} tab          - 'collection' | 'domain'
 * @param {object} [opts]       - { push?: boolean }  URL 동기화 여부
 */
function switchTab(tab, opts = {}) {
    const { push = true } = opts;

    const colMgr = document.getElementById('collection-manager');
    const domMgr = document.getElementById('domain-manager');
    const tabCol = document.getElementById('tab-collection');
    const tabDom = document.getElementById('tab-domain');

    if (!colMgr || !domMgr) return;

    colMgr.style.display = (tab === 'collection') ? 'block' : 'none';
    domMgr.style.display = (tab === 'domain') ? 'block' : 'none';
    tabCol.classList.toggle('active', tab === 'collection');
    tabDom.classList.toggle('active', tab === 'domain');

    // 사이드바 admin-panel 내부 nav-link 활성화 동기화
    document.querySelectorAll('a.nav-link[data-view="admin"]').forEach(link => {
        link.classList.toggle('active', link.dataset.tab === tab);
    });

    if (tab === 'collection') {
        loadCollections();
    } else {
        loadDomains();
    }

    if (push) {
        const url = `/admin?tab=${tab}`;
        window.history.pushState({}, '', url);
    }
}

// --- Collection CRUD ---
async function saveCollection() {
    const colNameInput = document.getElementById('col-collection-name');
    const collection_name = colNameInput.value;
    const name = document.getElementById('col-name').value;
    const description = document.getElementById('col-desc').value;
    const snippet_size_limit = document.getElementById('col-snippet').value;
    const search_method = document.getElementById('col-search').value;

    if (!collection_name) {
        alert('콜렉션 ID를 입력해주세요.');
        return;
    }

    if (!/^[a-zA-Z0-9_-]+$/.test(collection_name)) {
        alert('콜렉션 ID는 영문, 숫자, 하이픈(-), 언더바(_)만 가능합니다.');
        return;
    }

    const isEdit = colNameInput.disabled;
    const url = isEdit ? `/api/collections/${collection_name}` : '/api/collections';
    const method = isEdit ? 'PUT' : 'POST';

    const res = await fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            collection_name,
            name,
            description,
            snippet_size_limit,
            search_method
        })
    });

    if (res.ok) {
        alert('콜렉션이 저장되었습니다.');
        resetColForm();
        loadCollections();
    } else {
        const err = await res.json();
        alert('저장 실패: ' + (err.detail || '알 수 없는 오류'));
    }
}

async function loadCollections() {
    const res = await fetch('/api/collections');
    if (!res.ok) return;
    const data = await res.json();
    const list = document.getElementById('collection-list');
    list.innerHTML = data.map(c => `
        <tr style="cursor:pointer" onclick="editCollection(${JSON.stringify(c).replace(/"/g, '&quot;')})">
            <td><code style="background:#334155; padding:2px 5px; border-radius:3px;">${c.collection_name}</code></td>
            <td>${c.name}</td>
            <td style="text-align:center">
                <button onclick="event.stopPropagation(); deleteCollection('${c.collection_name}')" style="padding: 5px 10px; background: #ef4444; font-size: 0.7rem; border-radius:4px;">삭제</button>
            </td>
        </tr>
    `).join('');
}

function editCollection(c) {
    const colNameInput = document.getElementById('col-collection-name');
    colNameInput.value = c.collection_name;
    colNameInput.disabled = true; // PK는 수정 불가

    document.getElementById('col-name').value = c.name;
    document.getElementById('col-desc').value = c.description || '';
    document.getElementById('col-snippet').value = c.snippet_size_limit;
    document.getElementById('col-search').value = c.search_method;

    document.getElementById('col-form-title').innerText = '콜렉션 수정';
    document.getElementById('col-save-btn').innerText = '수정 완료';
}

function resetColForm() {
    const colNameInput = document.getElementById('col-collection-name');
    colNameInput.value = '';
    colNameInput.disabled = false;

    document.getElementById('col-name').value = '';
    document.getElementById('col-desc').value = '';
    document.getElementById('col-snippet').value = 500;
    document.getElementById('col-search').value = 'vector';
    document.getElementById('col-form-title').innerText = '콜렉션 등록';
    document.getElementById('col-save-btn').innerText = '저장하기';
}

async function deleteCollection(colName) {
    const deleteVector = confirm(`'${colName}' 콜렉션을 삭제하시겠습니까?\n\n이 작업은 Qdrant의 데이터도 함께 삭제할 수 있습니다.`);
    if (!deleteVector) return;

    const res = await fetch(`/api/collections/${colName}?delete_vector=true`, { method: 'DELETE' });

    if (res.ok) {
        alert('삭제되었습니다.');
        loadCollections();
        resetColForm();
    } else {
        alert('삭제 실패');
    }
}

// --- Domain CRUD ---
async function saveDomain() {
    const id = document.getElementById('dom-id').value;
    const name = document.getElementById('dom-name').value;

    const url = id ? `/api/domains/${id}` : '/api/domains';
    const method = id ? 'PUT' : 'POST';

    const res = await fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });

    if (res.ok) {
        alert('도메인이 저장되었습니다.');
        resetDomForm();
        loadDomains();
    } else {
        alert('저장 실패');
    }
}

async function loadDomains() {
    const res = await fetch('/api/domains');
    if (!res.ok) return;
    const data = await res.json();
    const list = document.getElementById('domain-list');
    list.innerHTML = data.map(d => `
        <tr style="cursor:pointer" onclick="editDomain(${d.id}, '${d.name}')">
            <td>${d.id}</td>
            <td>${d.name}</td>
            <td style="text-align:center">
                <button onclick="event.stopPropagation(); deleteDomain(${d.id})" style="padding: 5px 10px; background: #ef4444; font-size: 0.7rem; border-radius:4px;">삭제</button>
            </td>
        </tr>
    `).join('');
}

function editDomain(id, name) {
    document.getElementById('dom-id').value = id;
    document.getElementById('dom-name').value = name;
    document.getElementById('dom-form-title').innerText = '도메인 수정';
    document.getElementById('dom-save-btn').innerText = '수정 완료';
}

function resetDomForm() {
    document.getElementById('dom-id').value = '';
    document.getElementById('dom-name').value = '';
    document.getElementById('dom-form-title').innerText = '도메인 등록';
    document.getElementById('dom-save-btn').innerText = '저장하기';
}

async function deleteDomain(id) {
    if (!confirm('정말 삭제하시겠습니까?')) return;
    const res = await fetch(`/api/domains/${id}`, { method: 'DELETE' });
    if (res.ok) {
        alert('삭제되었습니다.');
        loadDomains();
        resetDomForm();
    } else {
        alert('삭제 실패');
    }
}

/**
 * Admin 뷰 초기화 - SPA 라우터가 첫 진입 시 호출
 * (실제 데이터 로드는 switchTab() 내부에서 수행)
 */
function initAdmin() {
    // 현재는 별도 로직 없음 (switchTab에서 처리)
}
