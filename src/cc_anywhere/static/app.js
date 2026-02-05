/**
 * CC-Anywhere Dashboard Application
 */

class CCAnywhere {
    constructor() {
        this.sessions = [];
        this.currentSession = null;
        this.terminal = null;
        this.fitAddon = null;
        this.ws = null;
        this.monitorWs = null;
        this.resizeTimeout = null;

        // WebSocket reconnection settings (exponential backoff)
        this.reconnectAttempts = 0;
        this.baseReconnectDelay = 1000;  // 1초 시작
        this.maxReconnectDelay = 30000;  // 최대 30초
        this.reconnectTimer = null;
        this.isOnline = navigator.onLine;

        // Monitor WebSocket reconnection
        this.monitorReconnectAttempts = 0;
        this.monitorReconnectTimer = null;

        // Heartbeat settings
        this.heartbeatInterval = null;
        this.heartbeatTimeout = null;
        this.heartbeatIntervalMs = 30000;  // 30초마다 ping
        this.heartbeatTimeoutMs = 15000;   // 15초 내 pong 없으면 재연결

        // Mobile features
        this.isMobile = window.innerWidth <= 768;
        this.fontSize = this.isMobile ? 8 : 12;
        this.isFullscreen = false;
        this.isChatMode = false;
        this.chatMessages = [];

        // Slash commands storage
        this.SLASH_COMMANDS_KEY = 'cc-anywhere-slash-commands';
        this.MAX_SLASH_COMMANDS = 10;

        this.init();
    }

