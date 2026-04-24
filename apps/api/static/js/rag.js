/**
 * RAG 지식 관리 콘솔 전용 JS
 */

let collections = [];
let domains = [];

/**
 * 드롭다운 목록 로드
 */
async function loadDropdowns() {
    try {
        const [colRes, domRes] = await Promise.all([
            fetch('/api/collections'),
            fetch('/api/domains')
        ]);

        if (colRes.ok) collections = await colRes.json();
        if (domRes.ok) domains = await domRes.json();

        const searchCol = document.getElementById('search-collection');
        const formCol = document.getElementById('form-collection');
        const searchDom = document.getElementById('search-domain');
        const formDom = document.getElementById('form-domain');

        // 초기화 (기존 옵션 제거)
        searchCol.innerHTML = '<option value="all">전체</option>';
        formCol.innerHTML = '<option value="" disabled selected>선택하세요</option>';
        searchDom.innerHTML = '<option value="all">전체</option>';
        formDom.innerHTML = '<option value="" disabled selected>선택하세요</option>';

        collections.forEach(c => {
            searchCol.innerHTML += `<option value="${c.collection_name}">${c.name} (${c.collection_name})</option>`;
            formCol.innerHTML += `<option value="${c.collection_name}">${c.name}</option>`;
        });

        domains.forEach(d => {
            searchDom.innerHTML += `<option value="${d.id}">${d.name}</option>`;
            formDom.innerHTML += `<option value="${d.id}">${d.name}</option>`;
        });

    } catch (error) {
        console.error('Error loading dropdowns:', error);
    }
}

/**
 * 검색 수행
 */
async function performSearch() {
    const colId = document.getElementById('search-collection').value;
    const domId = document.getElementById('search-domain').value;
    const algo = document.getElementById('search-algorithm').value;
    const query = document.getElementById('search-query').value;

    let url = `/api/rag/search?search_method=${algo}`;
    if (colId !== 'all') url += `&collection_id=${colId}`;
    if (domId !== 'all') url += `&domain_id=${domId}`;
    if (query.trim()) url += `&query=${encodeURIComponent(query)}`;

    const tbody = document.getElementById('rag-data-body');
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center">검색 중...</td></tr>';

    try {
        const res = await fetch(url);
        const data = await res.json();

        tbody.innerHTML = '';
        if (data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#94a3b8;">결과가 없습니다.</td></tr>';
            return;
        }

        data.forEach((item, index) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="text-align: center; font-weight: bold; color: #94a3b8;">${index + 1}</td>
                <td><span class="source-badge">${item.collection}</span></td>
                <td>${getDomainName(item.domain_id)}</td>
                <td><div class="text-truncate" title="${item.content}">${item.content}</div></td>
                <td><div class="text-truncate" title="${item.extended_content}">${item.extended_content}</div></td>
                <td><span class="source-badge">${item.source || 'N/A'}</span></td>
                <td style="font-family: monospace; font-size: 0.85rem; color: #38bdf8;">${item.score !== null && item.score !== undefined ? item.score.toFixed(3) : '-'}</td>
                <td>
                    <button class="btn-secondary" style="padding: 5px 10px; font-size: 0.75rem; border-radius: 4px; background: #3b82f6;" onclick="openEditSidebar(${JSON.stringify(item).replace(/"/g, '&quot;')}, 'show')">View</button>
                    <button class="btn-secondary" style="padding: 5px 10px; font-size: 0.75rem; border-radius: 4px;" onclick="openEditSidebar(${JSON.stringify(item).replace(/"/g, '&quot;')}, 'edit')">Edit</button>
                    <button class="btn-danger" style="padding: 5px 10px; font-size: 0.75rem; border-radius: 4px;" onclick="deletePoint('${item.collection}', '${item.id}')">Delete</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (error) {
        console.error('Search error:', error);
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:red">검색 중 오류가 발생했습니다.</td></tr>';
    }
}

function getDomainName(id) {
    if (!domains || domains.length === 0) return `Unknown (${id})`;
    const dom = domains.find(d => Number(d.id) === Number(id));
    return dom ? dom.name : `Unknown (${id})`;
}

/**
 * 사이드바 열기/닫기
 */
