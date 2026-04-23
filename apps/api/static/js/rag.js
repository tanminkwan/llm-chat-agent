// RAG Management Console Logic (Restored & Enhanced with Validation)

let currentMode = 'collection'; // 'collection' or 'domain'
let collections = [];
let domains = [];

document.addEventListener('DOMContentLoaded', async () => {
    await loadUserInfo();
    await loadData();
});

async function loadUserInfo() {
    try {
        const resp = await fetch('/user/me');
        if (resp.ok) {
            const user = await resp.json();
            document.getElementById('username').textContent = user.preferred_username;
            const badge = document.getElementById('role-badge');
            badge.textContent = user.is_admin ? 'Admin' : 'User';
            badge.className = user.is_admin ? 'admin-badge' : 'user-badge';
        }
    } catch (err) {
        console.error('Failed to load user info:', err);
    }
}

async function loadData() {
    try {
        const [colResp, domResp] = await Promise.all([
            fetch('/api/collections'),
            fetch('/api/domains')
        ]);

        if (colResp.ok) collections = await colResp.json();
        if (domResp.ok) domains = await domResp.json();

        renderTable();
        renderFilters();
    } catch (err) {
        alert('데이터를 불러오는데 실패했습니다.');
    }
}

function renderTable() {
    const tbody = document.getElementById('collection-list');
    tbody.innerHTML = '';

    collections.forEach(col => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><code class="badge-type">${col.collection_name}</code></td>
            <td>${col.name}</td>
            <td>${col.search_method.toUpperCase()}</td>
            <td>${col.description || '-'}</td>
            <td class="action-btns">
                <button class="icon-btn" onclick="editCollection('${col.collection_name}')">✏️</button>
                <button class="icon-btn" style="color: #ef4444;" onclick="deleteCollection('${col.collection_name}')">🗑️</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function renderFilters() {
    const colSelect = document.getElementById('filter-collection');
    const domSelect = document.getElementById('filter-domain');

    colSelect.innerHTML = '<option value="">전체 유형</option>';
    collections.forEach(col => {
        colSelect.innerHTML += `<option value="${col.collection_name}">${col.name} (${col.collection_name})</option>`;
    });

    domSelect.innerHTML = '<option value="">전체 분야</option>';
    domains.forEach(dom => {
        domSelect.innerHTML += `<option value="${dom.id}">${dom.name}</option>`;
    });
}

function showForm(mode, data = null) {
    currentMode = mode;
    const modal = document.getElementById('form-modal');
    const title = document.getElementById('modal-title');
    const colFields = document.getElementById('collection-fields');
    const domFields = document.getElementById('domain-fields');
    const colNameInput = document.getElementById('field-collection-name');

    modal.style.display = 'flex';
    
    if (mode === 'collection') {
        title.textContent = data ? '콜렉션 정보 수정' : '새 콜렉션 등록';
        colFields.style.display = 'block';
        domFields.style.display = 'none';
        
        if (data) {
            colNameInput.value = data.collection_name;
            colNameInput.disabled = true; 
            document.getElementById('field-name').value = data.name;
            document.getElementById('field-description').value = data.description || '';
            document.getElementById('field-search-method').value = data.search_method;
        } else {
            colNameInput.value = '';
            colNameInput.disabled = false;
            document.getElementById('rag-form').reset();
        }
    } else {
        title.textContent = '새 도메인(분야) 등록';
        colFields.style.display = 'none';
        domFields.style.display = 'block';
        document.getElementById('rag-form').reset();
    }
}

function closeModal() {
    document.getElementById('form-modal').style.display = 'none';
}

async function saveData(event) {
    event.preventDefault();
    const isEdit = document.getElementById('field-collection-name').disabled;
    
    let url = '/api/collections';
    let method = 'POST';
    let body = {};

    if (currentMode === 'collection') {
        const colName = document.getElementById('field-collection-name').value;
        
        // --- 유효성 검사 추가 ---
        if (!/^[a-zA-Z0-9_-]+$/.test(colName)) {
            alert("콜렉션 명(Table Name)은 영문, 숫자, 하이픈(-), 언더바(_)만 사용 가능하며 한글은 금지됩니다.");
            return;
        }

        if (isEdit) {
            url = `/api/collections/${colName}`;
            method = 'PUT';
        }
        body = {
            collection_name: colName,
            name: document.getElementById('field-name').value,
            description: document.getElementById('field-description').value,
            search_method: document.getElementById('field-search-method').value,
            snippet_size_limit: 500
        };
    } else {
        url = '/api/domains';
        body = { name: document.getElementById('field-domain-name').value };
    }

    try {
        const resp = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        if (resp.ok) {
            closeModal();
            await loadData();
        } else {
            const err = await resp.json();
            alert(`저장 실패: ${err.detail || '알 수 없는 에러'}`);
        }
    } catch (err) {
        alert('서버 통신 중 에러가 발생했습니다.');
    }
}

async function editCollection(colName) {
    const col = collections.find(c => c.collection_name === colName);
    if (col) showForm('collection', col);
}

async function deleteCollection(colName) {
    if (!confirm(`'${colName}' 콜렉션을 정말 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.`)) return;
    
    const deleteVector = confirm('Qdrant의 실제 벡터 데이터도 함께 삭제하시겠습니까?');
    
    try {
        const resp = await fetch(`/api/collections/${colName}?delete_vector=${deleteVector}`, {
            method: 'DELETE'
        });

        if (resp.ok) {
            await loadData();
        } else {
            alert('삭제에 실패했습니다.');
        }
    } catch (err) {
        alert('에러가 발생했습니다.');
    }
}

function performSearch() {
    const manualColId = document.getElementById('search-collection-id').value;
    const selectedColId = document.getElementById('filter-collection').value;
    const dom = document.getElementById('filter-domain').value;
    const query = document.getElementById('search-query').value;

    const colId = manualColId || selectedColId;

    if (!colId) {
        alert('조회할 콜렉션 ID를 입력하거나 유형을 선택하세요.');
        return;
    }

    alert(`통합 검색 실행 예 예정:\n[콜렉션 ID: ${colId}, 분야 ID: ${dom}, 쿼리: ${query}]`);
}
