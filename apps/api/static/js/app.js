/**
 * app.js - 앱 초기화 및 설정 로직 (SRP: 초기화/인증만 담당)
 */
window.onload = () => {
    checkLogin();
    loadConfig();
};

async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        if (response.ok) {
            const config = await response.json();

            const appTitle = config.app_name + " 서비스";
            document.title = appTitle;
            document.getElementById('app-logo').innerText = appTitle;

            const modelSelect = document.getElementById('model-select');
            modelSelect.innerHTML = `
                <option value="chat" style="background:#1e293b;">${config.chat_label} (${config.chat_model})</option>
                <option value="reasoning" style="background:#1e293b;">${config.reasoning_label} (${config.reasoning_model})</option>
            `;
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
                document.getElementById('admin-panel').style.display = 'block';
            }
        }
    } catch (err) {
        console.log('Not logged in');
    }
}