    async init() {
        // Initialize terminal
        this.initTerminal();

        // Set up event listeners
        this.setupEventListeners();

        // Initialize slash command dropdown
        this.updateSlashDropdown();

        // Load sessions
        await this.loadSessions();

        // Start monitoring
        this.startMonitoring();

        // Handle window resize (debounced)
        window.addEventListener('resize', () => this.handleResizeDebounced());

        // Handle orientation change (mobile)
        window.addEventListener('orientationchange', () => {
            // Wait for orientation change to complete
            setTimeout(() => this.handleResizeDebounced(), 100);
        });

        // Handle visibility change (mobile tab switch)
        document.addEventListener('visibilitychange', () => this.handleVisibilityChange());

        // Handle network status changes
        window.addEventListener('online', () => this.handleOnline());
        window.addEventListener('offline', () => this.handleOffline());

        // iOS keyboard viewport fix
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', () => this.handleVisualViewportResize());
        }
    }

    handleVisualViewportResize() {
        // Adjust layout when iOS keyboard appears/disappears
        const viewport = window.visualViewport;
        if (viewport) {
            const keyboardHeight = window.innerHeight - viewport.height;
            const app = document.querySelector('.app');

            if (keyboardHeight > 100) {
                // Keyboard is visible - adjust app height
                app.style.height = `${viewport.height}px`;
            } else {
                // Keyboard hidden - reset
                app.style.height = '';
            }
        }
    }

    handleVisibilityChange() {
        if (document.visibilityState === 'visible' && this.currentSession) {
            // Reconnect WebSocket when returning to tab
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
                console.log('Reconnecting after visibility change...');
                this.reconnectToSession();
            } else {
                // Force sync: resize terminal and scroll to bottom
                // Use setTimeout to wait for layout to settle after returning to tab
                setTimeout(() => {
                    this.fitAddon.fit();
                    this.sendResize();
                    this.terminal.scrollToBottom();
                }, 100);
            }
        }
    }

    handleOnline() {
        console.log('Network online');
        this.isOnline = true;

        if (this.currentSession) {
            // 네트워크 복구 시 즉시 재연결 시도
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
                this.terminal.writeln('\r\n\x1b[1;32m[Network restored - reconnecting...]\x1b[0m');
                this.reconnectToSession();  // reconnectToSession이 내부에서 카운터 리셋
            }
        }

        // Monitor WebSocket도 재연결
        if (!this.monitorWs || this.monitorWs.readyState !== WebSocket.OPEN) {
            this.monitorReconnectAttempts = 0;
            this.startMonitoring();
        }
    }

    handleOffline() {
        console.log('Network offline');
        this.isOnline = false;

        if (this.currentSession) {
            this.terminal.writeln('\r\n\x1b[1;31m[Network offline - waiting for connection...]\x1b[0m');
        }

        // 재연결 타이머 취소 (오프라인 상태에서는 재시도 무의미)
        this.cancelReconnectTimers();
        this.updateConnectionStatus('disconnected');
    }

    cancelReconnectTimers() {
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.monitorReconnectTimer) {
            clearTimeout(this.monitorReconnectTimer);
            this.monitorReconnectTimer = null;
        }
    }

    getReconnectDelay(attempts) {
        // 지수 백오프: 1s, 2s, 4s, 8s, 16s, 30s (최대)
        return Math.min(
            this.baseReconnectDelay * Math.pow(2, attempts),
            this.maxReconnectDelay
        );
    }

    startHeartbeat() {
        this.stopHeartbeat();

        this.heartbeatInterval = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                // ping 전송
                this.ws.send(JSON.stringify({ type: 'ping' }));

                // pong 타임아웃 설정
                this.heartbeatTimeout = setTimeout(() => {
                    console.warn('Heartbeat timeout - reconnecting...');
                    if (this.ws) {
                        this.ws.close();
                    }
                }, this.heartbeatTimeoutMs);
            }
        }, this.heartbeatIntervalMs);
    }

    stopHeartbeat() {
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }
        if (this.heartbeatTimeout) {
            clearTimeout(this.heartbeatTimeout);
            this.heartbeatTimeout = null;
        }
    }

    handlePong() {
        // pong 수신 시 타임아웃 취소
        if (this.heartbeatTimeout) {
            clearTimeout(this.heartbeatTimeout);
            this.heartbeatTimeout = null;
        }
    }

    reconnectToSession() {
        if (!this.currentSession) return;

        // 오프라인 상태면 재연결 시도 안함
        if (!this.isOnline) {
            console.log('Offline - skipping reconnect');
            return;
        }

        // Reset reconnect attempts for manual reconnect
        this.reconnectAttempts = 0;
        this.cancelReconnectTimers();

        // Close existing connection
        this.stopHeartbeat();
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }

        this.terminal.writeln('\r\n\x1b[1;33m[Reconnecting...]\x1b[0m');
        this.connectWebSocket(this.currentSession.id);
    }

    initTerminal() {
        // Update font size label
        this.updateFontSizeLabel();

        this.terminal = new Terminal({
            cursorBlink: true,
            fontSize: this.fontSize,
            fontFamily: 'Menlo, Monaco, "Courier New", monospace',
            theme: {
                background: '#1e1e1e',
                foreground: '#d4d4d4',
                cursor: '#d4d4d4',
                selectionBackground: '#264f78',
            },
            scrollback: 10000,
            convertEol: true,
            // Mobile scroll improvements
            scrollSensitivity: this.isMobile ? 5 : 1,
            fastScrollSensitivity: this.isMobile ? 15 : 5,
            smoothScrollDuration: this.isMobile ? 50 : 0,
            // Better touch handling
            allowProposedApi: true,
        });

        this.fitAddon = new FitAddon.FitAddon();
        const webLinksAddon = new WebLinksAddon.WebLinksAddon();

        this.terminal.loadAddon(this.fitAddon);
        this.terminal.loadAddon(webLinksAddon);

        const container = document.getElementById('terminal');
        this.terminal.open(container);
        this.fitAddon.fit();

        // Handle terminal input
        this.terminal.onData((data) => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'key',
                    data: data,
                }));
            }
        });

        // Show initial message
        this.terminal.writeln('\x1b[1;34m╔══════════════════════════════════════╗\x1b[0m');
        this.terminal.writeln('\x1b[1;34m║       Welcome to CC-Anywhere         ║\x1b[0m');
        this.terminal.writeln('\x1b[1;34m╚══════════════════════════════════════╝\x1b[0m');
        this.terminal.writeln('');
        this.terminal.writeln('Select a session or create a new one to start.');
    }

    setupEventListeners() {
        // New session button
        document.getElementById('btn-new-session').addEventListener('click', () => {
            this.showModal('modal-new-session');
        });

        // Discover button
        document.getElementById('btn-discover').addEventListener('click', () => {
            this.showDiscoverModal();
        });

        // Cancel modal button
        document.getElementById('btn-cancel-modal').addEventListener('click', () => {
            this.hideModal('modal-new-session');
        });

        // Close discover modal
        document.getElementById('btn-close-discover').addEventListener('click', () => {
            this.hideModal('modal-discover');
        });

        // History button
        document.getElementById('btn-history').addEventListener('click', () => {
            this.showHistoryModal();
        });

        // Close history modal
        document.getElementById('btn-close-history').addEventListener('click', () => {
            this.hideModal('modal-history');
        });

        // History back button
        document.getElementById('btn-history-back').addEventListener('click', () => {
            document.getElementById('history-sessions').classList.remove('hidden');
            document.getElementById('history-entries').classList.add('hidden');
        });

        // Import all button
        document.getElementById('btn-import-all').addEventListener('click', async () => {
            await this.importAllSessions();
        });

        // New session form
        document.getElementById('form-new-session').addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.createSession();
        });

        // Cancel command button (removed from UI, but keep handler for keyboard shortcut)
        document.getElementById('btn-cancel')?.addEventListener('click', async () => {
            if (this.currentSession) {
                await this.cancelCommand();
            }
        });

        // Delete session button
        document.getElementById('btn-delete').addEventListener('click', async () => {
            if (this.currentSession && confirm('Delete this session?')) {
                await this.deleteSession(this.currentSession.id);
            }
        });

        // Refresh button (reconnect to session - same as selectSession)
        document.getElementById('btn-refresh').addEventListener('click', () => {
            if (this.currentSession) {
                this.selectSession(this.currentSession);
            }
        });

        // Mobile refresh button
        const mobileRefreshBtn = document.getElementById('btn-mobile-refresh');
        if (mobileRefreshBtn) {
            const refreshHandler = (e) => {
                e.preventDefault();
                if (this.currentSession) {
                    this.selectSession(this.currentSession);
                }
            };
            mobileRefreshBtn.addEventListener('click', refreshHandler);
            mobileRefreshBtn.addEventListener('touchend', refreshHandler, { passive: false });
        }

        // Close modal on outside click
        document.querySelectorAll('.modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    this.hideModal(modal.id);
                }
            });
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            // Escape to close modal
            if (e.key === 'Escape') {
                document.querySelectorAll('.modal:not(.hidden)').forEach(modal => {
                    this.hideModal(modal.id);
                });
            }
        });

        // Mobile input bar for IME support
        const mobileInput = document.getElementById('mobile-input');
        mobileInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing) {
                e.preventDefault();
                const text = mobileInput.value || '';
                if (text && this.ws && this.ws.readyState === WebSocket.OPEN) {
                    // Send as input (with Enter)
                    this.ws.send(JSON.stringify({
                        type: 'input',
                        data: text,
                    }));

                    // Save slash command for dropdown
                    if (text.startsWith('/')) {
                        const cmdPart = text.split(' ')[0];
                        this.saveSlashCommand(cmdPart);
                    }

                    // Chat 모드 활성화 시 입력 메시지를 Chat 뷰에 추가
                    if (this.isChatMode) {
                        this.addChatMessage('input', text);
                        // Chat 뷰 스크롤
                        const chatContainer = document.getElementById('chat-messages');
                        chatContainer.scrollTop = chatContainer.scrollHeight;
                    }
                }
                mobileInput.value = '';
            }
        });

        // Slash command dropdown
        document.getElementById('slash-command-dropdown').addEventListener('change', (e) => {
            const command = e.target.value;
            if (command) {
                const mobileInput = document.getElementById('mobile-input');
                mobileInput.value = command + ' ';
                mobileInput.focus();
            }
            e.target.value = '';  // Reset dropdown
        });

        // Mobile special keys bar
        document.querySelectorAll('.key-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const key = btn.dataset.key;
                if (key) {
                    this.sendSpecialKey(key);
                }
            });
        });

        // Font size controls
        document.getElementById('btn-font-up').addEventListener('click', () => {
            this.changeFontSize(1);
        });
        document.getElementById('btn-font-down').addEventListener('click', () => {
            this.changeFontSize(-1);
        });

        // Fullscreen toggle
        document.getElementById('btn-fullscreen').addEventListener('click', () => {
            this.toggleFullscreen();
        });

        // Fullscreen exit button - 이벤트 위임 방식
        document.getElementById('fullscreen-sessions-bar')?.addEventListener('click', (e) => {
            if (e.target.id === 'btn-fullscreen-exit' || e.target.closest('#btn-fullscreen-exit')) {
                e.preventDefault();
                e.stopPropagation();
                this.toggleFullscreen();
            }
        });

        // Chat mode toggle
        document.getElementById('btn-chat-mode')?.addEventListener('click', () => {
            this.toggleChatMode();
        });

        // Clear all sessions button
        document.getElementById('btn-clear-all').addEventListener('click', async () => {
            if (this.sessions.length === 0) {
                alert('No sessions to clear');
                return;
            }
            if (confirm(`Delete all ${this.sessions.length} sessions?`)) {
                await this.clearAllSessions();
            }
        });

        // Directory browser
        document.getElementById('btn-browse-dir').addEventListener('click', () => {
            this.showDirBrowser();
        });

        // Also open browser when clicking on the readonly input
        document.getElementById('working-dir').addEventListener('click', () => {
            this.showDirBrowser();
        });

        document.getElementById('btn-dir-up').addEventListener('click', () => {
            if (this.currentBrowsePath && this.currentBrowseParent) {
                this.browseTo(this.currentBrowseParent);
            }
        });

        document.getElementById('btn-cancel-dir-browser').addEventListener('click', () => {
            this.hideModal('modal-dir-browser');
        });

        document.getElementById('btn-select-dir').addEventListener('click', () => {
            this.selectBrowsedDir();
        });

        // Favorites
        document.getElementById('btn-add-favorite').addEventListener('click', () => {
            this.addCurrentToFavorites();
        });

        // Refresh random session name
        document.getElementById('btn-refresh-name').addEventListener('click', () => {
            this.generateRandomSessionName();
        });

        // Git worktree checkbox toggle
        document.getElementById('create-worktree').addEventListener('change', (e) => {
            document.getElementById('worktree-branch-row').classList.toggle('hidden', !e.target.checked);
        });

        // Check Git status when working directory changes
        document.getElementById('working-dir').addEventListener('input', () => {
            this.checkGitStatus();
        });
    }

    async clearAllSessions() {
        try {
            const response = await fetch('/api/sessions', {
                method: 'DELETE',
            });

            if (!response.ok) {
                const error = await response.json();
                alert(error.detail || 'Failed to clear sessions');
                return;
            }

            const result = await response.json();

            // Clear current session
            if (this.currentSession) {
                this.currentSession = null;
                document.getElementById('terminal-title').textContent = 'Select a session';
                document.getElementById('connection-status').className = 'connection-status';
                document.getElementById('connection-status').textContent = '';
                document.getElementById('btn-refresh').disabled = true;
                document.getElementById('btn-delete').disabled = true;
                this.terminal.clear();
                this.terminal.writeln(`Cleared ${result.count} sessions.`);
            }

            await this.loadSessions();

        } catch (error) {
            console.error('Failed to clear sessions:', error);
            alert('Failed to clear sessions');
        }
    }

    // Slash commands management
    getSlashCommands() {
        try {
            const commands = localStorage.getItem(this.SLASH_COMMANDS_KEY);
            return commands ? JSON.parse(commands) : [];
        } catch (e) {
            return [];
        }
    }

    saveSlashCommand(command) {
        if (!command || !command.startsWith('/')) return;

        let commands = this.getSlashCommands();
        // Remove duplicate (move recent to front)
        commands = commands.filter(c => c !== command);
        commands.unshift(command);
        // Limit max count
        commands = commands.slice(0, this.MAX_SLASH_COMMANDS);

        localStorage.setItem(this.SLASH_COMMANDS_KEY, JSON.stringify(commands));
        this.updateSlashDropdown();
    }

    updateSlashDropdown() {
        const dropdown = document.getElementById('slash-command-dropdown');
        if (!dropdown) return;

        const commands = this.getSlashCommands();

        // Remove existing options (except first placeholder)
        while (dropdown.options.length > 1) {
            dropdown.remove(1);
        }

        // Add saved commands
        commands.forEach(cmd => {
            const option = document.createElement('option');
            option.value = cmd;
            option.textContent = cmd;
            dropdown.appendChild(option);
        });
    }

    // Favorites management
    getFavorites() {
        try {
            const favs = localStorage.getItem('cc-anywhere-favorites');
            return favs ? JSON.parse(favs) : [];
        } catch (e) {
            return [];
        }
    }

    saveFavorites(favorites) {
        localStorage.setItem('cc-anywhere-favorites', JSON.stringify(favorites));
    }

    addFavorite(dir) {
        if (!dir || dir === '~') return false;

        let favs = this.getFavorites();
        // Check if already exists
        if (favs.includes(dir)) return false;

        favs.push(dir);
        this.saveFavorites(favs);
        return true;
    }

    removeFavorite(dir) {
        let favs = this.getFavorites();
        favs = favs.filter(f => f !== dir);
        this.saveFavorites(favs);
    }

    addCurrentToFavorites() {
        const dir = document.getElementById('working-dir').value.trim();
        if (!dir) {
            alert('Select a directory first');
            return;
        }
        if (this.addFavorite(dir)) {
            this.renderFavorites();
        } else {
            alert('Already in favorites');
        }
    }

    renderFavorites() {
        const container = document.getElementById('favorites-list');
        const favs = this.getFavorites();

        if (favs.length === 0) {
            container.innerHTML = '<div class="dir-list-empty">No favorites yet. Browse and add directories.</div>';
            return;
        }

        container.innerHTML = '';
        favs.forEach(dir => {
            const item = document.createElement('div');
            item.className = 'dir-item';
            item.innerHTML = `
                <span class="dir-item-name" title="${this.escapeHtml(dir)}">📁 ${this.escapeHtml(dir)}</span>
                <div class="dir-item-actions">
                    <button class="dir-item-btn" title="Remove from favorites">✕</button>
                </div>
            `;

            // Click to select
            item.querySelector('.dir-item-name').addEventListener('click', () => {
                document.getElementById('working-dir').value = dir;
            });

            // Remove button
            item.querySelector('.dir-item-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                this.removeFavorite(dir);
                this.renderFavorites();
            });

            container.appendChild(item);
        });
    }

    // Directory browser
    async showDirBrowser() {
        this.showModal('modal-dir-browser');

        // Start from current working dir or home
        const currentDir = document.getElementById('working-dir').value.trim() || '~';
        await this.browseTo(currentDir);
    }

    async browseTo(path) {
        const listContainer = document.getElementById('dir-browser-list');
        const pathDisplay = document.getElementById('current-path');

        listContainer.innerHTML = '<div class="dir-browser-loading">Loading...</div>';

        try {
            const response = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);

            if (!response.ok) {
                const error = await response.json();
                listContainer.innerHTML = `<div class="dir-browser-empty">${error.detail || 'Failed to load'}</div>`;
                return;
            }

            const data = await response.json();

            this.currentBrowsePath = data.path;
            this.currentBrowseParent = data.parent;
            this.selectedBrowseDir = data.display_path; // Default to current directory

            pathDisplay.textContent = data.display_path;

            // Enable/disable up button
            document.getElementById('btn-dir-up').disabled = !data.parent;

            if (data.directories.length === 0) {
                listContainer.innerHTML = '<div class="dir-browser-empty">No subdirectories</div>';
                return;
            }

            listContainer.innerHTML = '';
            data.directories.forEach(dir => {
                const item = document.createElement('div');
                item.className = 'dir-browser-item';
                item.innerHTML = `
                    <span class="folder-icon">📁</span>
                    <span class="folder-name">${this.escapeHtml(dir.name)}</span>
                `;

                // Single click to select
                item.addEventListener('click', () => {
                    // Remove previous selection
                    listContainer.querySelectorAll('.dir-browser-item').forEach(i => i.classList.remove('selected'));
                    item.classList.add('selected');
                    this.selectedBrowseDir = dir.display_path || dir.path;
                });

                // Double click to navigate into
                item.addEventListener('dblclick', () => {
                    this.browseTo(dir.path);
                });

                listContainer.appendChild(item);
            });

        } catch (error) {
            console.error('Failed to browse directory:', error);
            listContainer.innerHTML = '<div class="dir-browser-empty">Failed to load directories</div>';
        }
    }

    selectBrowsedDir() {
        if (this.selectedBrowseDir) {
            document.getElementById('working-dir').value = this.selectedBrowseDir;
            this.hideModal('modal-dir-browser');
            // Check Git status for the selected directory
            this.checkGitStatus();
        }
    }

    async checkGitStatus() {
        const dir = document.getElementById('working-dir').value.trim();
        const gitOptions = document.getElementById('git-options');
        const createWorktree = document.getElementById('create-worktree');
        const worktreeBranchRow = document.getElementById('worktree-branch-row');

        if (!dir) {
            gitOptions.classList.add('hidden');
            return;
        }

        try {
            const response = await fetch(`/api/git/info?path=${encodeURIComponent(dir)}`);
            if (response.ok) {
                const data = await response.json();

                if (data.is_git_repo) {
                    gitOptions.classList.remove('hidden');
                    // Reset checkbox state
                    createWorktree.checked = false;
                    worktreeBranchRow.classList.add('hidden');
                    document.getElementById('worktree-branch').value = '';
                } else {
                    gitOptions.classList.add('hidden');
                }
            } else {
                gitOptions.classList.add('hidden');
            }
        } catch (error) {
            console.log('Failed to check Git status:', error);
            gitOptions.classList.add('hidden');
        }
    }

    sendSpecialKey(key) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

        // Map special key names to ANSI escape sequences
        const keyMap = {
            'Escape': '\x1b',
            'Tab': '\t',
            'S-Tab': '\x1b[Z',  // Shift+Tab (reverse tab / backtab)
            'C-c': '\x03',      // Ctrl+C
            'C-d': '\x04',      // Ctrl+D
            'C-z': '\x1a',      // Ctrl+Z
            'C-l': '\x0c',      // Ctrl+L (clear)
            'Up': '\x1b[A',
            'Down': '\x1b[B',
            'Left': '\x1b[D',
            'Right': '\x1b[C',
            'Enter': '\r',
        };

        const keyCode = keyMap[key];
        if (keyCode) {
            this.ws.send(JSON.stringify({
                type: 'key',
                data: keyCode,
            }));
        }
    }

    // Font size control
    changeFontSize(delta) {
        const minSize = 6;
        const maxSize = 16;
        this.fontSize = Math.min(maxSize, Math.max(minSize, this.fontSize + delta));

        if (this.terminal) {
            this.terminal.options.fontSize = this.fontSize;
            this.fitAddon.fit();
            this.sendResize();
        }

        this.updateFontSizeLabel();
    }

    updateFontSizeLabel() {
        const label = document.getElementById('font-size-label');
        if (label) {
            label.textContent = this.fontSize;
        }
    }

    // Fullscreen mode
    toggleFullscreen() {
        this.isFullscreen = !this.isFullscreen;
        document.body.classList.toggle('fullscreen-mode', this.isFullscreen);

        const btn = document.getElementById('btn-fullscreen');
        btn.textContent = this.isFullscreen ? '✕ Exit' : '⛶ Full';

        // Update fullscreen session bar
        if (this.isFullscreen) {
            this.renderFullscreenSessionChips();
        }

        // Refit terminal after layout change
        setTimeout(() => {
            if (this.fitAddon) {
                this.fitAddon.fit();
                this.sendResize();
            }
        }, 100);
    }

    // Fullscreen session chips
    renderFullscreenSessionChips() {
        const container = document.getElementById('fullscreen-session-chips');
        container.innerHTML = '';

        if (this.sessions.length === 0) {
            container.innerHTML = '<span style="color: var(--text-secondary); font-size: 0.75rem;">No sessions</span>';
            return;
        }

        this.sessions.forEach(session => {
            const chip = document.createElement('div');
            chip.className = `fullscreen-session-chip ${session.status}`;
            if (this.currentSession && this.currentSession.id === session.id) {
                chip.classList.add('selected');
            }

            chip.innerHTML = `
                <span class="chip-status"></span>
                <span class="chip-name">${this.escapeHtml(session.name)}</span>
            `;

            chip.addEventListener('click', () => this.selectSession(session));
            container.appendChild(chip);
        });
    }

    // Chat mode
    toggleChatMode() {
        this.isChatMode = !this.isChatMode;
        document.querySelector('.terminal-panel').classList.toggle('chat-mode-active', this.isChatMode);

        const btn = document.getElementById('btn-chat-mode');
        if (btn) btn.textContent = this.isChatMode ? '⌨ Term' : '💬 Chat';

        if (this.isChatMode) {
            // Load recent history into chat
            this.loadChatFromHistory();
        }
    }

    async loadChatFromHistory() {
        if (!this.currentSession) return;

        const container = document.getElementById('chat-messages');
        container.innerHTML = '<div class="chat-message system">Loading history...</div>';

        try {
            const response = await fetch(`/api/history/${this.currentSession.id}?limit=50`);
            const entries = await response.json();

            container.innerHTML = '';

            if (entries.length === 0) {
                container.innerHTML = '<div class="chat-message system">No history yet</div>';
                return;
            }

            entries.forEach(entry => {
                this.addChatMessage(entry.type, entry.content, entry.ts);
            });

            // Scroll to bottom
            container.scrollTop = container.scrollHeight;

        } catch (error) {
            console.error('Failed to load chat history:', error);
            container.innerHTML = '<div class="chat-message system">Failed to load history</div>';
        }
    }

    // ANSI 코드 및 터미널 제어 문자 제거
    stripAnsiCodes(text) {
        return text
            // ANSI escape sequences (색상, 스타일 등)
            .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')
            // OSC sequences (터미널 타이틀 등)
            .replace(/\x1b\][^\x07]*\x07/g, '')
            // 기타 escape sequences
            .replace(/\x1b[()][AB012]/g, '')
            // 캐리지 리턴 + 줄바꿈 정리
            .replace(/\r\n/g, '\n')
            .replace(/\r/g, '\n')
            // 연속 줄바꿈 정리
            .replace(/\n{3,}/g, '\n\n')
            // 앞뒤 공백 제거
            .trim();
    }

    addChatMessage(type, content, timestamp) {
        const container = document.getElementById('chat-messages');

        // output 타입은 ANSI 코드 제거
        let cleanContent = type === 'output' ? this.stripAnsiCodes(content) : content;

        // 빈 메시지는 무시
        if (!cleanContent || cleanContent.trim() === '') {
            return;
        }

        const msgType = type === 'input' ? 'user' : (type === 'output' ? 'assistant' : 'system');
        const time = timestamp ? new Date(timestamp).toLocaleTimeString() : '';

        const div = document.createElement('div');
        div.className = `chat-message ${msgType}`;

        // Simple formatting for code blocks
        let formattedContent = this.escapeHtml(cleanContent);
        formattedContent = formattedContent.replace(/```([\s\S]*?)```/g, '<pre>$1</pre>');
        formattedContent = formattedContent.replace(/`([^`]+)`/g, '<code>$1</code>');
        // 줄바꿈을 <br>로 변환
        formattedContent = formattedContent.replace(/\n/g, '<br>');

        div.innerHTML = `
            <div>${formattedContent}</div>
            ${time ? `<div class="chat-time">${time}</div>` : ''}
        `;

        container.appendChild(div);
    }

    async loadSessions() {
        try {
            const response = await fetch('/api/sessions');
            this.sessions = await response.json();
            this.renderSessionTiles();
        } catch (error) {
            console.error('Failed to load sessions:', error);
        }
    }

    renderSessionTiles() {
        const container = document.getElementById('session-tiles');
        container.innerHTML = '';

        if (this.sessions.length === 0) {
            container.innerHTML = '<p class="no-sessions">No sessions. Create one to start.</p>';
            // Fullscreen 세션 바도 업데이트
            if (this.isFullscreen) {
                this.renderFullscreenSessionChips();
            }
            return;
        }

        this.sessions.forEach(session => {
            const tile = document.createElement('div');
            tile.className = `session-tile ${session.status}`;
            if (this.currentSession && this.currentSession.id === session.id) {
                tile.classList.add('selected');
            }

            tile.innerHTML = `
                <button class="tile-delete-btn" data-session-id="${session.id}" title="Delete session">×</button>
                <div class="session-name">${this.escapeHtml(session.name)}</div>
                <div class="session-status">${session.status}</div>
                <div class="session-time">${this.formatTime(session.last_activity)}</div>
            `;

            // Delete button handler
            const deleteBtn = tile.querySelector('.tile-delete-btn');
            deleteBtn.addEventListener('click', async (e) => {
                e.stopPropagation(); // Prevent tile selection
                if (confirm(`Delete session "${session.name}"?`)) {
                    await this.deleteSession(session.id);
                }
            });

            // 더블탭/더블클릭으로 세션 선택 (500ms 이내)
            let lastTapTime = 0;

            const handleTap = (e) => {
                if (e.target.closest('.tile-delete-btn')) return;
                e.preventDefault();
                e.stopPropagation();

                const now = Date.now();
                if (now - lastTapTime < 500) {
                    // 더블탭: 세션 선택
                    this.selectSession(session);
                    lastTapTime = 0;
                } else {
                    // 첫 번째 탭: 시각적 피드백
                    lastTapTime = now;
                    tile.style.opacity = '0.7';
                    setTimeout(() => { tile.style.opacity = '1'; }, 200);
                }
            };

            tile.addEventListener('click', handleTap);
            tile.addEventListener('touchend', handleTap, { passive: false });
            container.appendChild(tile);
        });

        // Fullscreen 세션 바도 함께 업데이트
        if (this.isFullscreen) {
            this.renderFullscreenSessionChips();
        }
    }

    async selectSession(session) {
        // Disconnect from previous session - remove handlers first to prevent stale messages
        if (this.ws) {
            this.ws.onmessage = null;
            this.ws.onclose = null;
            this.ws.onerror = null;
            this.ws.close();
            this.ws = null;
        }

        this.currentSession = session;
        this.renderSessionTiles();

        // Update terminal header
        document.getElementById('terminal-title').textContent = session.name;
        document.getElementById('btn-refresh').disabled = false;
        document.getElementById('btn-delete').disabled = false;

        // Clear terminal completely
        this.terminal.clear();
        this.terminal.reset();

        // Use requestAnimationFrame to ensure DOM is ready
        requestAnimationFrame(() => {
            this.fitAddon.fit();
            this.terminal.writeln(`\x1b[1;32mConnecting to session: ${session.name}\x1b[0m`);
            this.terminal.writeln('');

            // Connect WebSocket after terminal is ready
            this.connectWebSocket(session.id);
        });
    }

    connectWebSocket(sessionId) {
        // 오프라인 상태면 연결 시도 안함
        if (!this.isOnline) {
            console.log('Offline - skipping WebSocket connection');
            this.updateConnectionStatus('disconnected');
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/${sessionId}`;

        // Store the session ID this connection is for
        const connectedSessionId = sessionId;

        this.updateConnectionStatus('connecting');
        this.stopHeartbeat();

        try {
            this.ws = new WebSocket(wsUrl);
        } catch (e) {
            console.error('WebSocket creation failed:', e);
            this.scheduleReconnect(sessionId);
            return;
        }

        this.ws.onopen = () => {
            console.log('WebSocket connected to session:', connectedSessionId);
            this.reconnectAttempts = 0;
            this.updateConnectionStatus('connected');
            // Send initial terminal size to sync WezTerm pane
            this.sendResize();
            // Start heartbeat
            this.startHeartbeat();
        };

        this.ws.onmessage = (event) => {
            // Verify this message is for the current session
            if (!this.currentSession || this.currentSession.id !== connectedSessionId) {
                console.log('Ignoring message for old session:', connectedSessionId);
                return;
            }

            try {
                const msg = JSON.parse(event.data);

                // Handle pong response
                if (msg.type === 'pong') {
                    this.handlePong();
                    return;
                }

                if (msg.type === 'output') {
                    this.terminal.write(msg.data);
                    // Auto-scroll to bottom on new output
                    this.terminal.scrollToBottom();

                    // Chat 모드 활성화 시 Chat 뷰에도 추가
                    if (this.isChatMode) {
                        this.addChatMessage('output', msg.data);
                        // Chat 뷰도 스크롤
                        const chatContainer = document.getElementById('chat-messages');
                        chatContainer.scrollTop = chatContainer.scrollHeight;
                    }
                }
            } catch (e) {
                console.error('Failed to parse message:', e);
            }
        };

        this.ws.onclose = (event) => {
            console.log('WebSocket disconnected, code:', event.code);
            this.updateConnectionStatus('disconnected');
            this.stopHeartbeat();

            // 오프라인 상태면 재연결 시도 안함
            if (!this.isOnline) {
                return;
            }

            if (this.currentSession && this.currentSession.id === sessionId) {
                this.scheduleReconnect(sessionId);
            }
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            // onclose가 호출되므로 여기서는 상태만 업데이트
        };
    }

    scheduleReconnect(sessionId) {
        // 이미 타이머가 있으면 무시
        if (this.reconnectTimer) return;

        this.reconnectAttempts++;
        const delay = this.getReconnectDelay(this.reconnectAttempts - 1);
        const delaySec = (delay / 1000).toFixed(1);

        this.terminal.writeln(`\r\n\x1b[1;33m[Reconnecting in ${delaySec}s... (attempt ${this.reconnectAttempts})]\x1b[0m`);

        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            if (this.currentSession && this.currentSession.id === sessionId && this.isOnline) {
                this.connectWebSocket(sessionId);
            }
        }, delay);
    }

    updateConnectionStatus(status) {
        const el = document.getElementById('connection-status');
        el.className = 'connection-status ' + status;

        const labels = {
            'connected': '●',
            'disconnected': '○',
            'connecting': '◌',
        };
        el.textContent = labels[status] || '';
        el.title = status.charAt(0).toUpperCase() + status.slice(1);
    }

    startMonitoring() {
        // 오프라인 상태면 연결 시도 안함
        if (!this.isOnline) {
            console.log('Offline - skipping monitor connection');
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/monitor`;

        // 기존 연결 정리
        if (this.monitorWs) {
            this.monitorWs.onclose = null;
            this.monitorWs.close();
            this.monitorWs = null;
        }

        try {
            this.monitorWs = new WebSocket(wsUrl);
        } catch (e) {
            console.error('Monitor WebSocket creation failed:', e);
            this.scheduleMonitorReconnect();
            return;
        }

        this.monitorWs.onopen = () => {
            console.log('Monitor WebSocket connected');
            this.monitorReconnectAttempts = 0;
        };

        this.monitorWs.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'sessions') {
                    this.sessions = msg.data;
                    this.renderSessionTiles();
                }
            } catch (e) {
                console.error('Failed to parse monitor message:', e);
            }
        };

        this.monitorWs.onclose = () => {
            console.log('Monitor WebSocket disconnected');
            if (this.isOnline) {
                this.scheduleMonitorReconnect();
            }
        };

        this.monitorWs.onerror = (error) => {
            console.error('Monitor WebSocket error:', error);
        };
    }

    scheduleMonitorReconnect() {
        // 이미 타이머가 있으면 무시
        if (this.monitorReconnectTimer) return;

        this.monitorReconnectAttempts++;
        const delay = this.getReconnectDelay(this.monitorReconnectAttempts - 1);

        this.monitorReconnectTimer = setTimeout(() => {
            this.monitorReconnectTimer = null;
            if (this.isOnline) {
                this.startMonitoring();
            }
        }, delay);
    }

    async createSession() {
        const name = document.getElementById('session-name').value.trim();
        const workingDir = document.getElementById('working-dir').value.trim() || null;

        // Git worktree options
        const createWorktree = document.getElementById('create-worktree').checked;
        const worktreeBranch = document.getElementById('worktree-branch').value.trim() || null;
        const cleanupWorktree = document.getElementById('cleanup-worktree').checked;

        if (!name) {
            alert('Please enter a session name');
            return;
        }

        const requestBody = {
            name,
            working_dir: workingDir,
        };

        // Include worktree options if enabled
        if (createWorktree) {
            requestBody.create_worktree = true;
            requestBody.cleanup_worktree = cleanupWorktree;
            if (worktreeBranch) {
                requestBody.worktree_branch = worktreeBranch;
            }
        }

        try {
            const response = await fetch('/api/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody),
            });

            if (!response.ok) {
                const error = await response.json();
                alert(error.detail || 'Failed to create session');
                return;
            }

            const session = await response.json();
            this.hideModal('modal-new-session');
            document.getElementById('form-new-session').reset();
            // Reset Git options
            document.getElementById('git-options').classList.add('hidden');

            // Reload and select new session
            await this.loadSessions();
            this.selectSession(session);

        } catch (error) {
            console.error('Failed to create session:', error);
            alert('Failed to create session');
        }
    }

    async deleteSession(sessionId) {
        try {
            const response = await fetch(`/api/sessions/${sessionId}`, {
                method: 'DELETE',
            });

            if (!response.ok) {
                const error = await response.json();
                alert(error.detail || 'Failed to delete session');
                return;
            }

            // Clear current session if deleted
            if (this.currentSession && this.currentSession.id === sessionId) {
                this.currentSession = null;
                document.getElementById('terminal-title').textContent = 'Select a session';
                document.getElementById('connection-status').className = 'connection-status';
                document.getElementById('connection-status').textContent = '';
                document.getElementById('btn-refresh').disabled = true;
                document.getElementById('btn-delete').disabled = true;
                this.terminal.clear();
                this.terminal.writeln('Session deleted.');
            }

            await this.loadSessions();

        } catch (error) {
            console.error('Failed to delete session:', error);
            alert('Failed to delete session');
        }
    }

    async cancelCommand() {
        if (!this.currentSession) return;

        try {
            await fetch(`/api/sessions/${this.currentSession.id}/cancel`, {
                method: 'POST',
            });
        } catch (error) {
            console.error('Failed to cancel command:', error);
        }
    }

    handleResizeDebounced() {
        // Debounce resize to avoid excessive calls
        if (this.resizeTimeout) {
            clearTimeout(this.resizeTimeout);
        }
        this.resizeTimeout = setTimeout(() => {
            this.handleResize();
        }, 150);
    }

    handleResize() {
        if (this.fitAddon) {
            this.fitAddon.fit();
            this.sendResize();
            console.log(`Terminal resized to ${this.terminal.cols}x${this.terminal.rows}`);
        }
    }

    sendResize() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN && this.terminal) {
            this.ws.send(JSON.stringify({
                type: 'resize',
                cols: this.terminal.cols,
                rows: this.terminal.rows,
            }));
        }
    }

    showModal(modalId) {
        document.getElementById(modalId).classList.remove('hidden');

        // Update favorites list for new session modal
        if (modalId === 'modal-new-session') {
            this.renderFavorites();
            // Generate random session name
            this.generateRandomSessionName();
        }

        // Focus first input
        const input = document.querySelector(`#${modalId} input:not([readonly])`);
        if (input) input.focus();
    }

    async generateRandomSessionName() {
        const nameInput = document.getElementById('session-name');
        if (!nameInput) return;

        try {
            const response = await fetch('/api/sessions/random-name');
            if (response.ok) {
                const data = await response.json();
                nameInput.value = data.name;
            }
        } catch (error) {
            console.log('Failed to generate random name:', error);
            // Silent fail - user can still type their own name
        }
    }

    hideModal(modalId) {
        document.getElementById(modalId).classList.add('hidden');
    }

    formatTime(isoString) {
        const date = new Date(isoString);
        const now = new Date();
        const diff = (now - date) / 1000; // seconds

        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return date.toLocaleDateString();
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async showDiscoverModal() {
        this.showModal('modal-discover');
        const container = document.getElementById('discover-list');
        container.innerHTML = '<p>Loading...</p>';

        try {
            const response = await fetch('/api/wezterm/panes');
            const panes = await response.json();

            if (panes.length === 0) {
                container.innerHTML = '<p>No WezTerm panes found.</p>';
                return;
            }

            // Get managed pane IDs
            const managedPaneIds = new Set(
                this.sessions
                    .filter(s => s.wezterm_pane_id !== undefined)
                    .map(s => s.wezterm_pane_id)
            );

            container.innerHTML = '';
            panes.forEach(p => {
                const paneId = p.pane_id;
                const isManaged = managedPaneIds.has(paneId);
                const item = document.createElement('div');
                item.className = `discover-item ${isManaged ? 'managed' : 'unmanaged'}`;

                const statusIcon = isManaged ? '✅' : '⚪';
                const statusText = isManaged ? 'Managed' : 'Not managed';

                item.innerHTML = `
                    <div class="discover-info">
                        <span class="discover-name">${statusIcon} Pane ${paneId}</span>
                        <span class="discover-status">${statusText}</span>
                    </div>
                    <div class="discover-actions">
                        ${isManaged ? '' : `<button class="btn btn-small btn-primary" data-pane="${paneId}">Import</button>`}
                    </div>
                `;

                // Add import button handler
                const importBtn = item.querySelector('button');
                if (importBtn) {
                    importBtn.addEventListener('click', async () => {
                        await this.importSession(paneId);
                    });
                }

                container.appendChild(item);
            });

        } catch (error) {
            console.error('Failed to discover sessions:', error);
            container.innerHTML = '<p class="error">Failed to discover sessions.</p>';
        }
    }

    async importSession(paneId) {
        try {
            const response = await fetch('/api/wezterm/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pane_id: paneId }),
            });

            if (!response.ok) {
                const error = await response.json();
                alert(error.detail || 'Failed to import session');
                return;
            }

            const session = await response.json();
            await this.loadSessions();
            await this.showDiscoverModal(); // Refresh the list
            this.selectSession(session);

        } catch (error) {
            console.error('Failed to import session:', error);
            alert('Failed to import session');
        }
    }

    async importAllSessions() {
        try {
            const response = await fetch('/api/wezterm/import-all', {
                method: 'POST',
            });

            if (!response.ok) {
                const error = await response.json();
                alert(error.detail || 'Failed to import sessions');
                return;
            }

            const result = await response.json();
            await this.loadSessions();
            this.hideModal('modal-discover');

            if (result.imported > 0) {
                alert(`Imported ${result.imported} session(s)`);
            } else {
                alert('No new sessions to import');
            }

        } catch (error) {
            console.error('Failed to import sessions:', error);
            alert('Failed to import sessions');
        }
    }

    async showHistoryModal() {
        this.showModal('modal-history');
        document.getElementById('history-sessions').classList.remove('hidden');
        document.getElementById('history-entries').classList.add('hidden');

        const container = document.getElementById('history-sessions');
        container.innerHTML = '<p>Loading...</p>';

        try {
            const response = await fetch('/api/history');
            const sessions = await response.json();

            if (sessions.length === 0) {
                container.innerHTML = '<p>No history available.</p>';
                return;
            }

            container.innerHTML = '';
            sessions.forEach(s => {
                const item = document.createElement('div');
                item.className = 'history-session-item';

                const lastTime = s.last_ts ? new Date(s.last_ts).toLocaleString() : 'Unknown';

                item.innerHTML = `
                    <div class="history-session-info">
                        <span class="history-session-id">${this.escapeHtml(s.id)}</span>
                        <span class="history-session-meta">${s.entry_count} entries · Last: ${lastTime}</span>
                    </div>
                `;

                item.addEventListener('click', () => this.showSessionHistory(s.id));
                container.appendChild(item);
            });

        } catch (error) {
            console.error('Failed to load history:', error);
            container.innerHTML = '<p class="error">Failed to load history.</p>';
        }
    }

    async showSessionHistory(sessionId) {
        document.getElementById('history-sessions').classList.add('hidden');
        document.getElementById('history-entries').classList.remove('hidden');
        document.getElementById('history-session-name').textContent = sessionId;

        const container = document.getElementById('history-content');
        container.innerHTML = '<p>Loading...</p>';

        try {
            const response = await fetch(`/api/history/${sessionId}?limit=100`);
            const entries = await response.json();

            if (entries.length === 0) {
                container.innerHTML = '<p>No entries.</p>';
                return;
            }

            container.innerHTML = '';
            entries.forEach(entry => {
                const div = document.createElement('div');
                div.className = `history-entry ${entry.type}`;

                const time = new Date(entry.ts).toLocaleTimeString();
                const typeLabel = {input: '▶', output: '◀', system: '⚙'}[entry.type] || '';

                div.innerHTML = `
                    <div class="history-entry-time">${typeLabel} ${time}</div>
                    <div>${this.escapeHtml(entry.content)}</div>
                `;

                container.appendChild(div);
            });

            // Scroll to bottom
            container.scrollTop = container.scrollHeight;

        } catch (error) {
            console.error('Failed to load session history:', error);
            container.innerHTML = '<p class="error">Failed to load history.</p>';
        }
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new CCAnywhere();
});