function openSidebar(mode) {
    document.getElementById('form-mode').value = mode;
    
    let title = 'Create Vector Point';
    if (mode === 'edit') title = 'Edit Vector Point';
    if (mode === 'show') title = 'View Vector Point';
    document.getElementById('sidebar-title').innerText = title;
    
    // 라벨 텍스트 동적 처리 (불필요한 '선택 *' 제거)
    const isShow = (mode === 'show');
    const isEdit = (mode === 'edit');
    
    document.querySelector('label[for="form-collection"]').innerText = (isEdit || isShow) ? 'Collection' : 'Collection 선택 *';
    document.querySelector('label[for="form-domain"]').innerText = isShow ? 'Domain' : 'Domain 선택 *';
    document.querySelector('label[for="form-source"]').innerText = isShow ? '출처 (파일명 또는 참조명)' : '출처 (파일명 또는 참조명) *';
    document.querySelector('label[for="form-content"]').innerText = isShow ? '임베딩용 문구 (Content)' : '임베딩용 문구 (Content) *';
    document.querySelector('label[for="form-extended"]').innerText = isShow ? '실제 노출 내용 (Extended Content)' : '실제 노출 내용 (Extended Content) *';
    
    if (mode === 'create') {
        document.getElementById('knowledge-form').reset();
        document.getElementById('form-collection').style.display = 'block';
        document.getElementById('form-collection-label').style.display = 'none';
        document.getElementById('form-collection').disabled = false;
        document.getElementById('form-domain').disabled = false;
        document.getElementById('form-source').readOnly = false;
        document.getElementById('form-content').readOnly = false;
        document.getElementById('form-extended').readOnly = false;
        
        // 버튼 텍스트 및 상태 초기화
        const saveBtn = document.getElementById('btn-save-knowledge');
        const cancelBtn = document.getElementById('btn-cancel-knowledge');
        if (saveBtn) saveBtn.style.display = 'block';
        if (cancelBtn) {
            cancelBtn.innerText = '취소';
            cancelBtn.style.flex = 'initial';
        }
    }

    document.getElementById('action-sidebar').classList.add('active');
    document.getElementById('sidebar-overlay').classList.add('active');
}

function closeSidebar() {
    document.getElementById('action-sidebar').classList.remove('active');
    document.getElementById('sidebar-overlay').classList.remove('active');
}

/**
 * 수정 모드로 사이드바 열기
 */
function openEditSidebar(item, mode = 'edit') {
    openSidebar(mode);
    document.getElementById('edit-point-id').value = item.id;
    document.getElementById('form-collection').value = item.collection; // API 전송용
    
    // 수정/조회 시 Select 숨기고 Label 표시
    document.getElementById('form-collection').style.display = 'none';
    document.getElementById('form-collection-label').style.display = 'block';
    document.getElementById('form-collection-label').innerText = item.collection;
    
    document.getElementById('form-domain').value = item.domain_id;
    document.getElementById('form-source').value = item.source || '';
    document.getElementById('form-content').value = item.content;
    document.getElementById('form-extended').value = item.extended_content;

    const isReadonly = (mode === 'show');
    document.getElementById('form-domain').disabled = isReadonly;
    document.getElementById('form-source').readOnly = isReadonly;
    document.getElementById('form-content').readOnly = isReadonly;
    document.getElementById('form-extended').readOnly = isReadonly;
    
    // 버튼 상태 제어 (조회 시 저장 숨김 & 취소->닫기 변경)
    const saveBtn = document.getElementById('btn-save-knowledge');
    const cancelBtn = document.getElementById('btn-cancel-knowledge');
    
    if (saveBtn) {
        saveBtn.style.display = isReadonly ? 'none' : 'block';
    }
    
    if (cancelBtn) {
        cancelBtn.innerText = isReadonly ? '닫기' : '취소';
        cancelBtn.style.flex = isReadonly ? '1' : 'initial';
    }
}

/**
 * 지식 저장 (등록/수정)
 */
async function saveKnowledge(event) {
    event.preventDefault();
    const mode = document.getElementById('form-mode').value;
    
    const payload = {
        collection_name: document.getElementById('form-collection').value,
        domain_id: parseInt(document.getElementById('form-domain').value),
        source: document.getElementById('form-source').value,
        content: document.getElementById('form-content').value,
        extended_content: document.getElementById('form-extended').value,
        point_id: mode === 'edit' ? document.getElementById('edit-point-id').value : null
    };

    try {
        const res = await fetch('/api/rag/knowledge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            alert(mode === 'create' ? '등록되었습니다.' : '수정되었습니다.');
            closeSidebar();
            performSearch();
        } else {
            const err = await res.json();
            alert('저장 실패: ' + err.detail);
        }
    } catch (error) {
        alert('서버 통신 오류가 발생했습니다.');
    }
}

/**
 * 지식 삭제
 */
async function deletePoint(colName, pointId) {
    if (!confirm('정말 삭제하시겠습니까?')) return;

    try {
        const res = await fetch(`/api/rag/knowledge/${colName}/${pointId}`, {
            method: 'DELETE'
        });

        if (res.ok) {
            performSearch();
        } else {
            alert('삭제 실패');
        }
    } catch (error) {
        alert('오류 발생');
    }
}

// Enter 키로 검색 지원
document.getElementById('search-query')?.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        performSearch();
    }
});
